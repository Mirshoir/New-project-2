(function () {
  const CATALOGS = [
    { id: 1, sourcePdf: "01_Staple_Model_Catalog.pdf", title: "Staple Model Catalog" },
    { id: 2, sourcePdf: "02_Milana_Man_Premium_Collection.pdf", title: "Milana Man Premium Collection" },
    { id: 3, sourcePdf: "03_Kindergarten_Set.pdf", title: "Kindergarten Set" },
    { id: 4, sourcePdf: "04_Milana_Products_in_Stock.pdf", title: "Milana Products in Stock" }
  ];

  const config = window.MILANA_CONFIG || {};
  const params = new URLSearchParams(window.location.search);
  const catalogId = Number(params.get("id")) || 1;
  const catalog = CATALOGS.find((item) => item.id === catalogId) || CATALOGS[0];
  const state = {
    session: readSession(),
    products: [],
    filtered: [],
    editing: null
  };

  const loginPanel = document.getElementById("loginPanel");
  const loginForm = document.getElementById("loginForm");
  const loginMessage = document.getElementById("loginMessage");
  const workspace = document.getElementById("adminWorkspace");
  const logoutButton = document.getElementById("logoutButton");
  const titleEl = document.getElementById("catalogTitle");
  const numberEl = document.getElementById("catalogNumber");
  const gridEl = document.getElementById("productGrid");
  const countEl = document.getElementById("productCount");
  const searchEl = document.getElementById("productSearch");
  const statusEl = document.getElementById("status");
  const editModal = document.getElementById("editModal");
  const editForm = document.getElementById("editForm");
  const editMessage = document.getElementById("editMessage");

  titleEl.textContent = catalog.title;
  numberEl.textContent = "Catalog 0" + catalog.id;
  document.querySelectorAll("[data-catalog-link]").forEach((link) => {
    link.classList.toggle("active", Number(link.dataset.catalogLink) === catalog.id);
  });

  loginForm.addEventListener("submit", handleLogin);
  logoutButton.addEventListener("click", logout);
  searchEl.addEventListener("input", applySearch);
  gridEl.addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-id]");
    if (!button) {
      return;
    }
    openEditor(button.dataset.editId);
  });
  document.getElementById("closeEdit").addEventListener("click", closeEditor);
  document.getElementById("cancelEdit").addEventListener("click", closeEditor);
  editModal.addEventListener("click", (event) => {
    if (event.target === editModal) {
      closeEditor();
    }
  });
  editForm.addEventListener("submit", saveEditor);

  if (state.session) {
    restoreSession().catch((error) => {
      logout();
      loginMessage.textContent = error.message;
    });
  }

  async function handleLogin(event) {
    event.preventDefault();
    loginMessage.textContent = "Checking login...";

    try {
      requireSupabaseConfig();
      const email = document.getElementById("adminEmail").value.trim();
      const password = document.getElementById("adminPassword").value;
      const response = await fetch(`${baseUrl()}/auth/v1/token?grant_type=password`, {
        method: "POST",
        headers: {
          apikey: config.supabasePublishableKey,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ email, password })
      });

      if (!response.ok) {
        const details = await readResponseMessage(response);
        if (details.includes("invalid_credentials")) {
          throw new Error("Wrong email/password, or this user is not created in Supabase Auth yet.");
        }
        throw new Error(details || "Login failed.");
      }

      state.session = await response.json();
      saveSession(state.session);
      const admin = await checkAdmin();
      if (!admin) {
        throw new Error("This account is not allowed to edit Milana products.");
      }

      showWorkspace();
      await loadProducts();
    } catch (error) {
      logout();
      loginMessage.textContent = error.message;
    }
  }

  async function restoreSession() {
    const admin = await checkAdmin();
    if (!admin) {
      throw new Error("This account is not allowed to edit Milana products.");
    }

    showWorkspace();
    await loadProducts();
  }

  async function checkAdmin() {
    const response = await supabaseFetch("/rest/v1/rpc/is_milana_admin", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: "{}"
    });

    if (!response.ok) {
      const details = await readResponseMessage(response);
      throw new Error(details || "Admin security is not set up in Supabase yet.");
    }

    return response.json();
  }

  async function loadProducts() {
    showStatus("Loading products...");
    const localProducts = await readProductsFromLocalJson();
    const supabaseProducts = await readProductsFromSupabase().catch(() => []);
    const supabaseByKey = new Map(supabaseProducts.map((product) => [productKey(product), product]));
    state.products = localProducts.map((product) => {
      const supabaseProduct = supabaseByKey.get(productKey(product));
      if (!supabaseProduct) {
        return product;
      }

      return Object.assign({}, product, supabaseProduct, {
        local_only: false
      });
    });
    state.filtered = state.products.slice();
    hideStatus();
    renderProducts();
  }

  async function readProductsFromSupabase() {
    const source = encodeURIComponent(catalog.sourcePdf);
    const table = encodeURIComponent(config.table || "milana_products");
    const select = [
      "id",
      "source_pdf",
      "page",
      "card_index",
      "model_code",
      "product_code",
      "price",
      "currency",
      "image_url",
      "image_path",
      "image_storage_bucket",
      "image_storage_path"
    ].join(",");
    const response = await supabaseFetch(
      `/rest/v1/${table}?select=${select}&source_pdf=eq.${source}&order=page.asc,card_index.asc`
    );

    if (!response.ok) {
      throw new Error("Products could not be loaded from Supabase.");
    }

    const products = await response.json();
    if (!products.length) {
      throw new Error("Supabase returned no visible products.");
    }

    return products;
  }

  async function readProductsFromLocalJson() {
    const response = await fetch(config.localJson || "outputs/catalog_processing/milana_products_latest.json", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error("Products could not be loaded from Supabase or local JSON.");
    }

    const products = await response.json();
    return products
      .filter((item) => item && item.source_pdf === catalog.sourcePdf)
      .sort((a, b) => Number(a.page || 0) - Number(b.page || 0) || Number(a.card_index || 0) - Number(b.card_index || 0))
      .map((item) => Object.assign({}, item, {
        id: item.id || `${item.source_pdf}:${item.page}:${item.card_index}`,
        local_only: true
      }));
  }

  function productKey(product) {
    return [
      product.source_pdf || "",
      Number(product.page || 0),
      Number(product.card_index || 0)
    ].join(":");
  }

  function renderProducts() {
    countEl.textContent = state.filtered.length + (state.filtered.length === 1 ? " item" : " items");
    if (!state.filtered.length) {
      gridEl.innerHTML = '<div class="empty">No matching products.</div>';
      return;
    }

    gridEl.innerHTML = state.filtered.map((product) => {
      const model = escapeHtml(product.model_code || product.product_code || "Model");
      const code = escapeHtml(product.product_code || product.model_code || "");
      const image = escapeAttribute(resolveImageUrl(product));
      const price = escapeHtml(formatPrice(product.price, product.currency));
      return `
        <article class="product-card">
          <button class="edit-button" type="button" data-edit-id="${escapeAttribute(product.id)}">Edit</button>
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

  function openEditor(id) {
    const product = state.products.find((item) => String(item.id) === String(id));
    if (!product) {
      return;
    }

    state.editing = product;
    document.getElementById("editTitle").textContent = product.model_code || product.product_code || "Product";
    document.getElementById("editModel").value = product.model_code || "";
    document.getElementById("editCode").value = product.product_code || "";
    document.getElementById("editPrice").value = product.price || "";
    document.getElementById("editImageUrl").value = product.image_url || "";
    document.getElementById("editImageFile").value = "";
    editMessage.textContent = "";
    editModal.hidden = false;
  }

  function closeEditor() {
    editModal.hidden = true;
    state.editing = null;
  }

  async function saveEditor(event) {
    event.preventDefault();
    if (!state.editing) {
      return;
    }

    const saveButton = document.getElementById("saveEdit");
    saveButton.disabled = true;
    editMessage.textContent = "Saving...";

    try {
      const modelCode = cleanValue(document.getElementById("editModel").value);
      const productCode = cleanValue(document.getElementById("editCode").value);
      const price = Number(document.getElementById("editPrice").value);
      const imageUrlField = cleanValue(document.getElementById("editImageUrl").value);
      const file = document.getElementById("editImageFile").files[0];
      if (!Number.isFinite(price)) {
        throw new Error("Price must be a number.");
      }

      let imageUrl = imageUrlField;
      let imageStorageBucket = state.editing.image_storage_bucket || null;
      let imageStoragePath = state.editing.image_storage_path || null;
      if (file) {
        const uploaded = await uploadImage(file);
        imageUrl = uploaded.url;
        imageStorageBucket = uploaded.bucket;
        imageStoragePath = uploaded.path;
      }

      const payload = {
        model_code: modelCode || null,
        product_code: productCode || null,
        price,
        currency: state.editing.currency || "USD",
        image_url: imageUrl || null,
        image_storage_bucket: imageStorageBucket,
        image_storage_path: imageStoragePath
      };

      await saveOverride(payload);
      const updated = state.editing.local_only ? payload : await patchProduct(payload);
      Object.assign(state.editing, updated || payload);
      applySearch();
      editMessage.textContent = "Saved.";
      closeEditor();
    } catch (error) {
      editMessage.textContent = error.message;
    } finally {
      saveButton.disabled = false;
    }
  }

  async function uploadImage(file) {
    const bucket = config.imageBucket || "product-images";
    const prefix = (config.adminImagePrefix || "manual-edits").replace(/^\/+|\/+$/g, "");
    const safeName = file.name.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "product.jpg";
    const objectPath = `${prefix}/${Date.now()}-${Math.random().toString(36).slice(2, 8)}-${safeName}`;
    const response = await supabaseFetch(
      `/storage/v1/object/${encodeURIComponent(bucket)}/${pathEncode(objectPath)}`,
      {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "x-upsert": "true"
        },
        body: file
      }
    );

    if (!response.ok) {
      throw new Error("Picture upload failed.");
    }

    return {
      bucket,
      path: objectPath,
      url: `${baseUrl()}/storage/v1/object/public/${encodeURIComponent(bucket)}/${pathEncode(objectPath)}`
    };
  }

  async function saveOverride(payload) {
    const table = encodeURIComponent(config.overrideTable || "milana_product_overrides");
    const body = Object.assign({}, payload, {
      source_pdf: state.editing.source_pdf,
      page: state.editing.page,
      card_index: state.editing.card_index
    });
    const response = await supabaseFetch(
      `/rest/v1/${table}?on_conflict=source_pdf,page,card_index`,
      {
        method: "POST",
        headers: {
          Prefer: "resolution=merge-duplicates,return=minimal",
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body)
      }
    );

    if (!response.ok) {
      throw new Error("Manual override could not be saved.");
    }
  }

  async function patchProduct(payload) {
    const table = encodeURIComponent(config.table || "milana_products");
    const select = "id,source_pdf,page,card_index,model_code,product_code,price,currency,image_url,image_storage_bucket,image_storage_path";
    const response = await supabaseFetch(
      `/rest/v1/${table}?id=eq.${encodeURIComponent(state.editing.id)}&select=${select}`,
      {
        method: "PATCH",
        headers: {
          Prefer: "return=representation",
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      }
    );

    if (!response.ok) {
      throw new Error("Product row could not be updated.");
    }

    const rows = await response.json();
    return rows[0];
  }

  function applySearch() {
    const query = searchEl.value.trim().toLowerCase();
    state.filtered = query
      ? state.products.filter((item) => searchableText(item).includes(query))
      : state.products.slice();
    renderProducts();
  }

  function showWorkspace() {
    loginPanel.hidden = true;
    workspace.hidden = false;
    logoutButton.hidden = false;
  }

  function logout() {
    state.session = null;
    localStorage.removeItem("milana_admin_session");
    loginPanel.hidden = false;
    workspace.hidden = true;
    logoutButton.hidden = true;
  }

  function requireSupabaseConfig() {
    if (!config.supabaseUrl || !config.supabasePublishableKey) {
      throw new Error("Fill site-config.js with Supabase URL and publishable key first.");
    }
  }

  function supabaseFetch(path, options) {
    requireSupabaseConfig();
    const headers = Object.assign(
      {
        apikey: config.supabasePublishableKey,
        Authorization: `Bearer ${state.session ? state.session.access_token : config.supabasePublishableKey}`
      },
      (options && options.headers) || {}
    );
    return fetch(`${baseUrl()}${path}`, Object.assign({}, options, { headers }));
  }

  function baseUrl() {
    return String(config.supabaseUrl || "").replace(/\/+$/, "");
  }

  function readSession() {
    try {
      return JSON.parse(localStorage.getItem("milana_admin_session") || "null");
    } catch (_error) {
      return null;
    }
  }

  function saveSession(session) {
    localStorage.setItem("milana_admin_session", JSON.stringify(session));
  }

  function pathEncode(path) {
    return path.split("/").map(encodeURIComponent).join("/");
  }

  function cleanValue(value) {
    return String(value || "").trim();
  }

  function formatPrice(value, currency) {
    const number = Number(value);
    const clean = Number.isFinite(number) ? number.toFixed(2).replace(/\.00$/, "").replace(/0$/, "") : String(value || "");
    if (!clean) {
      return "";
    }
    return currency === "USD" || !currency ? "$" + clean : clean + " " + currency;
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

  function searchableText(item) {
    return [item.model_code, item.product_code, item.price, item.currency, item.source_pdf].join(" ").toLowerCase();
  }

  function showStatus(message) {
    statusEl.hidden = false;
    statusEl.textContent = message;
  }

  function hideStatus() {
    statusEl.hidden = true;
    statusEl.textContent = "";
  }

  function showError(error) {
    showStatus(error.message || "Something went wrong.");
  }

  async function readResponseMessage(response) {
    try {
      const data = await response.json();
      return [data.error_code, data.msg, data.message, data.error_description, data.error]
        .filter(Boolean)
        .join(": ");
    } catch (_error) {
      return response.statusText || "";
    }
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
