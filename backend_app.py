from __future__ import annotations

import json
import logging
import mimetypes
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from PIL import Image

from catalog_processor.processor import OcrEngine, color_histogram_embedding, extract_codes_from_line, normalize_code

try:
    import cv2
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None


LOGGER = logging.getLogger("milana_backend")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass
class CatalogProduct:
    product_id: str
    product_code: str
    model_code: str
    price: str
    currency: str
    combined_text: str
    source_pdf: str
    page: int
    card_index: int
    image_path: Path | None
    image_url: str | None
    hist_vector: list[float] | None = None
    clip_vector: list[float] | None = None


@dataclass
class MatchCandidate:
    product: CatalogProduct
    score: float
    reasons: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)


class MediaUrlRequest(BaseModel):
    media_url: str
    user_message: str = ""
    language: str = "uz"
    top_k: int = Field(default=3, ge=1, le=10)


class InboundEventRequest(BaseModel):
    platform: str = "generic"
    user_id: str = ""
    text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    language: str = "uz"
    top_k: int = Field(default=3, ge=1, le=10)


class ClipEmbeddingEngine:
    def __init__(self, enabled: bool, model_name: str, batch_size: int = 16, eager_load: bool = False):
        self.enabled = enabled
        self.model_name = model_name
        self.batch_size = max(1, batch_size)
        self.eager_load = eager_load
        self.model = None
        self.available = False
        self.loaded = False
        self.error = ""

        if not enabled:
            self.error = "CLIP disabled by ENABLE_CLIP=0"
            return

        if eager_load:
            self.ensure_model_loaded()

    def ensure_model_loaded(self) -> bool:
        if not self.enabled:
            return False
        if self.model is not None:
            return True

        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(self.model_name)
            self.available = True
            self.loaded = True
            LOGGER.info("CLIP engine ready: %s", self.model_name)
            return True
        except Exception as exc:  # pragma: no cover - optional dependency/runtime
            self.error = str(exc)
            self.available = False
            self.loaded = False
            LOGGER.warning("CLIP engine unavailable: %s", exc)
            return False

    def encode_images(self, images: list[Image.Image]) -> list[list[float] | None]:
        if not images:
            return []
        if not self.ensure_model_loaded() or self.model is None:
            return [None] * len(images)

        try:
            rgb_images = [img.convert("RGB") for img in images]
            vectors = self.model.encode(
                rgb_images,
                normalize_embeddings=True,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover - runtime specific
            LOGGER.warning("CLIP encoding failed: %s", exc)
            return [None] * len(images)

        result: list[list[float] | None] = []
        for vector in vectors:
            result.append([float(value) for value in vector.tolist()])
        return result


class CatalogMatcherBackend:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.catalog_json_path = Path(
            os.getenv(
                "CATALOG_JSON_PATH",
                str(project_root / "outputs" / "catalog_processing" / "milana_products_latest.json"),
            )
        )
        self.catalog_images_dir = Path(
            os.getenv(
                "CATALOG_IMAGES_DIR",
                str(project_root / "outputs" / "catalog_processing" / "images" / "latest"),
            )
        )
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_KEY")
            or ""
        )
        self.supabase_table = os.getenv("SUPABASE_PRODUCTS_TABLE", "milana_products")
        self.max_media_mb = int(os.getenv("BACKEND_MAX_MEDIA_MB", "25"))

        self.enable_clip = env_flag("ENABLE_CLIP", default=True)
        self.clip_model_name = os.getenv("CLIP_MODEL", "clip-ViT-B-32")
        self.clip_batch_size = int(os.getenv("CLIP_BATCH_SIZE", "16"))
        self.clip_eager_load = env_flag("CLIP_EAGER_LOAD", default=False)
        self.clip_precompute_catalog = env_flag("CLIP_PRECOMPUTE_CATALOG", default=False)
        self.clip_rerank_pool = max(10, min(200, int(os.getenv("CLIP_RERANK_POOL", "80"))))
        self.clip_engine = ClipEmbeddingEngine(
            self.enable_clip,
            self.clip_model_name,
            self.clip_batch_size,
            eager_load=self.clip_eager_load,
        )

        self.weight_visual = float(os.getenv("WEIGHT_VISUAL", "0.72"))
        self.weight_code = float(os.getenv("WEIGHT_CODE", "0.22"))
        self.weight_text = float(os.getenv("WEIGHT_TEXT", "0.06"))
        self.weight_clip_within_visual = float(os.getenv("WEIGHT_CLIP_WITHIN_VISUAL", "0.82"))
        self.min_fusion_score = float(os.getenv("MATCH_MIN_FUSION_SCORE", "0.20"))

        self.products: list[CatalogProduct] = []
        self.code_index: dict[str, list[CatalogProduct]] = {}

    def refresh_catalog(self) -> int:
        rows: list[dict[str, Any]] = []

        if self.supabase_url and self.supabase_key:
            try:
                rows = self._load_rows_from_supabase()
                LOGGER.info("Loaded %s product rows from Supabase.", len(rows))
            except Exception as exc:
                LOGGER.warning("Supabase load failed, falling back to local JSON: %s", exc)

        if not rows:
            rows = self._load_rows_from_local_json()
            LOGGER.info("Loaded %s product rows from local JSON.", len(rows))

        products: list[CatalogProduct] = []
        clip_queue: list[tuple[int, Path]] = []

        for row in rows:
            product_code = normalize_code(str(row.get("product_code") or ""))
            model_code = normalize_code(str(row.get("model_code") or ""))
            product_id = self._product_id(row)
            image_path = self._resolve_local_image_path(row)

            product = CatalogProduct(
                product_id=product_id,
                product_code=product_code,
                model_code=model_code,
                price=str(row.get("price") or ""),
                currency=str(row.get("currency") or ""),
                combined_text=str(row.get("combined_text") or ""),
                source_pdf=str(row.get("source_pdf") or ""),
                page=int(row.get("page") or 0),
                card_index=int(row.get("card_index") or 0),
                image_path=image_path,
                image_url=row.get("image_url"),
            )

            if image_path and image_path.exists():
                try:
                    with Image.open(image_path) as image:
                        product.hist_vector = color_histogram_embedding(image)
                except Exception:
                    product.hist_vector = None

                if self.enable_clip and self.clip_precompute_catalog:
                    clip_queue.append((len(products), image_path))

            products.append(product)

        if self.enable_clip and self.clip_precompute_catalog and clip_queue:
            for start in range(0, len(clip_queue), self.clip_batch_size):
                chunk = clip_queue[start : start + self.clip_batch_size]
                chunk_images: list[Image.Image] = []
                chunk_indexes: list[int] = []

                for idx, path in chunk:
                    try:
                        with Image.open(path) as image:
                            chunk_images.append(image.convert("RGB"))
                            chunk_indexes.append(idx)
                    except Exception:
                        continue

                vectors = self.clip_engine.encode_images(chunk_images)
                for local_idx, vector in enumerate(vectors):
                    if vector is None:
                        continue
                    products[chunk_indexes[local_idx]].clip_vector = vector

        code_index: dict[str, list[CatalogProduct]] = {}
        for product in products:
            for key in [product.product_code, product.model_code]:
                if not key:
                    continue
                code_index.setdefault(key, []).append(product)

        self.products = products
        self.code_index = code_index
        return len(self.products)

    def _product_id(self, row: dict[str, Any]) -> str:
        return "::".join(
            [
                str(row.get("source_pdf") or ""),
                str(row.get("page") or 0),
                str(row.get("card_index") or 0),
                normalize_code(str(row.get("product_code") or "")),
            ]
        )

    def _resolve_local_image_path(self, row: dict[str, Any]) -> Path | None:
        raw_image_path = str(row.get("image_path") or "").strip()
        if raw_image_path:
            candidate = Path(raw_image_path)
            if candidate.exists():
                return candidate

            basename = Path(raw_image_path.replace("\\", "/")).name
            if basename:
                candidate = self.catalog_images_dir / basename
                if candidate.exists():
                    return candidate

        storage_path = str(row.get("image_storage_path") or "").strip()
        if storage_path:
            basename = Path(storage_path).name
            candidate = self.catalog_images_dir / basename
            if candidate.exists():
                return candidate

        return None

    def _load_rows_from_local_json(self) -> list[dict[str, Any]]:
        if not self.catalog_json_path.exists():
            raise RuntimeError(f"Catalog JSON not found: {self.catalog_json_path}")
        payload = json.loads(self.catalog_json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("Catalog JSON must contain a list of products.")
        return payload

    def _load_rows_from_supabase(self) -> list[dict[str, Any]]:
        fields = ",".join(
            [
                "source_pdf",
                "page",
                "card_index",
                "model_code",
                "product_code",
                "price",
                "currency",
                "combined_text",
                "image_url",
                "image_path",
                "image_storage_path",
            ]
        )
        url = f"{self.supabase_url}/rest/v1/{urllib.parse.quote(self.supabase_table)}?select={urllib.parse.quote(fields)}"
        request = urllib.request.Request(
            url,
            headers={
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def extract_codes(self, text: str) -> list[str]:
        found: list[str] = []
        for line in text.splitlines():
            for code in extract_codes_from_line(line):
                normalized = normalize_code(code)
                if normalized and normalized not in found:
                    found.append(normalized)
        return found

    def _ensure_clip_vectors_for_products(self, products: list[CatalogProduct]) -> None:
        if not self.enable_clip or not products:
            return

        pending: list[CatalogProduct] = [
            product
            for product in products
            if product.clip_vector is None and product.image_path and product.image_path.exists()
        ]
        if not pending:
            return

        for start in range(0, len(pending), self.clip_batch_size):
            chunk = pending[start : start + self.clip_batch_size]
            chunk_images: list[Image.Image] = []
            chunk_products: list[CatalogProduct] = []

            for product in chunk:
                try:
                    with Image.open(product.image_path) as image:
                        chunk_images.append(image.convert("RGB"))
                        chunk_products.append(product)
                except Exception:
                    continue

            if not chunk_images:
                continue

            vectors = self.clip_engine.encode_images(chunk_images)
            for index, vector in enumerate(vectors):
                if vector is None:
                    continue
                chunk_products[index].clip_vector = vector

    def find_matches(
        self,
        images: list[Image.Image],
        extracted_codes: list[str],
        ocr_text: str,
        user_message: str,
        top_k: int,
    ) -> list[MatchCandidate]:
        if not self.products:
            return []

        query_hist_vectors = [color_histogram_embedding(image) for image in images]
        query_clip_vectors = self.clip_engine.encode_images(images) if self.enable_clip else [None] * len(images)

        code_set = {normalize_code(code) for code in extracted_codes if code}
        text_blob = f"{ocr_text}\n{user_message}".upper()

        def build_candidate(product: CatalogProduct) -> MatchCandidate:
            visual_clip_score = max_similarity(query_clip_vectors, product.clip_vector)
            visual_hist_score = max_similarity(query_hist_vectors, product.hist_vector)
            visual_score = self._combined_visual_score(visual_clip_score, visual_hist_score)

            code_score = 0.0
            if product.product_code and product.product_code in code_set:
                code_score = 1.0
            elif product.model_code and product.model_code in code_set:
                code_score = 0.92

            text_score = 0.0
            if product.product_code and product.product_code in text_blob:
                text_score = 1.0
            elif product.model_code and product.model_code in text_blob:
                text_score = 0.9

            final_score = (
                self.weight_visual * visual_score
                + self.weight_code * code_score
                + self.weight_text * text_score
            )

            reasons: list[str] = []
            if code_score >= 1.0:
                reasons.append(f"exact_code:{product.product_code}")
            elif code_score > 0:
                reasons.append(f"exact_model:{product.model_code}")
            if text_score > 0:
                reasons.append("text_match")
            if visual_clip_score > 0:
                reasons.append("visual_clip")
            if visual_hist_score > 0:
                reasons.append("visual_hist")

            components = {
                "final": round(final_score, 6),
                "visual": round(visual_score, 6),
                "visual_clip": round(visual_clip_score, 6),
                "visual_hist": round(visual_hist_score, 6),
                "code": round(code_score, 6),
                "text": round(text_score, 6),
            }

            return MatchCandidate(
                product=product,
                score=final_score,
                reasons=reasons,
                components=components,
            )

        candidates: list[MatchCandidate] = [build_candidate(product) for product in self.products]

        if self.enable_clip:
            initial_ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
            rerank_slice = initial_ranked[: self.clip_rerank_pool]
            self._ensure_clip_vectors_for_products([item.product for item in rerank_slice])
            reranked = [build_candidate(item.product) for item in rerank_slice]
            by_id = {item.product.product_id: item for item in reranked}
            candidates = [by_id.get(item.product.product_id, item) for item in candidates]

        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        filtered = [item for item in ranked if item.score >= self.min_fusion_score]
        return (filtered or ranked)[:top_k]

    def _combined_visual_score(self, clip_score: float, hist_score: float) -> float:
        clip_weight = self.weight_clip_within_visual if self.enable_clip else 0.0
        hist_weight = 1.0 - clip_weight

        if clip_score <= 0 and hist_score <= 0:
            return 0.0

        if clip_weight <= 0:
            return hist_score

        if clip_score <= 0:
            return hist_score

        return clip_weight * clip_score + hist_weight * hist_score


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    arr_a = np.array(a, dtype="float64")
    arr_b = np.array(b, dtype="float64")
    denom = float(np.linalg.norm(arr_a) * np.linalg.norm(arr_b))
    if denom == 0:
        return 0.0
    return float(np.dot(arr_a, arr_b) / denom)


def max_similarity(query_vectors: list[list[float] | None], target_vector: list[float] | None) -> float:
    if not query_vectors or not target_vector:
        return 0.0
    best = 0.0
    for query in query_vectors:
        if not query:
            continue
        score = cosine_similarity(query, target_vector)
        if score > best:
            best = score
    return best


def extract_images_from_media(path: Path, mime_type: str | None = None, max_frames: int = 6) -> list[Image.Image]:
    mime = (mime_type or "").lower()
    suffix = path.suffix.lower()

    if mime.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        with Image.open(path) as image:
            return [image.convert("RGB")]

    if mime.startswith("video/") or suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for video processing but is not available.")

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError("Unable to open video file.")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total_frames <= 0:
            cap.release()
            raise RuntimeError("Video has no readable frames.")

        indices = np.linspace(0, max(total_frames - 1, 0), num=min(max_frames, total_frames), dtype=int)
        images: list[Image.Image] = []

        for frame_index in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
            ok, frame = cap.read()
            if not ok:
                continue
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            images.append(Image.fromarray(frame_rgb))

        cap.release()
        if not images:
            raise RuntimeError("No usable frames were extracted from video.")
        return images

    raise RuntimeError(f"Unsupported media type: {mime_type or suffix}")


def save_remote_media(media_url: str, max_mb: int) -> tuple[Path, str | None]:
    suffix = Path(urllib.parse.urlparse(media_url).path).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = Path(tmp.name)

    request = urllib.request.Request(
        media_url,
        headers={
            "User-Agent": "Mozilla/5.0 MilanaBackend/1.0",
        },
    )
    max_bytes = max_mb * 1024 * 1024

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type")
            downloaded = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise RuntimeError(f"Media exceeds max limit of {max_mb} MB")
                tmp.write(chunk)
    except urllib.error.URLError as exc:
        tmp.close()
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download media URL: {exc}") from exc

    tmp.close()
    return path, content_type


def format_match_for_response(candidate: MatchCandidate) -> dict[str, Any]:
    product = candidate.product
    return {
        "score": round(candidate.score, 4),
        "reasons": candidate.reasons,
        "components": candidate.components,
        "product_code": product.product_code,
        "model_code": product.model_code,
        "price": product.price,
        "currency": product.currency,
        "source_pdf": product.source_pdf,
        "page": product.page,
        "card_index": product.card_index,
        "description": product.combined_text,
        "image_url": product.image_url,
        "image_path": str(product.image_path) if product.image_path else None,
    }


def fallback_message(language: str, matches: list[dict[str, Any]]) -> str:
    if not matches:
        return (
            "Kechirasiz, mahsulotni aniq topa olmadim. "
            "Rasmni yaqinroq yoki mahsulot kodi bilan yuboring, men aniq topib beraman."
        )

    top = matches[0]
    code = top.get("product_code") or top.get("model_code") or "Noma'lum"
    price = top.get("price")
    currency = top.get("currency") or ""
    price_text = f"{price} {currency}".strip() if price else "narx bazada ko'rsatilmagan"

    if language.lower().startswith("ru"):
        return f"Похоже, это товар {code}. Цена: {price_text}. Если хотите, подберу альтернативы."
    if language.lower().startswith("en"):
        return f"This looks like product {code}. Price: {price_text}. I can also suggest alternatives."
    return f"Bu mahsulot {code} ga o'xshaydi. Narxi: {price_text}. Xohlasangiz, o'xshash variantlarni ham beraman."


def generate_llm_message(
    user_message: str,
    language: str,
    matches: list[dict[str, Any]],
) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback_message(language, matches)

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    system_prompt = (
        "You are a sales assistant for Milana catalog. "
        "Reply in the requested language, be concise, warm, and practical. "
        "Use only provided product matches. Never invent codes or prices."
    )
    user_prompt = {
        "language": language,
        "user_message": user_message,
        "matches": matches,
        "task": "Write a short customer-facing reply proposing the best match and optional alternatives.",
    }

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_prompt, ensure_ascii=False)}]},
        ],
        "temperature": 0.4,
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        LOGGER.warning("LLM request failed, returning fallback text: %s", exc)
        return fallback_message(language, matches)

    output_text = (result.get("output_text") or "").strip()
    if output_text:
        return output_text

    for output_item in result.get("output", []):
        for content_item in output_item.get("content", []):
            text_value = content_item.get("text")
            if text_value:
                return str(text_value).strip()

    return fallback_message(language, matches)


def guess_mime_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    guessed, _ = mimetypes.guess_type(filename)
    return guessed


PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND = CatalogMatcherBackend(PROJECT_ROOT)
BACKEND.refresh_catalog()
OCR = OcrEngine(lang=os.getenv("OCR_LANG", "eng+rus+uzb"), tesseract_cmd=os.getenv("TESSERACT_CMD"), logger=LOGGER)

app = FastAPI(title="Milana Media Matcher Backend", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "catalog_products": len(BACKEND.products),
        "ocr_available": OCR.available,
        "clip_enabled": BACKEND.enable_clip,
        "clip_available": BACKEND.clip_engine.available,
        "clip_loaded": BACKEND.clip_engine.loaded,
        "clip_model": BACKEND.clip_model_name,
        "fusion_weights": {
            "visual": BACKEND.weight_visual,
            "code": BACKEND.weight_code,
            "text": BACKEND.weight_text,
            "clip_within_visual": BACKEND.weight_clip_within_visual,
        },
    }


@app.post("/api/catalog/refresh")
def refresh_catalog() -> dict[str, Any]:
    count = BACKEND.refresh_catalog()
    return {"status": "ok", "catalog_products": count}


@app.post("/api/process-media")
async def process_media_upload(
    file: UploadFile = File(...),
    user_message: str = Form(default=""),
    language: str = Form(default="uz"),
    top_k: int = Form(default=3),
) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    suffix = Path(file.filename or "upload.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        media_path = Path(tmp.name)

    mime_type = file.content_type or guess_mime_from_filename(file.filename)
    try:
        return _process_media(media_path, mime_type, user_message, language, top_k)
    finally:
        media_path.unlink(missing_ok=True)


@app.post("/api/process-media-url")
def process_media_url(payload: MediaUrlRequest) -> dict[str, Any]:
    media_path, mime_type = save_remote_media(payload.media_url, BACKEND.max_media_mb)
    try:
        return _process_media(media_path, mime_type, payload.user_message, payload.language, payload.top_k)
    finally:
        media_path.unlink(missing_ok=True)


@app.post("/api/inbound-event")
def process_inbound_event(payload: InboundEventRequest) -> dict[str, Any]:
    if not payload.media_urls:
        raise HTTPException(status_code=400, detail="media_urls is required for inbound event processing.")

    last_error = "No media processed."
    for media_url in payload.media_urls:
        try:
            media_path, mime_type = save_remote_media(media_url, BACKEND.max_media_mb)
        except Exception as exc:
            last_error = str(exc)
            continue

        try:
            result = _process_media(media_path, mime_type, payload.text, payload.language, payload.top_k)
            return {
                "status": "ok",
                "platform": payload.platform,
                "user_id": payload.user_id,
                "media_url": media_url,
                "response_message": result["llm_reply"],
                "result": result,
            }
        finally:
            media_path.unlink(missing_ok=True)

    raise HTTPException(status_code=400, detail=last_error)


def _process_media(
    media_path: Path,
    mime_type: str | None,
    user_message: str,
    language: str,
    top_k: int,
) -> dict[str, Any]:
    if top_k < 1 or top_k > 10:
        raise HTTPException(status_code=400, detail="top_k must be between 1 and 10")

    try:
        images = extract_images_from_media(media_path, mime_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ocr_text_parts: list[str] = []
    for image in images:
        ocr_text = OCR.read(image)
        if ocr_text:
            ocr_text_parts.append(ocr_text)

    merged_ocr_text = "\n".join(ocr_text_parts)
    extracted_codes = BACKEND.extract_codes(merged_ocr_text)

    if user_message:
        for code in BACKEND.extract_codes(user_message):
            if code not in extracted_codes:
                extracted_codes.append(code)

    matches = BACKEND.find_matches(
        images=images,
        extracted_codes=extracted_codes,
        ocr_text=merged_ocr_text,
        user_message=user_message,
        top_k=top_k,
    )
    response_matches = [format_match_for_response(candidate) for candidate in matches]
    llm_reply = generate_llm_message(user_message, language, response_matches)

    return {
        "status": "ok",
        "media_type": mime_type,
        "frames_analyzed": len(images),
        "extracted_codes": extracted_codes,
        "matches": response_matches,
        "llm_reply": llm_reply,
        "debug": {
            "ocr_chars": len(merged_ocr_text),
            "catalog_products": len(BACKEND.products),
            "clip_available": BACKEND.clip_engine.available,
            "min_fusion_score": BACKEND.min_fusion_score,
        },
    }
