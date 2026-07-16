const inventoryEl = document.querySelector("#inventoryCount");
const problemInventoryEl = document.querySelector("#problemInventoryCount");
const uncheckedInventoryEl = document.querySelector("#uncheckedInventoryCount");
const redeemForm = document.querySelector("#redeemForm");
const convertForm = document.querySelector("#convertForm");
const toastEl = document.querySelector("#redeemToast, #convertToast, #lookupToast");
const codeInput = document.querySelector("#cardCode");
const codeCount = document.querySelector("[data-code-count]");
const MAX_CARD_CODES = 100;
let toastTimer;
let expiryTimer;

try {
  window.localStorage?.removeItem("cdk-local-library-v1");
} catch (_) {}

function showMessage(text, type = "error") {
  if (!toastEl) return;
  clearTimeout(toastTimer);
  toastEl.hidden = false;
  toastEl.textContent = text;
  toastEl.className = `toast show ${type}`;
  toastTimer = window.setTimeout(() => {
    toastEl.classList.remove("show");
    window.setTimeout(() => { toastEl.hidden = true; }, 180);
  }, 3200);
}

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

async function refreshInventory() {
  if (!inventoryEl && !problemInventoryEl && !uncheckedInventoryEl) return;
  try {
    const response = await fetch("/api/inventory", { cache: "no-store" });
    const data = await response.json();
    if (inventoryEl) animateNumber(inventoryEl, Number(data.normal ?? data.inventory ?? 0));
    if (problemInventoryEl) animateNumber(problemInventoryEl, Number(data.problem || 0));
    if (uncheckedInventoryEl) animateNumber(uncheckedInventoryEl, Number(data.unchecked || 0));
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

function startExpiryCountdown(element, expiresAt) {
  clearInterval(expiryTimer);
  const expiry = new Date(expiresAt).getTime();
  const update = () => {
    const remaining = Math.max(0, expiry - Date.now());
    const totalSeconds = Math.floor(remaining / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = Math.floor((remaining % 60000) / 1000);
    const countdown = hours > 0
      ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    element.textContent = remaining > 0
      ? `链接将在 ${countdown} 后失效`
      : "链接已失效，可重新提交生成";
    element.closest(".expiry-line")?.classList.toggle("expired", remaining === 0);
    if (remaining === 0) clearInterval(expiryTimer);
  };
  update();
  expiryTimer = window.setInterval(update, 1000);
}

function setLoading(button, loading, label) {
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.querySelector("span")?.textContent || button.textContent;
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
  const text = button.querySelector("span");
  if (text) text.textContent = loading ? label : button.dataset.label;
}

async function jsonResponse(response) {
  const contentType = response.headers.get("content-type") || "";
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
  codeCount.setAttribute("aria-label", `识别到 ${count} 个有效兑换码`);
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
    showMessage(`单次最多兑换 ${MAX_CARD_CODES} 个 CDK，请分批处理`);
    codeInput?.focus({ preventScroll: true });
    return;
  }
  setCodeInput(codes);
  const button = redeemForm.querySelector("button[type='submit']");
  setLoading(button, true, isLookup ? "正在查找" : "正在生成");
  try {
    const response = await fetch(endpoint, { method: "POST", body: new FormData(redeemForm) });
    const data = await jsonResponse(response);
    const result = document.querySelector("#deliveryResult");
    document.querySelector("#deliveryFilename").textContent = data.filename;
    const link = document.querySelector("#deliveryLink");
    link.href = safeDownloadUrl(data.download_url);
    link.setAttribute("download", data.filename);
    result.hidden = false;
    requestAnimationFrame(() => result.classList.add("visible"));
    startExpiryCountdown(document.querySelector("#deliveryExpiry"), data.expires_at);
    showMessage(isLookup ? "原文件已找回" : "交付链接已生成", "success");
    refreshInventory();
  } catch (error) {
    showMessage(error.message || (isLookup ? "查找失败" : "兑换失败"));
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
    ? `${files.length} 个文件 · ${(total / 1024 / 1024).toFixed(2)} MB`
    : "支持 JSON、CPA、SUB、SUB2、ZIP";
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
  setLoading(button, true, "正在转换");
  try {
    const response = await fetch("/api/convert", { method: "POST", body: new FormData(convertForm) });
    const data = await jsonResponse(response);
    const result = document.querySelector("#convertResult");
    document.querySelector("#convertResultTitle").textContent = `${data.source_format} → ${data.target_format}`;
    document.querySelector("#convertResultMeta").textContent = `${data.account_count} 个账号 · ${data.filename}`;
    const link = document.querySelector("#convertDownloadLink");
    link.href = safeDownloadUrl(data.download_url);
    link.setAttribute("download", data.filename);
    result.hidden = false;
    requestAnimationFrame(() => result.classList.add("visible"));
    startExpiryCountdown(document.querySelector("#convertExpiry"), data.expires_at);
    showMessage("格式转换完成", "success");
  } catch (error) {
    showMessage(error.message || "转换失败");
  } finally {
    setLoading(button, false, "");
  }
});

initializeFormatOptions();
refreshInventory();
if (inventoryEl) window.setInterval(refreshInventory, 15000);

const passwordToggle = document.querySelector("[data-password-toggle]");
passwordToggle?.addEventListener("click", () => {
  const password = document.querySelector("#password");
  if (!password) return;
  const willShow = password.type === "password";
  password.type = willShow ? "text" : "password";
  passwordToggle.textContent = willShow ? "隐藏" : "显示";
  passwordToggle.setAttribute("aria-label", willShow ? "隐藏密码" : "显示密码");
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
  if (loginSubmit) {
    loginSubmit.disabled = true;
    const label = loginSubmit.querySelector("span");
    if (label) label.textContent = "正在验证";
  }
});

window.addEventListener("pageshow", () => {
  if (!loginForm) return;
  delete loginForm.dataset.submitting;
  loginForm.removeAttribute("aria-busy");
  if (loginSubmit) {
    loginSubmit.disabled = false;
    const label = loginSubmit.querySelector("span");
    if (label) label.textContent = "进入管理控制台";
  }
});

const portalPaths = new Set(["/", "/convert", "/lookup"]);
const hasNativeCrossDocumentTransition =
  typeof CSS !== "undefined" &&
  CSS.supports?.("view-transition-name: root") &&
  "onpageswap" in window;
const prefersReducedPortalMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
let portalEntryTimer = 0;
let portalNavigationPending = false;

function finishPortalEntry() {
  document.documentElement.classList.remove("is-portal-leaving");
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

  try {
    sessionStorage.setItem("portal-cross-navigation", target.pathname);
  } catch (_) {}
  portalNavigationPending = true;
  if (hasNativeCrossDocumentTransition) return;

  event.preventDefault();
  if (!prefersReducedPortalMotion) document.documentElement.classList.add("is-portal-leaving");
  window.setTimeout(() => window.location.assign(target.href), prefersReducedPortalMotion ? 0 : 120);
});
