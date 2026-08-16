const inventoryEl = document.querySelector("#inventoryCount");
const problemInventoryEl = document.querySelector("#problemInventoryCount");
const uncheckedInventoryEl = document.querySelector("#uncheckedInventoryCount");
const deliveryProductsEl = document.querySelector("[data-delivery-products]");
const redeemForm = document.querySelector("#redeemForm");
const convertForm = document.querySelector("#convertForm");
const toastEl = document.querySelector("[data-site-notice]");
const codeInput = document.querySelector("#cardCode");
const codeCount = document.querySelector("[data-code-count]");
const progressCard = document.querySelector("[data-redeem-progress]");
const progressTitle = document.querySelector("[data-progress-title]");
const progressCount = document.querySelector("[data-progress-count]");
const progressBar = document.querySelector("[data-progress-bar]");
const progressDetail = document.querySelector("[data-progress-detail]");
const progressStages = Array.from(document.querySelectorAll("[data-progress-stage]"));
const MAX_CARD_CODES = 100;
let toastTimer;
let toastHideTimer;
let toastReturnFocus;
let progressTimer;
let portalNavigationPending = false;

function i18nText(text) {
  return window.CDKI18N?.text?.(text) || text;
}

function i18nKey(key, fallback) {
  return window.CDKI18N?.t?.(key, fallback) || i18nText(fallback);
}

function i18nFormat(key, fallback, ...args) {
  return window.CDKI18N?.format?.(key, fallback, ...args) || i18nText(fallback);
}

try {
  window.localStorage?.removeItem("cdk-local-library-v1");
} catch (_) {}

const prefersReducedPortalMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function startRouteLoading() {
  document.body.classList.add("is-route-loading");
}

function stopRouteLoading() {
  document.body.classList.remove("is-route-loading");
  document.documentElement.classList.remove("is-portal-leaving", "is-exception-leaving");
}

function markArrivalForTarget(url) {
  try {
    sessionStorage.setItem(url.pathname.startsWith("/admin") ? "admin-cross-navigation" : "portal-cross-navigation", "1");
  } catch (_) {}
}

function navigateWithPortalExit(value, options = {}) {
  if (portalNavigationPending) return;
  const target = new URL(String(value || "/"), window.location.href);
  if (target.origin !== window.location.origin) {
    window.location.assign(target.href);
    return;
  }
  portalNavigationPending = true;
  markArrivalForTarget(target);
  startRouteLoading();
  if (!prefersReducedPortalMotion) {
    document.documentElement.classList.add("is-portal-leaving");
    if (options.exception) document.documentElement.classList.add("is-exception-leaving");
  }
  const delay = prefersReducedPortalMotion ? 0 : Number(options.delay ?? (options.exception ? 760 : 140));
  window.setTimeout(() => window.location.assign(target.href), delay);
}

function requestErrorMessage(error, fallback) {
  if (error?.name === "AbortError") return "网络超时，请稍后重试";
  return error?.message || fallback;
}

function fetchWithTimeout(resource, init = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return fetch(resource, { ...init, signal: controller.signal }).finally(() => window.clearTimeout(timer));
}

function closeMessage() {
  if (!toastEl || toastEl.hidden || !toastEl.classList.contains("show")) return;
  clearTimeout(toastTimer);
  clearTimeout(toastHideTimer);
  const backdrop = document.querySelector("[data-site-notice-backdrop]");
  toastEl.classList.remove("show");
  backdrop?.classList.remove("show");
  const shouldRestoreFocus = toastEl.contains(document.activeElement);
  toastHideTimer = window.setTimeout(() => {
    toastEl.hidden = true;
    if (backdrop) backdrop.hidden = true;
    if (shouldRestoreFocus) toastReturnFocus?.focus?.({ preventScroll: true });
  }, 240);
}

function showMessage(text, type = "error", options = {}) {
  if (!toastEl) return;
  const normalizedType = type === "success" ? "success" : "error";
  const backdrop = document.querySelector("[data-site-notice-backdrop]");
  const message = toastEl.querySelector("[data-site-notice-message]");
  const title = toastEl.querySelector("[data-site-notice-title]");
  const kicker = toastEl.querySelector("[data-site-notice-kicker]");
  const mark = toastEl.querySelector("[data-site-notice-mark] span");
  const close = toastEl.querySelector("[data-site-notice-close]");
  const kind = String(options.kind || (normalizedType === "error" ? "general" : "success"));
  toastReturnFocus = document.activeElement;
  clearTimeout(toastTimer);
  clearTimeout(toastHideTimer);
  toastEl.hidden = false;
  if (backdrop) backdrop.hidden = false;
  toastEl.dataset.noticeKind = kind;
  if (message) message.textContent = i18nText(text);
  if (title) title.textContent = normalizedType === "success" ? i18nKey("notice_success_title", "生成成功") : i18nKey("notice_error_title", "操作未完成");
  if (kicker) kicker.textContent = normalizedType === "success" ? i18nKey("notice_success_kicker", "CDK READY") : i18nKey("notice_error_kicker", "ACTION BLOCKED");
  if (mark) mark.textContent = normalizedType === "success" ? "✓" : "!";
  const confirm = toastEl.querySelector("[data-site-notice-confirm]");
  if (confirm) confirm.textContent = i18nKey("notice_confirm", "知道了");
  toastEl.className = `site-notice ${normalizedType}`;
  toastEl.classList.toggle("exception", normalizedType === "error");
  void toastEl.offsetWidth;
  const reveal = () => {
    if (toastEl.hidden) return;
    backdrop?.classList.add("show");
    toastEl.classList.add("show");
    close?.focus({ preventScroll: true });
  };
  requestAnimationFrame(() => requestAnimationFrame(reveal));
  window.setTimeout(reveal, 40);
  toastTimer = window.setTimeout(closeMessage, 4200);
}

document.querySelectorAll("[data-site-notice-close], [data-site-notice-confirm]").forEach((button) => {
  button.addEventListener("click", closeMessage);
});
document.querySelector("[data-site-notice-backdrop]")?.addEventListener("click", closeMessage);
document.addEventListener("keydown", (event) => {
  if (!toastEl || toastEl.hidden) return;
  if (event.key === "Escape") {
    closeMessage();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(toastEl.querySelectorAll("button:not(:disabled), a[href], input:not(:disabled)"));
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

document.querySelectorAll("[data-site-flash]").forEach((flash) => {
  showMessage(flash.textContent.trim(), flash.dataset.messageType || "error");
  flash.remove();
});

function animateNumber(element, nextValue) {
  const currentValue = Number(element.dataset.value || element.textContent || 0);
  if (currentValue === nextValue) {
    element.dataset.value = String(nextValue);
    element.textContent = nextValue.toLocaleString("zh-CN");
    return;
  }
  const start = performance.now();
  element.dataset.value = String(nextValue);
  element.classList.remove("number-pop");
  void element.offsetWidth;
  element.classList.add("number-pop");
  element.addEventListener("animationend", () => element.classList.remove("number-pop"), { once: true });
  function frame(now) {
    const progress = Math.min((now - start) / 650, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = Math.round(currentValue + (nextValue - currentValue) * eased).toLocaleString("zh-CN");
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function productStockState(product) {
  const available = Number(product.available || 0);
  const threshold = Number(product.low_stock_threshold || 0);
  if (available <= 0) return { className: "stock-empty", label: "无库存" };
  if (available <= threshold) return { className: "stock-low", label: "库存告急" };
  return { className: "stock-ready", label: "库存充足" };
}

function updateDeliveryProductCard(card, product) {
  const state = productStockState(product);
  card.classList.remove("stock-empty", "stock-low", "stock-ready");
  card.classList.add(state.className);
  const stockLabel = card.querySelector("[data-product-stock-label]");
  stockLabel.textContent = state.label;
  window.CDKI18N?.apply?.(stockLabel);
  card.querySelector("[data-product-name]").textContent = String(product.name || "");
  card.querySelector("[data-product-sku]").textContent = String(product.sku || "");
  animateNumber(card.querySelector("[data-product-available]"), Number(product.available || 0));
  animateNumber(card.querySelector("[data-product-cards]"), Number(product.pending_cards || 0));
}

function createDeliveryProductCard(product) {
  const card = document.createElement("article");
  card.className = "product-stock-card";
  card.dataset.productId = String(product.id);
  card.innerHTML = `
    <header class="product-stock-header">
      <span class="stock-signal"><i aria-hidden="true"></i><b data-product-stock-label></b></span>
      <span class="product-listed-chip">已上架</span>
    </header>
    <div class="delivery-product-name">
      <strong data-product-name></strong>
      <small data-product-sku></small>
    </div>
    <div class="product-stock-metrics">
      <div class="delivery-metric">
        <strong data-product-available data-value="0">0</strong>
        <small>可用库存</small>
      </div>
      <div class="delivery-metric listed">
        <strong data-product-cards data-value="0">0</strong>
        <small>在售 CDK</small>
      </div>
    </div>`;
  updateDeliveryProductCard(card, product);
  window.CDKI18N?.apply?.(card);
  return card;
}

function syncDeliveryProducts(products) {
  if (!deliveryProductsEl || !Array.isArray(products)) return;
  const cardsById = new Map(
    Array.from(deliveryProductsEl.querySelectorAll("[data-product-id]")).map((card) => [card.dataset.productId, card]),
  );
  const productIds = new Set(products.map((product) => String(product.id)));
  cardsById.forEach((card, id) => {
    if (!productIds.has(id)) card.remove();
  });

  if (products.length === 0) {
    if (!deliveryProductsEl.querySelector(".delivery-products-empty")) {
      const empty = document.createElement("p");
      empty.className = "delivery-products-empty";
      empty.textContent = "暂无可提货商品";
      deliveryProductsEl.append(empty);
      window.CDKI18N?.apply?.(empty);
    }
    return;
  }

  deliveryProductsEl.querySelector(".delivery-products-empty")?.remove();
  products.forEach((product) => {
    const productId = String(product.id);
    const card = cardsById.get(productId) || createDeliveryProductCard(product);
    updateDeliveryProductCard(card, product);
    deliveryProductsEl.append(card);
  });
}

async function refreshInventory() {
  if (!inventoryEl && !problemInventoryEl && !uncheckedInventoryEl && !deliveryProductsEl) return;
  try {
    const response = await fetchWithTimeout("/api/inventory", { cache: "no-store" }, 10000);
    if (!response.ok) throw new Error("Inventory request failed");
    const data = await response.json();
    if (inventoryEl) animateNumber(inventoryEl, Number(data.normal ?? data.inventory ?? 0));
    if (problemInventoryEl) animateNumber(problemInventoryEl, Number(data.problem || 0));
    if (uncheckedInventoryEl) animateNumber(uncheckedInventoryEl, Number(data.unchecked || 0));
    syncDeliveryProducts(data.products);
  } catch {
    if (inventoryEl) inventoryEl.textContent = "--";
    if (problemInventoryEl) problemInventoryEl.textContent = "--";
    if (uncheckedInventoryEl) uncheckedInventoryEl.textContent = "--";
  }
}

function initializeFormatOptions(scope = document) {
  scope.querySelectorAll("[data-format-card]").forEach((option) => {
    const input = option.querySelector("input[type='radio']");
    const update = () => {
      const name = input?.name;
      if (!name) return;
      scope.querySelectorAll(`[data-format-card] input[name='${name}']`).forEach((candidate) => {
        candidate.closest("[data-format-card]")?.classList.toggle("selected", candidate.checked);
      });
    };
    option.addEventListener("click", () => {
      if (!input || input.disabled) return;
      input.checked = true;
      update();
    });
    input?.addEventListener("change", update);
    update();
  });
}

function setLoading(button, loading, label) {
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.querySelector("span")?.textContent || button.textContent;
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
  const text = button.querySelector("span");
  if (text) text.textContent = loading ? label : button.dataset.label;
}

function setRedeemProgress(step, total, title, detail, percent, stage = 0) {
  if (!progressCard) return;
  progressCard.hidden = false;
  progressCard.classList.add("visible");
  progressCard.dataset.stage = String(stage);
  if (progressTitle) progressTitle.textContent = title;
  if (progressCount) progressCount.textContent = `${Math.min(step, total)} / ${total}`;
  if (progressDetail) progressDetail.textContent = detail;
  if (progressBar) progressBar.style.width = `${Math.max(4, Math.min(100, percent))}%`;
  progressStages.forEach((item) => {
    const itemStage = Number(item.dataset.progressStage || 0);
    item.classList.toggle("done", itemStage < stage);
    item.classList.toggle("active", itemStage === stage);
  });
}

function startRedeemProgress(total) {
  clearInterval(progressTimer);
  const stages = [
    ["正在准备提货任务", "正在校验兑换码并排队", 12, 0],
    ["正在执行商品验活", "该批次会按商品测活策略筛选可交付账号", 38, 1],
    ["正在生成交付文件", "活号确认后生成一次性下载链接", 72, 2],
    ["正在收尾", "加密库存正在安全出库", 88, 3],
  ];
  let index = 0;
  setRedeemProgress(0, total, i18nText(stages[0][0]), i18nText(stages[0][1]), stages[0][2], stages[0][3]);
  progressTimer = window.setInterval(() => {
    index = Math.min(index + 1, stages.length - 1);
    const [title, detail, percent, stage] = stages[index];
    const shown = Math.min(total, Math.max(1, Math.round((Number(percent) / 100) * total)));
    setRedeemProgress(shown, total, i18nText(title), i18nText(detail), Number(percent), stage);
    if (index === stages.length - 1) clearInterval(progressTimer);
  }, total > 1 ? 520 : 680);
}

function finishRedeemProgress(total, ok) {
  clearInterval(progressTimer);
  setRedeemProgress(
    ok ? total : 0,
    total,
    ok ? i18nText("提货任务完成") : i18nText("提货任务未完成"),
    ok ? i18nText("下载链接已生成，请及时保存") : i18nText("请根据提示调整后重试"),
    ok ? 100 : 18,
    ok ? 3 : 0,
  );
  progressCard?.classList.toggle("failed", !ok);
}

async function jsonResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (response.redirected) throw new Error("登录状态已失效，即将重新进入");
  if ([401, 403].includes(response.status) || contentType.includes("text/html")) {
    throw new Error("服务器返回异常，请刷新页面后重试");
  }
  if (!contentType.includes("application/json")) throw new Error("服务器返回异常");
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || "请求失败");
  return data;
}

function safeDownloadUrl(value) {
  const url = new URL(String(value || ""), window.location.origin);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error("下载链接无效");
  return url.href;
}

function extractCardCodes(rawValue) {
  const value = String(rawValue || "");
  const matches = [];
  const seen = new Set();
  const pattern = /(?:^|[^0-9a-f])([0-9a-f]{32})(?![0-9a-f])/gi;
  let match;
  while ((match = pattern.exec(value)) !== null) {
    const code = match[1].toLowerCase();
    if (!seen.has(code)) {
      seen.add(code);
      matches.push(code);
      if (matches.length > MAX_CARD_CODES) break;
    }
  }
  return matches;
}

function setCodeInput(codes) {
  if (!codeInput) return;
  codeInput.value = extractCardCodes(codes.join("\n")).join("\n");
  updateCodeCount();
  codeInput.dispatchEvent(new Event("change", { bubbles: true }));
}

function updateCodeCount() {
  if (!codeInput || !codeCount) return;
  const count = extractCardCodes(codeInput.value).length;
  codeCount.textContent = String(count);
  codeCount.setAttribute("aria-label", i18nFormat("code_count", `识别到 ${count} 个有效兑换码`, count));
  codeCount.classList.toggle("has-codes", count > 0);
}

if (codeInput) {
  codeInput.addEventListener("input", updateCodeCount);
  updateCodeCount();
}

redeemForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const endpoint = redeemForm.dataset.endpoint || "/api/redeem";
  const isLookup = endpoint === "/api/lookup";
  const codes = extractCardCodes(codeInput?.value);
  if (!codes.length) {
    showMessage("未识别到有效的 32 位 CDK");
    codeInput?.focus({ preventScroll: true });
    return;
  }
  if (isLookup && codes.length !== 1) {
    showMessage("每次只能查找一个已兑换的 CDK");
    codeInput?.focus({ preventScroll: true });
    return;
  }
  if (!isLookup && codes.length > MAX_CARD_CODES) {
    showMessage(i18nFormat("max_redeem", `单次最多兑换 ${MAX_CARD_CODES} 个 CDK，请分批处理`, MAX_CARD_CODES));
    codeInput?.focus({ preventScroll: true });
    return;
  }
  setCodeInput(codes);
  const button = redeemForm.querySelector("button[type='submit']");
  setLoading(button, true, isLookup ? i18nText("正在查找") : i18nText("正在生成"));
  if (!isLookup) startRedeemProgress(codes.length);
  try {
    const timeout = isLookup ? 45000 : Math.min(360000, 60000 + codes.length * 3000);
    const response = await fetchWithTimeout(endpoint, { method: "POST", body: new FormData(redeemForm) }, timeout);
    const data = await jsonResponse(response);
    const result = document.querySelector("#deliveryResult");
    document.querySelector("#deliveryFilename").textContent = data.filename;
    const link = document.querySelector("#deliveryLink");
    link.href = safeDownloadUrl(data.download_url);
    link.setAttribute("download", data.filename);
    result.hidden = false;
    requestAnimationFrame(() => result.classList.add("visible"));
    if (!isLookup) finishRedeemProgress(codes.length, true);
    showMessage(isLookup ? "原文件已找回" : "交付链接已生成", "success");
    refreshInventory();
  } catch (error) {
    if (!isLookup) finishRedeemProgress(codes.length, false);
    showMessage(requestErrorMessage(error, isLookup ? "查找失败" : "兑换失败"), "error", {
      kind: error?.name === "AbortError" ? "network" : "liveness",
    });
  } finally {
    setLoading(button, false, "");
  }
});

const convertFiles = document.querySelector("#convertFiles");
function updateConvertFileSummary() {
  if (!convertFiles) return;
  const files = Array.from(convertFiles.files || []);
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const summary = document.querySelector("#convertFileSummary");
  if (summary) summary.textContent = files.length
    ? i18nFormat("uploaded_files", `${files.length} 个文件 · ${(total / 1024 / 1024).toFixed(2)} MB`, files.length, `${(total / 1024 / 1024).toFixed(2)} MB`)
    : i18nText("支持 JSON、CPA、SUB、SUB2、ZIP");
  document.querySelector("[data-convert-drop]")?.classList.toggle("has-files", files.length > 0);
}

convertFiles?.addEventListener("change", updateConvertFileSummary);

const convertDrop = document.querySelector("[data-convert-drop]");
if (convertDrop && convertFiles) {
  ["dragenter", "dragover"].forEach((eventName) => {
    convertDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      convertDrop.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    convertDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      convertDrop.classList.remove("is-dragging");
    });
  });
  convertDrop.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer?.files || []).filter((file) => /\.(json|cpa|sub|sub2|zip)$/i.test(file.name));
    if (!files.length) {
      showMessage("请选择 JSON、CPA、SUB、SUB2 或 ZIP 文件");
      return;
    }
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    convertFiles.files = transfer.files;
    updateConvertFileSummary();
  });
}

convertForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = convertForm.querySelector("button[type='submit']");
  setLoading(button, true, i18nText("正在转换"));
  try {
    const response = await fetchWithTimeout("/api/convert", { method: "POST", body: new FormData(convertForm) }, 90000);
    const data = await jsonResponse(response);
    const result = document.querySelector("#convertResult");
    document.querySelector("#convertResultTitle").textContent = `${data.source_format} → ${data.target_format}`;
    document.querySelector("#convertResultMeta").textContent = window.CDKI18N?.language === "en"
      ? `${data.account_count} account${Number(data.account_count) === 1 ? "" : "s"} · ${data.filename}`
      : `${data.account_count} 个账号 · ${data.filename}`;
    const link = document.querySelector("#convertDownloadLink");
    link.href = safeDownloadUrl(data.download_url);
    link.setAttribute("download", data.filename);
    result.hidden = false;
    requestAnimationFrame(() => result.classList.add("visible"));
    showMessage("格式转换完成", "success");
  } catch (error) {
    showMessage(requestErrorMessage(error, "转换失败"), "error", {
      kind: error?.name === "AbortError" ? "network" : "general",
    });
  } finally {
    setLoading(button, false, "");
  }
});

initializeFormatOptions();
refreshInventory();
if (inventoryEl || deliveryProductsEl) window.setInterval(refreshInventory, 15000);

const passwordToggle = document.querySelector("[data-password-toggle]");
passwordToggle?.addEventListener("click", () => {
  const password = document.querySelector("#password");
  if (!password) return;
  const willShow = password.type === "password";
  password.type = willShow ? "text" : "password";
  passwordToggle.textContent = willShow ? i18nText("隐藏") : i18nText("显示");
  passwordToggle.setAttribute("aria-label", willShow ? i18nText("隐藏密码") : i18nText("显示密码"));
  passwordToggle.setAttribute("aria-pressed", String(willShow));
  password.focus({ preventScroll: true });
});

const loginForm = document.querySelector("[data-login-form]");
const loginSubmit = loginForm?.querySelector("[data-login-submit]");
loginForm?.addEventListener("submit", (event) => {
  if (loginForm.dataset.submitting === "true") {
    event.preventDefault();
    return;
  }
  loginForm.dataset.submitting = "true";
  loginForm.setAttribute("aria-busy", "true");
  markArrivalForTarget(new URL("/admin", window.location.href));
  startRouteLoading();
  if (!prefersReducedPortalMotion) document.documentElement.classList.add("is-portal-leaving");
  if (loginSubmit) {
    loginSubmit.disabled = true;
    const label = loginSubmit.querySelector("span");
    if (label) label.textContent = i18nText("正在验证");
  }
});

window.addEventListener("pageshow", () => {
  stopRouteLoading();
  if (!loginForm) return;
  delete loginForm.dataset.submitting;
  loginForm.removeAttribute("aria-busy");
  if (loginSubmit) {
    loginSubmit.disabled = false;
    const label = loginSubmit.querySelector("span");
    if (label) label.textContent = i18nText("进入管理控制台");
  }
});

const portalPaths = new Set(["/", "/convert", "/lookup", "/admin/login"]);
const hasNativeCrossDocumentTransition =
  typeof CSS !== "undefined" &&
  CSS.supports?.("view-transition-name: root") &&
  "onpageswap" in window;
let portalEntryTimer = 0;

function finishPortalEntry() {
  stopRouteLoading();
  portalNavigationPending = false;
  if (hasNativeCrossDocumentTransition || prefersReducedPortalMotion) return;

  window.clearTimeout(portalEntryTimer);
  document.documentElement.classList.remove("portal-fallback-enter");
  document.documentElement.classList.add("portal-fallback-enter");
  portalEntryTimer = window.setTimeout(
    () => document.documentElement.classList.remove("portal-fallback-enter"),
    320,
  );
}

window.addEventListener("pageshow", finishPortalEntry);
if (document.readyState === "complete") finishPortalEntry();

document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest("a[href]");
  if (!link || link.hasAttribute("download")) return;
  if (link.target && link.target.toLowerCase() !== "_self") return;

  const target = new URL(link.href, window.location.href);
  if (target.origin !== window.location.origin) return;
  if (!portalPaths.has(window.location.pathname) || !portalPaths.has(target.pathname)) return;
  if (target.pathname === window.location.pathname && target.search === window.location.search) return;
  if (portalNavigationPending) return;

  if (hasNativeCrossDocumentTransition) {
    markArrivalForTarget(target);
    portalNavigationPending = true;
    return;
  }

  event.preventDefault();
  navigateWithPortalExit(target.href);
});
