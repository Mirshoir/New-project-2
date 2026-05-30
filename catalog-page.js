(function () {
  const CATALOGS = [
    {
      id: 1,
      sourcePdf: "01_Staple_Model_Catalog.pdf",
      titles: {
        en: "Staple Model Catalog",
        ru: "Каталог моделей из Штапеля",
        uz: "Shtapel modellari katalogi"
      }
    },
    {
      id: 2,
      sourcePdf: "02_Milana_Man_Premium_Collection.pdf",
      titles: {
        en: "Milana Man Premium Collection",
        ru: "Milana Man Premium Collection",
        uz: "Milana Man Premium Collection"
      }
    },
    {
      id: 3,
      sourcePdf: "03_Kindergarten_Set.pdf",
      titles: {
        en: "Kindergarten Set",
        ru: "Комплект для Садика",
        uz: "Bog'cha uchun komplekt"
      }
    },
    {
      id: 4,
      sourcePdf: "04_Milana_Products_in_Stock.pdf",
      titles: {
        en: "Milana Products in Stock",
        ru: "Милана наличие товаров",
        uz: "Milana mavjud mahsulotlar"
      }
    }
  ];

  const params = new URLSearchParams(window.location.search);
  const catalogId = Number(params.get("id")) || 1;
  const catalog = CATALOGS.find((item) => item.id === catalogId) || CATALOGS[0];
  const lang = localStorage.getItem("mp_lang") || document.documentElement.lang || "uz";
  const config = window.MILANA_CONFIG || {};
  const state = {
    products: [],
    filtered: []
  };

  const titleEl = document.getElementById("catalogTitle");
  const numberEl = document.getElementById("catalogNumber");
  const gridEl = document.getElementById("productGrid");
  const countEl = document.getElementById("productCount");
  const searchEl = document.getElementById("productSearch");
  const statusEl = document.getElementById("status");
  const productModal = document.getElementById("productModal");
  const productModalCard = document.getElementById("productModalCard");
  const closeProductModal = document.getElementById("closeProductModal");
  const adminShortcut = document.getElementById("adminShortcut");

  titleEl.textContent = catalog.titles[lang] || catalog.titles.en;
  numberEl.textContent = "Catalog 0" + catalog.id;
  document.title = titleEl.textContent + " | Milana Premium";
  if (adminShortcut) {
    adminShortcut.href = "admin.html?id=" + catalog.id;
  }
  document.querySelectorAll("[data-catalog-link]").forEach((link) => {
    link.classList.toggle("active", Number(link.dataset.catalogLink) === catalog.id);
  });

  searchEl.addEventListener("input", () => {
    const query = searchEl.value.trim().toLowerCase();
    state.filtered = query
      ? state.products.filter((item) => searchableText(item).includes(query))
      : state.products.slice();
    renderProducts();
  });

  gridEl.addEventListener("click", (event) => {
    const card = event.target instanceof Element ? event.target.closest("[data-product-index]") : null;
    if (!card) {
      return;
    }

    openProductModal(Number(card.dataset.productIndex));
  });

  gridEl.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }

    const card = event.target instanceof Element ? event.target.closest("[data-product-index]") : null;
    if (!card) {
      return;
    }

    event.preventDefault();
    openProductModal(Number(card.dataset.productIndex));
  });

  closeProductModal.addEventListener("click", closeProductModalView);
  productModal.addEventListener("click", (event) => {
    if (event.target === productModal) {
      closeProductModalView();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !productModal.hidden) {
      closeProductModalView();
    }
  });

  loadProducts().catch((error) => {
    console.error(error);
    showStatus("Products could not be loaded. Check Supabase config or run the local processor again.");
    countEl.textContent = "0 items";
  });

  async function loadProducts() {
    showStatus("Loading products...");
    const products = await readFromSupabase().catch(() => readFromLocalJson());
    state.products = products
      .filter((item) => item && item.source_pdf === catalog.sourcePdf)
      .sort((a, b) => Number(a.page || 0) - Number(b.page || 0) || Number(a.card_index || 0) - Number(b.card_index || 0));
    state.filtered = state.products.slice();

    if (!state.products.length) {
      showStatus("No products found for this catalog yet.");
    } else {
      hideStatus();
    }

    renderProducts();
  }

  async function readFromSupabase() {
    if (!config.supabaseUrl || !config.supabasePublishableKey) {
      throw new Error("Supabase browser config is not set.");
    }

    const baseUrl = String(config.supabaseUrl).replace(/\/+$/, "");
    const table = encodeURIComponent(config.table || "milana_products");
    const source = encodeURIComponent(catalog.sourcePdf);
    const select = [
      "source_pdf",
      "page",
      "card_index",
      "model_code",
      "product_code",
      "price",
      "currency",
      "image_url",
      "image_storage_path",
      "extraction_status"
    ].join(",");
    const url = `${baseUrl}/rest/v1/${table}?select=${select}&source_pdf=eq.${source}&order=page.asc,card_index.asc`;
    const response = await fetch(url, {
      headers: {
        apikey: config.supabasePublishableKey,
        Authorization: `Bearer ${config.supabasePublishableKey}`
      }
    });

    if (!response.ok) {
      throw new Error("Supabase returned " + response.status);
    }

    const products = await response.json();
    if (!products.length) {
      throw new Error("Supabase returned no visible products.");
    }

    return products;
  }

  async function readFromLocalJson() {
    const response = await fetch(config.localJson || "outputs/catalog_processing/milana_products_latest.json", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error("Local JSON returned " + response.status);
    }

    return response.json();
  }

  function renderProducts() {
    countEl.textContent = state.filtered.length + (state.filtered.length === 1 ? " item" : " items");

    if (!state.filtered.length) {
      gridEl.innerHTML = '<div class="empty">No matching products.</div>';
      return;
    }

    gridEl.innerHTML = state.filtered.map((product, index) => {
      const model = escapeHtml(product.model_code || product.product_code || "Model");
      const code = escapeHtml(product.product_code || product.model_code || "");
      const image = escapeAttribute(resolveImageUrl(product));
      const price = escapeHtml(formatPrice(product.price, product.currency));
      return `
        <article class="product-card" data-product-index="${index}" role="button" tabindex="0">
          <div class="product-image">
            <img src="${image}" alt="${model}" loading="lazy">
          </div>
          <div class="product-info">
            <div class="model-row">
              <h2 class="model">${model}</h2>
              <p class="price">${price}</p>
            </div>
            <p class="code">Code ${code}</p>
          </div>
        </article>
      `;
    }).join("");
  }

  function openProductModal(index) {
    const product = state.filtered[index];
    if (!product) {
      return;
    }

    const model = product.model_code || product.product_code || "Model";
    const code = product.product_code || product.model_code || "";
    const image = resolveImageUrl(product);
    const price = formatPrice(product.price, product.currency);

    productModalCard.innerHTML = `
      <div class="modal-image">
        <img src="${escapeAttribute(image)}" alt="${escapeAttribute(model)}">
      </div>
      <div class="modal-info">
        <p class="eyebrow">Product</p>
        <h2 class="model">${escapeHtml(model)}</h2>
        <p class="code">Code ${escapeHtml(code)}</p>
        <p class="price">${escapeHtml(price)}</p>
      </div>
    `;
    productModal.hidden = false;
    document.body.style.overflow = "hidden";
    closeProductModal.focus();
  }

  function closeProductModalView() {
    productModal.hidden = true;
    productModalCard.innerHTML = "";
    document.body.style.overflow = "";
  }

  function resolveImageUrl(product) {
    if (product.image_url) {
      return product.image_url;
    }

    const rawPath = product.image_path || "";
    const normalized = String(rawPath).replace(/\\/g, "/");
    const marker = "/outputs/catalog_processing/";
    const markerIndex = normalized.indexOf(marker);
    if (markerIndex >= 0) {
      return "outputs/catalog_processing/" + normalized.slice(markerIndex + marker.length);
    }

    if (normalized.startsWith("outputs/")) {
      return normalized;
    }

    return "covers/milana-products-in-stock-en.png";
  }

  function formatPrice(value, currency) {
    const number = Number(value);
    const clean = Number.isFinite(number) ? number.toFixed(2).replace(/\.00$/, "").replace(/0$/, "") : String(value || "");
    if (!clean) {
      return "";
    }
    return currency === "USD" || !currency ? "$" + clean : clean + " " + currency;
  }

  function searchableText(item) {
    return [
      item.model_code,
      item.product_code,
      item.price,
      item.currency,
      item.source_pdf
    ].join(" ").toLowerCase();
  }

  function showStatus(message) {
    statusEl.hidden = false;
    statusEl.textContent = message;
  }

  function hideStatus() {
    statusEl.hidden = true;
    statusEl.textContent = "";
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }
})();
