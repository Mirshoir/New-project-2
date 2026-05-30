# Milana Premium Catalog Website

GitHub Pages ready static catalog site plus a local Google Drive PDF-to-Supabase processor.

## Website

- The landing page has 4 catalog cards in `index.html`.
- Each card opens `catalog.html?id=1`, `catalog.html?id=2`, `catalog.html?id=3`, or `catalog.html?id=4`.
- Product pages show one box per product with image, model, code, and price.
- The browser reads products from Supabase when `site-config.js` has your Project URL and publishable key.
- On your local computer it can fall back to `outputs/catalog_processing/milana_products_latest.json`.
- Thumbnails are stored in `covers/`.

For the live website, edit `site-config.js`:

```js
window.MILANA_CONFIG = {
  supabaseUrl: "https://YOUR_PROJECT_REF.supabase.co",
  supabasePublishableKey: "YOUR_SB_PUBLISHABLE_KEY",
  table: "milana_products",
  overrideTable: "milana_product_overrides",
  imageBucket: "product-images",
  adminImagePrefix: "manual-edits",
  localJson: "outputs/catalog_processing/milana_products_latest.json"
};
```

Use the publishable key only. Do not put the secret/service-role key in `site-config.js`.

Admin editing is available at:

```text
admin.html
```

Before using it:

1. In Supabase Auth, create your own email/password user.
2. In `supabase_schema.sql`, replace `YOUR_ADMIN_EMAIL@example.com` with that email.
3. Run `supabase_schema.sql` in Supabase SQL editor.

The public catalog stays read-only. Only the email listed in `milana_admins` can update rows, upload product images, and save manual overrides. Manual overrides are applied by the daily processor, so your edits survive the next PDF refresh.

Launch locally:

```powershell
python -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Daily Supabase Processor

Pipeline:

1. Read the 4 Google Drive PDF links from `index.html`.
2. Download the current PDF files into `outputs/catalog_processing/downloaded_pdfs/latest/`.
3. Compare SHA-256 hashes with the previous run.
4. Rebuild fresh when a PDF changed, or every day when `-Force` is used.
5. Save product card images to `outputs/catalog_processing/images/`.
6. Rows are exported only when both product code and price are detected.
7. OCR extracts visible prefixes such as `V-`, `TJ-`, or `SJ-` when Tesseract OCR is installed.
8. High-quality product images are uploaded to Supabase Storage.
9. Product rows are refreshed in Supabase by deleting old processor rows and inserting the latest rows.
10. Visual fingerprints and embedding vectors are saved locally for audit.

Without Tesseract, the processor still uses the PDF text layer. In that mode many rows will have numeric codes and `extraction_status` values such as `prefix_needs_ocr`, meaning the visible image prefix is not available from the PDF text layer.

## Supabase Setup

1. In Supabase SQL editor, run:

```text
supabase_schema.sql
```

2. Set environment variables in PowerShell. Use your real project URL and secret/service-role key:

```powershell
setx SUPABASE_URL "https://YOUR_PROJECT_REF.supabase.co"
setx SUPABASE_SERVICE_ROLE_KEY "YOUR_SUPABASE_SECRET_OR_SERVICE_ROLE_KEY"
setx SUPABASE_PRODUCTS_TABLE "milana_products"
setx SUPABASE_OVERRIDES_TABLE "milana_product_overrides"
setx SUPABASE_IMAGE_BUCKET "product-images"
setx SUPABASE_IMAGE_PREFIX "milana/latest"
```

Open a new PowerShell window after using `setx`.

Quick Supabase refresh:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -Force
```

Check/download the Google Drive PDFs without refreshing Supabase:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -DownloadOnly
```

Known layout run, for example 2 rows by 2 columns per page:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -Grid 2x2
```

If your Tesseract install only has English language data:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -OcrLang eng
```

With optional CLIP ML embeddings:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -EnableMlEmbeddings
```

Install the daily Windows scheduled task, for example every day at 07:00. `-Force` refreshes Supabase fresh each day and clears old generated local images/thumbnails/embeddings:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_daily_excel_update.ps1 -At 07:00 -Force
```

After that, update the PDFs in Google Drive using the same file links. The scheduled task will check those links every day and refresh Supabase.

Optional Excel export for checking:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_processor.ps1 -Destination excel -Force
```

Manual setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m catalog_processor --source website --destination supabase --website-index index.html --output outputs/catalog_processing --force
```

For OCR, install Tesseract OCR and make sure `tesseract.exe` is on PATH. If it is not on PATH, pass:

```powershell
.\.venv\Scripts\python.exe -m catalog_processor --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Optional ML embeddings use `requirements-ml.txt`; the first run may download the CLIP model.
