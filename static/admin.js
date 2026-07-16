function startRouteLoading() {
  document.body.classList.add("is-route-loading");
}

function stopRouteLoading() {
  document.body.classList.remove("is-route-loading");
}

let activeApiRequests = 0;

function startApiLoading() {
  activeApiRequests += 1;
  document.body.classList.add("is-api-loading");
}

function stopApiLoading() {
  activeApiRequests = Math.max(0, activeApiRequests - 1);
  if (activeApiRequests === 0) {
    window.setTimeout(() => {
      if (activeApiRequests === 0) document.body.classList.remove("is-api-loading");
    }, 120);
  }
}

if (window.fetch && !window.fetch.__withGlobalLoading) {
  const nativeFetch = window.fetch.bind(window);
  const fetchWithGlobalLoading = async (...args) => {
    startApiLoading();
    try {
      const [resource, init = {}] = args;
      const method = String(init.method || "GET").toUpperCase();
      const target = new URL(typeof resource === "string" ? resource : resource.url, window.location.href);
      if (target.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
        const headers = new Headers(init.headers || {});
        const token = document.querySelector('meta[name="csrf-token"]')?.content;
        if (token) headers.set("X-CSRF-Token", token);
        return await nativeFetch(resource, { ...init, headers });
      }
      return await nativeFetch(resource, init);
    } finally {
      stopApiLoading();
    }
  };
  fetchWithGlobalLoading.__withGlobalLoading = true;
  window.fetch = fetchWithGlobalLoading;
}

function isModifiedClick(event) {
  return event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

const hasNativeAdminTransition =
  typeof CSS !== "undefined" &&
  CSS.supports?.("view-transition-name: root") &&
  "onpageswap" in window;

function shouldShowRouteLoadingForLink(link) {
  if (!link || link.target || link.hasAttribute("download")) return false;

  const href = link.getAttribute("href");
  if (!href || href.startsWith("#") || href.startsWith("javascript:") || href.startsWith("mailto:") || href.startsWith("tel:")) {
    return false;
  }

  const nextUrl = new URL(link.href, window.location.href);
  if (nextUrl.origin !== window.location.origin) return false;
  return nextUrl.href !== window.location.href;
}

function shouldShowRouteLoadingForForm(form, submitter) {
  if (!form || form.dataset.noRouteProgress === "true") return false;

  const method = ((submitter?.getAttribute("formmethod") || form.method || "get")).toLowerCase();
  if (method === "dialog") return false;

  const action = submitter?.getAttribute("formaction") || form.action || window.location.href;
  const nextUrl = new URL(action, window.location.href);
  if (nextUrl.origin !== window.location.origin) return false;

  if (/\/download(?:$|[/?#])/i.test(nextUrl.pathname)) return false;
  if (/\/status(?:$|[/?#])/i.test(nextUrl.pathname)) return false;
  return true;
}

/*
  if (/download|\/download|下载|涓嬭浇/i.test(label)) return false;
  return true;
}

*/
document.addEventListener("click", (event) => {
  if (isModifiedClick(event)) return;
  const link = event.target.closest("a[href]");
  if (!shouldShowRouteLoadingForLink(link)) return;
  if (hasNativeAdminTransition && document.body.classList.contains("admin-surface")) {
    try {
      sessionStorage.setItem("admin-cross-navigation", "1");
    } catch (_) {}
  }
  startRouteLoading();
});

document.addEventListener("submit", (event) => {
  if (event.defaultPrevented) return;
  const submitter = event.submitter || document.activeElement;
  if (!shouldShowRouteLoadingForForm(event.target, submitter)) return;
  if (hasNativeAdminTransition && document.body.classList.contains("admin-surface")) {
    try {
      sessionStorage.setItem("admin-cross-navigation", "1");
    } catch (_) {}
  }
  startRouteLoading();
});

window.addEventListener("pageshow", () => {
  activeApiRequests = 0;
  document.body.classList.remove("is-api-loading");
  stopRouteLoading();
});

const activeAdminNav = document.querySelector(".admin-sidebar .nav-link.active");
if (activeAdminNav && window.matchMedia("(max-width: 900px)").matches) {
  requestAnimationFrame(() => activeAdminNav.scrollIntoView({ block: "nearest", inline: "center" }));
}

const searchableSelects = new Set();

function normalizeSelectText(text) {
  return text.trim().toLowerCase();
}

function closeSearchableSelect(wrapper) {
  if (!wrapper) return;
  wrapper.classList.remove("open");
  wrapper.querySelector(".system-select-button")?.setAttribute("aria-expanded", "false");
}

function closeOtherSearchableSelects(activeWrapper) {
  searchableSelects.forEach((wrapper) => {
    if (wrapper !== activeWrapper) closeSearchableSelect(wrapper);
  });
}

function updateSearchableSelectButton(select, wrapper) {
  const selected = select.selectedOptions[0] || select.options[0];
  const label = selected ? selected.textContent.trim() : "";
  const button = wrapper.querySelector(".system-select-button");
  const value = wrapper.querySelector(".system-select-value");
  if (value) value.textContent = label;
  if (button) {
    button.title = label;
    button.disabled = select.disabled;
    button.setAttribute("aria-disabled", select.disabled ? "true" : "false");
  }
  wrapper.querySelectorAll(".system-select-option").forEach((option) => {
    option.setAttribute("aria-selected", Number(option.dataset.index) === select.selectedIndex ? "true" : "false");
  });
}

function filterSearchableOptions(wrapper, query = "") {
  const normalizedQuery = normalizeSelectText(query);
  let visibleCount = 0;
  wrapper.querySelectorAll(".system-select-option").forEach((option) => {
    const isVisible = normalizeSelectText(option.textContent || "").includes(normalizedQuery);
    option.hidden = !isVisible;
    if (isVisible) visibleCount += 1;
  });
  const empty = wrapper.querySelector(".system-select-empty");
  if (empty) empty.hidden = visibleCount > 0;
}

function openSearchableSelect(wrapper) {
  closeOtherSearchableSelects(wrapper);
  wrapper.classList.add("open");
  wrapper.querySelector(".system-select-button")?.setAttribute("aria-expanded", "true");
  const search = wrapper.querySelector(".system-select-search");
  filterSearchableOptions(wrapper, "");
  if (search) {
    search.value = "";
    window.requestAnimationFrame(() => search.focus());
  }
}

function enhanceSearchableSelect(select, index) {
  if (select.dataset.searchableSelectReady === "true" || select.multiple) return;
  select.dataset.searchableSelectReady = "true";

  const wrapper = document.createElement("div");
  wrapper.className = "system-select";
  wrapper.dataset.selectName = select.name || "";

  const button = document.createElement("button");
  button.type = "button";
  button.className = "system-select-button";
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");

  const value = document.createElement("span");
  value.className = "system-select-value";
  button.appendChild(value);

  const panel = document.createElement("div");
  panel.className = "system-select-panel";
  panel.setAttribute("role", "listbox");

  const search = document.createElement("input");
  search.className = "system-select-search";
  search.type = "search";
  search.placeholder = "\u641c\u7d22";
  search.autocomplete = "off";
  search.spellcheck = false;
  search.setAttribute("aria-label", "\u641c\u7d22\u9009\u9879");

  const options = document.createElement("div");
  options.className = "system-select-options";

  Array.from(select.options).forEach((nativeOption, optionIndex) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "system-select-option";
    option.dataset.value = nativeOption.value;
    option.dataset.index = String(optionIndex);
    option.setAttribute("role", "option");
    option.textContent = nativeOption.textContent.trim();
    option.disabled = nativeOption.disabled;
    option.setAttribute("aria-selected", nativeOption.selected ? "true" : "false");
    options.appendChild(option);
  });

  const empty = document.createElement("div");
  empty.className = "system-select-empty";
  empty.hidden = true;
  empty.textContent = "\u6ca1\u6709\u5339\u914d\u9009\u9879";

  panel.append(search, options, empty);
  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(select, button, panel);
  select.classList.add("system-select-native");
  select.tabIndex = -1;

  const panelId = select.id ? `${select.id}-system-listbox` : `system-select-${index}-listbox`;
  panel.id = panelId;
  button.setAttribute("aria-controls", panelId);

  updateSearchableSelectButton(select, wrapper);
  searchableSelects.add(wrapper);

  button.addEventListener("click", () => {
    if (select.disabled) return;
    if (wrapper.classList.contains("open")) {
      closeSearchableSelect(wrapper);
    } else {
      openSearchableSelect(wrapper);
    }
  });

  search.addEventListener("input", () => filterSearchableOptions(wrapper, search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSearchableSelect(wrapper);
      button.focus();
      return;
    }
    if (event.key !== "Enter") return;
    const firstVisible = wrapper.querySelector(".system-select-option:not([hidden]):not(:disabled)");
    if (!firstVisible) return;
    event.preventDefault();
    firstVisible.click();
  });

  options.addEventListener("click", (event) => {
    const option = event.target.closest(".system-select-option");
    if (!option || option.disabled) return;
    const nextOption = select.options[Number(option.dataset.index)];
    if (!nextOption) return;
    select.value = nextOption.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    updateSearchableSelectButton(select, wrapper);
    closeSearchableSelect(wrapper);
    button.focus();
  });

  select.addEventListener("change", () => updateSearchableSelectButton(select, wrapper));
}

document.querySelectorAll("select").forEach(enhanceSearchableSelect);

document.querySelectorAll("[data-compact-upload]").forEach((drop) => {
  const input = drop.querySelector("[data-compact-upload-input]");
  const label = drop.querySelector("[data-compact-upload-label]");
  if (!input || !label) return;
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    if (files.length === 0) {
      label.textContent = "选择 JSON / CPA / SUB / SUB2 / ZIP";
    } else if (files.length === 1) {
      label.textContent = files[0].name;
    } else {
      label.textContent = `已选择 ${files.length} 个文件`;
    }
  });
});

document.addEventListener("click", (event) => {
  if (event.target.closest(".system-select")) return;
  closeOtherSearchableSelects(null);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeOtherSearchableSelects(null);
});

function updateBulkActions(form) {
  if (!form) return;
  const selected = Array.from(form.querySelectorAll("input[type='checkbox'][name='ids']:checked"));
  const count = form.querySelector("[data-selected-count]");
  if (count) count.textContent = `已选 ${selected.length} 个`;
  const select = form.querySelector(".bulk-status-select");
  if (select) {
    select.disabled = false;
    const wrapper = select.closest(".system-select");
    if (wrapper) updateSearchableSelectButton(select, wrapper);
  }
  form.querySelectorAll("[data-bulk-action]").forEach((button) => {
    const allowedStatuses = (button.dataset.allowedStatuses || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const hasAllowedSelection = allowedStatuses.length === 0 || selected.some((checkbox) => allowedStatuses.includes(checkbox.dataset.status || ""));
    button.disabled = selected.length === 0 || !hasAllowedSelection;
  });
}

document.addEventListener("change", (event) => {
  const master = event.target.closest("[data-check-all]");
  if (master) {
    const form = master.closest("form");
    form.querySelectorAll("input[type='checkbox'][name='ids']:not(:disabled)").forEach((checkbox) => {
      checkbox.checked = master.checked;
    });
    updateBulkActions(form);
    return;
  }
  const checkbox = event.target.closest("[data-bulk-form] input[type='checkbox'][name='ids']");
  if (checkbox) updateBulkActions(checkbox.closest("[data-bulk-form]"));
});

document.querySelectorAll("[data-bulk-form]").forEach((form) => {
  updateBulkActions(form);
});

let pendingConfirm = null;
let overlayLockCount = 0;

function lockPageScroll() {
  overlayLockCount += 1;
  if (overlayLockCount > 1) return;
  const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
  document.body.style.paddingRight = scrollbarWidth > 0 ? `${scrollbarWidth}px` : "";
  document.body.classList.add("overlay-open");
}

function unlockPageScroll() {
  overlayLockCount = Math.max(0, overlayLockCount - 1);
  if (overlayLockCount > 0) return;
  document.body.classList.remove("overlay-open");
  document.body.style.paddingRight = "";
}

function recoverStaleScrollLock() {
  const activeOverlay = document.querySelector(
    ".confirm-dialog:not([hidden]), .drawer.open[aria-hidden='false']",
  );
  if (activeOverlay) return;
  overlayLockCount = 0;
  document.body.classList.remove("overlay-open");
  document.body.style.paddingRight = "";
}

window.addEventListener("pageshow", recoverStaleScrollLock);
document.addEventListener("wheel", recoverStaleScrollLock, { passive: true });

function setHiddenField(form, name, value) {
  let input = form.querySelector(`input[type="hidden"][name="${name}"]`);
  if (!input) {
    input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    form.appendChild(input);
  }
  input.value = value;
}

function closeConfirmDialog() {
  const dialog = document.querySelector("[data-confirm-dialog]");
  const backdrop = document.querySelector("[data-confirm-backdrop]");
  if (!dialog || !backdrop) return;
  dialog.hidden = true;
  backdrop.hidden = true;
  dialog.classList.remove("open");
  backdrop.classList.remove("open");
  pendingConfirm = null;
  unlockPageScroll();
}

function cancelConfirmDialog() {
  closeConfirmDialog();
}

function openConfirmDialog(message, onChoice, yesLabel = "确认", noLabel = "取消") {
  const dialog = document.querySelector("[data-confirm-dialog]");
  const backdrop = document.querySelector("[data-confirm-backdrop]");
  const messageNode = document.querySelector("[data-confirm-message]");
  const yesButton = document.querySelector("[data-confirm-yes]");
  const noButton = document.querySelector("[data-confirm-no]");
  if (!dialog || !backdrop || !messageNode || !yesButton || !noButton) {
    onChoice(false);
    return;
  }
  pendingConfirm = onChoice;
  messageNode.textContent = message;
  yesButton.textContent = yesLabel;
  noButton.textContent = noLabel;
  lockPageScroll();
  backdrop.hidden = false;
  dialog.hidden = false;
  requestAnimationFrame(() => {
    backdrop.classList.add("open");
    dialog.classList.add("open");
    yesButton.focus();
  });
}

function resolveConfirmDialog(choice) {
  const callback = pendingConfirm;
  closeConfirmDialog();
  if (callback) callback(choice);
}

function filenameFromDisposition(disposition) {
  if (!disposition) return "cards_batch.txt";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) return decodeURIComponent(encoded[1]);
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : "cards_batch.txt";
}

async function downloadWithOptionalRefresh(form, submitter, fieldName, shouldMark) {
  const action = submitter?.getAttribute("formaction") || form.action;
  const formData = new FormData(form);
  if (fieldName) {
    setHiddenField(form, fieldName, shouldMark ? "1" : "0");
    formData.set(fieldName, shouldMark ? "1" : "0");
  }
  submitter.disabled = true;

  try {
    const response = await fetch(action, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error("下载失败，请刷新后重试");

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filenameFromDisposition(response.headers.get("content-disposition"));
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    if (shouldMark) {
      window.setTimeout(() => window.location.reload(), 300);
    }
  } catch (error) {
    showToast(error.message || "下载失败，请刷新后重试");
  } finally {
    submitter.disabled = false;
  }
}

async function submitStatusUpdate(form, submitter) {
  const action = submitter?.getAttribute("formaction") || form.action;
  const formData = new FormData(form);
  submitter.disabled = true;

  try {
    const response = await fetch(action, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error("接口返回异常，请刷新页面后重试");
    }
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "修改状态失败");
    showToast(data.message || "状态已修改", "success");
    window.setTimeout(() => window.location.reload(), 450);
  } catch (error) {
    showToast(error.message || "修改状态失败");
    submitter.disabled = false;
  }
}

async function submitAccountStatusCheck(form, submitter) {
  const action = submitter?.getAttribute("formaction") || form.action;
  const formData = new FormData(form);
  submitter.disabled = true;

  try {
    const response = await fetch(action, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error("接口返回异常，请刷新页面后重试");
    }
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "账号状态检测失败");
    showToast(data.message || "账号状态已检测", "success");
    window.setTimeout(() => window.location.reload(), 650);
  } catch (error) {
    showToast(error.message || "账号状态检测失败");
    submitter.disabled = false;
  }
}

document.querySelectorAll("[data-bulk-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const submitter = event.submitter || document.activeElement;
    if (submitter?.matches("[data-confirm-card-action]") && submitter.dataset.confirmed !== "true") {
      event.preventDefault();
      openConfirmDialog(
        submitter.dataset.confirmMessage || "确定执行此操作？",
        (confirmed) => {
          if (!confirmed) return;
          submitter.dataset.confirmed = "true";
          form.requestSubmit(submitter);
          window.setTimeout(() => delete submitter.dataset.confirmed, 0);
        },
        submitter.dataset.confirmLabel || "确认",
      );
      return;
    }
    if (submitter?.matches("[data-status-update]")) {
      event.preventDefault();
      submitStatusUpdate(form, submitter);
      return;
    }
    if (submitter?.matches("[data-account-check]")) {
      event.preventDefault();
      submitAccountStatusCheck(form, submitter);
      return;
    }
    if (!submitter?.matches("[data-card-download], [data-file-download]")) return;
    event.preventDefault();
    if (submitter.matches("[data-card-download]")) {
      downloadWithOptionalRefresh(form, submitter);
      return;
    }
    openConfirmDialog("下载的文件是否标记已使用？", (shouldMarkSold) => {
      downloadWithOptionalRefresh(form, submitter, "mark_sold", shouldMarkSold);
    }, "标记已使用", "仅下载");
  });
});

document.querySelector("[data-confirm-yes]")?.addEventListener("click", () => resolveConfirmDialog(true));
document.querySelector("[data-confirm-no]")?.addEventListener("click", () => resolveConfirmDialog(false));
document.querySelector("[data-confirm-close]")?.addEventListener("click", cancelConfirmDialog);
document.querySelector("[data-confirm-backdrop]")?.addEventListener("click", cancelConfirmDialog);

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.left = "-9999px";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-text]");
  if (!button || button.disabled) return;
  try {
    await copyText(button.dataset.copyText || "");
    showToast("卡密已复制", "success");
  } catch (error) {
    showToast("复制失败，请手动复制");
  }
});

function closeDrawers() {
  let closedAny = false;
  document.querySelectorAll(".drawer.open").forEach((drawer) => {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    closedAny = true;
  });
  document.querySelectorAll(".drawer-backdrop").forEach((backdrop) => {
    backdrop.hidden = true;
    backdrop.classList.remove("open");
  });
  if (closedAny) unlockPageScroll();
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-drawer-open]");
  if (!button) return;
  const drawer = document.querySelector(`#${button.dataset.drawerOpen}`);
  const backdrop = document.querySelector(".drawer-backdrop");
  if (!drawer || !backdrop) return;
  lockPageScroll();
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.classList.add("open");
  });
});

function openDrawerElement(drawer) {
  const backdrop = document.querySelector(".drawer-backdrop");
  if (!drawer || !backdrop) return;
  closeDrawers();
  lockPageScroll();
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.classList.add("open");
  });
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-card-policy-open]");
  if (!button || button.disabled) return;
  const bulkForm = button.closest("[data-bulk-form]");
  const drawer = document.querySelector("#policyDrawer");
  const policyForm = drawer?.querySelector("[data-card-policy-form]");
  const idsContainer = policyForm?.querySelector("[data-card-policy-ids]");
  const selectedLabel = drawer?.querySelector("[data-card-policy-selected]");
  if (!bulkForm || !drawer || !policyForm || !idsContainer || !selectedLabel) return;
  const selected = Array.from(bulkForm.querySelectorAll('input[name="ids"]:checked'));
  if (selected.length === 0) return;
  const fields = document.createDocumentFragment();
  selected.forEach((checkbox) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "ids";
    input.value = checkbox.value;
    fields.appendChild(input);
  });
  idsContainer.replaceChildren(fields);
  selectedLabel.textContent = `将更新 ${selected.length} 张卡密`;
  openDrawerElement(drawer);
});

function renderUsageDetails(data) {
  const drawer = document.querySelector("#usageDrawer");
  const code = drawer?.querySelector("[data-usage-card-code]");
  const firstTime = drawer?.querySelector("[data-usage-first-time]");
  const usageCount = drawer?.querySelector("[data-usage-count]");
  const usageMax = drawer?.querySelector("[data-usage-max]");
  const usageExpires = drawer?.querySelector("[data-usage-expires]");
  const list = drawer?.querySelector("[data-usage-list]");
  if (!drawer || !code || !firstTime || !usageCount || !usageMax || !usageExpires || !list) return;

  code.textContent = data.card_code || "-";
  firstTime.textContent = data.first_used_at || "-";
  list.replaceChildren();

  const redemptions = Array.isArray(data.redemptions) ? data.redemptions : [];
  usageCount.textContent = String(data.redemption_count ?? redemptions.length);
  usageMax.textContent = String(data.max_redemptions ?? 1);
  usageExpires.textContent = data.expires_at || "永不过期";
  if (redemptions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "暂无使用记录";
    list.appendChild(empty);
    return;
  }

  redemptions.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "usage-item";

    const main = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = `第 ${index + 1} 次使用 · ${item.file_count || 0} 个文件`;
    const time = document.createElement("strong");
    time.textContent = item.redeemed_at || "-";
    main.append(label, time);

    const format = document.createElement("div");
    format.className = "usage-format";
    format.textContent = item.output_format || "-";

    const action = document.createElement("button");
    action.type = "button";
    action.className = "ghost-action mini usage-link-action";
    action.textContent = "生成下载链接";
    action.dataset.redemptionLink = String(item.id || "");

    row.append(main, format, action);
    list.appendChild(row);
  });
}

async function openUsageDetails(button) {
  const drawer = document.querySelector("#usageDrawer");
  if (!drawer || button.disabled) return;

  renderUsageDetails({
    card_code: "加载中...",
    first_used_at: "-",
    redemptions: [],
  });
  openDrawerElement(drawer);
  button.disabled = true;

  try {
    const response = await fetch(`/admin/cards/${button.dataset.cardId}/redemptions`, {
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "使用详情加载失败");
    renderUsageDetails(data);
  } catch (error) {
    showToast(error.message || "使用详情加载失败");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-usage-detail]");
  if (button) openUsageDetails(button);
});

document.querySelector("[data-usage-list]")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-redemption-link]");
  if (!button || button.disabled) return;
  button.disabled = true;
  button.textContent = "正在生成";
  try {
    const response = await fetch(`/admin/redemptions/${button.dataset.redemptionLink}/link`, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "生成失败");
    const link = document.createElement("a");
    link.className = "secondary-action mini usage-download-link";
    link.href = data.download_url;
    link.textContent = "下载文件 · 24h";
    link.setAttribute("download", data.filename || "");
    button.replaceWith(link);
    showToast("24 小时下载链接已生成", "success");
  } catch (error) {
    button.disabled = false;
    button.textContent = "重新生成";
    showToast(error.message || "生成下载链接失败");
  }
});

document.querySelectorAll("[data-reset-password]").forEach((button) => {
  button.addEventListener("click", () => {
    const drawer = document.querySelector("#passwordDrawer");
    const backdrop = document.querySelector(".drawer-backdrop");
    const form = drawer?.querySelector("[data-reset-form]");
    const username = drawer?.querySelector("[data-reset-username]");
    const userId = drawer?.querySelector("[data-reset-user-id]");
    if (!drawer || !backdrop || !form || !username || !userId) return;
    form.reset();
    userId.value = button.dataset.userId || "";
    username.textContent = button.dataset.username || "-";
    lockPageScroll();
    backdrop.hidden = false;
    requestAnimationFrame(() => {
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
      backdrop.classList.add("open");
    });
  });
});

document.querySelectorAll("[data-pool-config]").forEach((button) => {
  button.addEventListener("click", () => {
    const drawer = document.querySelector("#poolDrawer");
    const backdrop = document.querySelector(".drawer-backdrop");
    const form = drawer?.querySelector("[data-pool-form]");
    const username = drawer?.querySelector("[data-pool-username]");
    const urlInput = drawer?.querySelector("[data-pool-url]");
    const keyInput = drawer?.querySelector('input[name="quota_pool_management_key"]');
    if (!drawer || !backdrop || !form || !username || !urlInput || !keyInput) return;
    form.action = `/admin/users/${button.dataset.userId || ""}/pool`;
    username.textContent = button.dataset.username || "-";
    urlInput.value = button.dataset.poolUrl || "";
    keyInput.value = "";
    lockPageScroll();
    backdrop.hidden = false;
    requestAnimationFrame(() => {
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
      backdrop.classList.add("open");
    });
  });
});

document.querySelectorAll("[data-drawer-close]").forEach((button) => {
  button.addEventListener("click", closeDrawers);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDrawers();
    if (pendingConfirm) cancelConfirmDialog();
  }
});

function showToast(message, type = "error") {
  const toast = document.querySelector("#adminToast");
  if (!toast) return;
  toast.hidden = false;
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.remove("show");
    window.setTimeout(() => {
      toast.hidden = true;
    }, 180);
  }, 2600);
}

document.querySelectorAll(".page-flash").forEach((flash) => {
  showToast(flash.textContent.trim(), flash.dataset.toastType || "success");
  flash.remove();
});

function formatUploadSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

document.querySelectorAll("[data-upload-form]").forEach((form) => {
  const input = form.querySelector("[data-upload-input]");
  const summary = form.querySelector("[data-upload-summary]");
  const list = form.querySelector("[data-upload-list]");
  const submit = form.querySelector("[data-upload-submit]");
  const maxBytes = 25 * 1024 * 1024;
  const maxFiles = 100;
  if (!input || !summary || !list || !submit) return;

  function renderUploadFiles() {
    const files = Array.from(input.files || []);
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    const invalidFiles = files.filter((file) => !/\.(json|cpa|sub|sub2|zip)$/i.test(file.name));
    const chineseNameFiles = files.filter((file) => /[\u3400-\u9fff\uf900-\ufaff]/.test(file.name));
    const uploadableFiles = files.filter((file) => /\.(json|cpa|sub|sub2|zip)$/i.test(file.name) && !/[\u3400-\u9fff\uf900-\ufaff]/.test(file.name));
    const isOversized = totalBytes > maxBytes;
    const hasTooManyFiles = files.length > maxFiles;

    list.replaceChildren();
    files.slice(0, 8).forEach((file) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      const size = document.createElement("span");
      name.textContent = file.name;
      size.textContent = formatUploadSize(file.size);
      item.append(name, size);
      list.appendChild(item);
    });
    if (files.length > 8) {
      const item = document.createElement("li");
      const name = document.createElement("span");
      const size = document.createElement("span");
      name.textContent = `另有 ${files.length - 8} 个文件`;
      size.textContent = "";
      item.append(name, size);
      list.appendChild(item);
    }
    list.hidden = files.length === 0;

    const hasError = isOversized || hasTooManyFiles || invalidFiles.length > 0 || chineseNameFiles.length > 0;
    summary.classList.toggle("error", hasError);
    submit.disabled = files.length === 0 || isOversized || hasTooManyFiles || invalidFiles.length > 0 || uploadableFiles.length === 0;

    if (files.length === 0) {
      summary.textContent = "尚未选择文件";
    } else if (invalidFiles.length > 0) {
      summary.textContent = "只能上传 JSON、CPA、SUB、SUB2 或 ZIP 文件";
    } else if (chineseNameFiles.length > 0) {
      summary.textContent = `文件名不能包含中文，将跳过：${chineseNameFiles[0].name}`;
    } else if (hasTooManyFiles) {
      summary.textContent = `单批最多选择 ${maxFiles} 个文件`;
    } else if (isOversized) {
      summary.textContent = `当前选择 ${files.length} 个文件，共 ${formatUploadSize(totalBytes)}，已超过 25MB`;
    } else {
      summary.textContent = `已选择 ${files.length} 个文件，共 ${formatUploadSize(totalBytes)}`;
    }
  }

  input.addEventListener("change", renderUploadFiles);
  renderUploadFiles();
});

const importModeButtons = document.querySelectorAll("[data-import-mode]");
const importPanes = document.querySelectorAll("[data-import-pane]");
importModeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const mode = button.dataset.importMode;
    importModeButtons.forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    importPanes.forEach((pane) => {
      pane.hidden = pane.dataset.importPane !== mode;
    });
  });
});

const manualJson = document.querySelector("[data-manual-json]");
const manualSize = document.querySelector("[data-manual-size]");
if (manualJson && manualSize) {
  const renderManualSize = () => {
    manualSize.textContent = formatUploadSize(new Blob([manualJson.value]).size);
  };
  manualJson.addEventListener("input", renderManualSize);
  renderManualSize();
}

if (window.location.search.includes("message=") || window.location.search.includes("error=")) {
  const url = new URL(window.location.href);
  url.searchParams.delete("message");
  url.searchParams.delete("error");
  window.history.replaceState({}, "", url);
}

document.querySelectorAll("[data-user-toggle]").forEach((button) => {
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    if (button.disabled) return;

    const form = button.closest("form");
    const label = button.querySelector("b");
    button.disabled = true;

    try {
      const response = await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error("接口返回异常，请刷新页面后重试");
      }
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "切换失败");

      button.classList.toggle("on", data.is_active);
      button.classList.toggle("off", !data.is_active);
      label.textContent = data.label;
      const row = button.closest("tr");
      const updatedAt = row?.querySelector("[data-updated-at]");
      if (updatedAt && data.updated_at) updatedAt.textContent = data.updated_at;
      showToast(`账号 ${data.username} 已${data.label}`, "success");
    } catch (error) {
      showToast(error.message || "切换失败");
    } finally {
      button.disabled = false;
    }
  });
});
