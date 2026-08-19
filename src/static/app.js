const $ = (id) => document.getElementById(id);
const DEVELOPER_MODE_KEY = "simbridge.developerMode";
const AUTH_TOKEN_KEY = "simbridge.mobileToken";

const state = {
  pendingSend: null,
  refreshTimer: null,
  loading: false,
  refreshQueued: false,
  view: "messages",
  messageBox: "all",
  developerMode: false,
  forwarding: null,
  selectedIndices: new Set(),
  search: "",
  mobileSection: "messages",
  started: false,
  authToken: "",
  loginChallenge: "",
  mobileAccessInfo: null,
};

try {
  state.authToken = window.localStorage.getItem(AUTH_TOKEN_KEY) || "";
} catch (_error) {
  // Storage can be unavailable in private browsing.
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.authToken) headers.Authorization = `Bearer ${state.authToken}`;
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const data = await response.json();
  if (response.status === 401) {
    clearAuthToken();
    showPairModal();
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function clearAuthToken() {
  state.authToken = "";
  state.loginChallenge = "";
  try {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch (_error) {
    // Ignore unavailable browser storage.
  }
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value || "-";
}

function setPill(text, kind = "") {
  const pill = $("statusPill");
  if (!pill) return;
  pill.textContent = text;
  pill.className = `pill ${kind}`.trim();
}

function fmt(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  return `${value}${suffix}`;
}

function mapOperatorChinese(name, plmn) {
  if (plmn) {
    const plmnClean = String(plmn).replace(/[^0-9]/g, "");
    const plmnMap = {
      "46003": "中国电信", "46005": "中国电信", "46011": "中国电信",
      "46001": "中国联通", "46006": "中国联通", "46009": "中国联通",
      "46000": "中国移动", "46002": "中国移动", "46004": "中国移动", "46007": "中国移动", "46008": "中国移动",
      "46015": "中国广电",
    };
    if (plmnMap[plmnClean]) return plmnMap[plmnClean];
  }
  const s = String(name || "").trim().toUpperCase();
  if (s.includes("CTCC") || s.includes("CHN-CT") || s.includes("CHINA TELECOM") || s.includes("TELECOM")) {
    return "中国电信";
  }
  if (s.includes("UNICOM") || s.includes("CHN-UNICOM") || s.includes("CHINA UNICOM")) {
    return "中国联通";
  }
  if (s.includes("CMCC") || s.includes("CHN-CMCC") || s.includes("CHINA MOBILE") || s.includes("MOBILE")) {
    return "中国移动";
  }
  if (s.includes("CBN") || s.includes("CHN-CB") || s.includes("BROADNET") || s.includes("CHINA BROADNET")) {
    return "中国广电";
  }
  return name || "-";
}

function renderStatus(data) {
  const signal = data.signal || {};
  const storage = data.storage || {};
  const operator = data.operator || {};
  const smsc = data.smsc || {};

  setText("simValue", data.sim);
  const operatorDisplayName = mapOperatorChinese(operator.chineseName || operator.name, operator.plmn);
  setText("operatorValue", [operatorDisplayName, operator.access].filter(Boolean).join(" / "));

  // Format signal with dBm & Quality
  const signalStr = signal.dbm === null || signal.dbm === undefined
    ? signal.raw || "-"
    : `${signal.dbm} dBm / ${signal.quality}`;
  setText("signalValue", signalStr);

  setText("storageValue", storage.total ? `${storage.used}/${storage.total} (${storage.name})` : storage.raw);
  setText("smscValue", smsc.number || smsc.raw);

  if ($("rawStatus")) {
    $("rawStatus").textContent = JSON.stringify(data.raw || {}, null, 2);
  }
  setText("subtitle", data.updatedAt ? `更新于 ${data.updatedAt.replace("T", " ")}` : "已连接");

  if (data.sim === "READY") {
    setPill("在线");
  } else if (data.sim) {
    setPill(data.sim, "warn");
  } else {
    setPill("未知", "muted");
  }
}

function statusLabel(value) {
  return {
    0: "未读",
    1: "已读",
    2: "未发",
    3: "已发",
  }[value] || "短信";
}

function updateBatchBar() {
  const count = state.selectedIndices.size;
  setText("selectedCountText", `已选 ${count} 条`);
  const batchBtn = $("batchDeleteBtn");
  if (batchBtn) {
    batchBtn.disabled = count === 0;
    batchBtn.textContent = count > 0 ? `批量删除 (${count})` : "批量删除";
  }
  const selectAll = $("selectAllCheckbox");
  if (selectAll) {
    const checkboxes = document.querySelectorAll(".message-checkbox");
    const total = checkboxes.length;
    selectAll.checked = total > 0 && Array.from(checkboxes).every((cb) => cb.checked);
    selectAll.indeterminate = count > 0 && !selectAll.checked;
  }
}

function toggleSelectAll(checked) {
  document.querySelectorAll(".message-checkbox").forEach((cb) => {
    cb.checked = checked;
    const idx = cb.dataset.index;
    const article = cb.closest(".message");
    if (checked) {
      state.selectedIndices.add(idx);
      if (article) article.classList.add("selected");
    } else {
      state.selectedIndices.delete(idx);
      if (article) article.classList.remove("selected");
    }
  });
  updateBatchBar();
}

async function batchDelete() {
  const indices = Array.from(state.selectedIndices);
  if (!indices.length) return;
  if (!window.confirm(`确认永久删除选中的 ${indices.length} 条本地存档？`)) return;
  try {
    await Promise.all(indices.map((id) => api("/api/archive/delete", {
      method: "POST",
      body: JSON.stringify({ id }),
    })));
    state.selectedIndices.clear();
    showResult(`已永久删除 ${indices.length} 条本地存档`, true);
    refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

function renderMessages(data, box = state.messageBox) {
  const list = $("messageList");
  const messages = data.messages || [];
  setText("messageCount", `${data.total ?? messages.length} 条`);

  // Clean stale selections
  const currentIndices = new Set(messages.map((m) => m.archiveId));
  for (const idx of state.selectedIndices) {
    if (!currentIndices.has(idx)) {
      state.selectedIndices.delete(idx);
    }
  }

  if (!messages.length) {
    const labels = {
      all: "暂无短信记录",
      inbox: "暂无收件记录",
      sent: "模块内暂无已发记录",
      unread: "暂无未读短信",
      starred: "暂无收藏短信",
    };
    list.innerHTML = `
      <div class="empty-state">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.4;">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <span>${labels[box] || "暂无短信"}</span>
      </div>`;
    updateBatchBar();
    return;
  }

  list.replaceChildren(...messages.map(renderMessage));
  updateBatchBar();
}

function renderMessage(message) {
  const decoded = message.decoded || {};
  const item = document.createElement("article");
  item.className = "message";
  if (state.selectedIndices.has(message.archiveId)) {
    item.classList.add("selected");
  }

  // Message Header
  const header = document.createElement("div");
  header.className = "message-header";

  const sender = document.createElement("div");
  sender.className = "message-sender";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "message-checkbox";
  checkbox.dataset.index = message.archiveId;
  checkbox.checked = state.selectedIndices.has(message.archiveId);
  checkbox.addEventListener("change", (e) => {
    if (e.target.checked) {
      state.selectedIndices.add(message.archiveId);
      item.classList.add("selected");
    } else {
      state.selectedIndices.delete(message.archiveId);
      item.classList.remove("selected");
    }
    updateBatchBar();
  });

  const avatar = document.createElement("div");
  avatar.className = "sender-avatar";
  const peerText = decoded.peer || (message.index === null ? "本地存档" : `索引 ${message.index}`);
  const initial = peerText.replace(/[^0-9a-zA-Z\u4e00-\u9fa5]/g, "").slice(-2) || "短信";
  avatar.textContent = initial;

  const peer = document.createElement("div");
  peer.className = "message-peer";
  peer.textContent = peerText;

  sender.append(checkbox, avatar, peer);

  const statusText = statusLabel(message.status);
  const statusPill = document.createElement("span");
  let pillKind = "muted";
  if (message.status === 0) pillKind = ""; // Green for unread
  if (message.status === 3) pillKind = "muted";
  statusPill.className = `pill ${pillKind}`.trim();
  statusPill.textContent = statusText;

  header.append(sender, statusPill);

  // Text body
  const text = document.createElement("p");
  text.className = "message-text";
  text.textContent = decoded.text || decoded.error || "(空内容)";

  const note = document.createElement("p");
  note.className = "message-note";
  note.textContent = `备注：${message.note || ""}`;
  note.hidden = !message.note;

  // Details dropdown (Developer Mode)
  const details = document.createElement("details");
  details.className = "raw-box developer-only";
  const summary = document.createElement("summary");
  summary.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg> <span>原始 PDU</span>`;
  const pre = document.createElement("pre");
  pre.textContent = message.pdu || "";
  details.append(summary, pre);

  // Footer & Actions
  const footer = document.createElement("div");
  footer.className = "message-footer";

  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = [decoded.timestamp, decoded.dcs].filter(Boolean).join(" · ");

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const starBtn = document.createElement("button");
  starBtn.type = "button";
  starBtn.className = `small-button star-button${message.starred ? " active" : ""}`;
  starBtn.textContent = message.starred ? "已收藏" : "收藏";
  starBtn.addEventListener("click", () => updateArchive(message.archiveId, { starred: !message.starred }));

  const noteBtn = document.createElement("button");
  noteBtn.type = "button";
  noteBtn.className = "small-button";
  noteBtn.textContent = "备注";
  noteBtn.addEventListener("click", () => editNote(message));

  if (message.index !== null && message.index !== undefined) {
    const modemDeleteBtn = document.createElement("button");
    modemDeleteBtn.type = "button";
    modemDeleteBtn.className = "small-button";
    modemDeleteBtn.textContent = "从卡删除";
    modemDeleteBtn.addEventListener("click", () => deleteFromModem(message.index));
    actions.append(modemDeleteBtn);
  }

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "small-button danger";
  deleteBtn.textContent = "删除存档";
  deleteBtn.addEventListener("click", () => deleteArchive(message.archiveId));

  actions.prepend(starBtn, noteBtn);
  actions.append(deleteBtn);
  footer.append(meta, actions);

  item.append(header, text, note, footer, details);
  return item;
}

function renderRadio(data) {
  const cell = data.servingCell || {};
  const network = data.network || {};
  const operator = data.operator || {};
  const signal = data.signal || {};
  const band = cell.bandLabel || network.band || "-";
  const plmn = [data.mcc, data.mnc].filter(Boolean).join(" / ") || data.plmn || "-";
  const quality = [
    fmt(cell.rsrp, " dBm"),
    fmt(cell.rsrq, " dB"),
    fmt(cell.sinr, " dB"),
  ].join(" / ");

  const radioOpName = mapOperatorChinese(operator.chineseName || operator.name, data.plmn || plmn);
  setText("radioOperator", [radioOpName, operator.access].filter(Boolean).join(" / "));
  setText("radioPlmn", plmn);
  setText("radioBand", band);
  setText("radioQuality", quality);
  setText("cellIdValue", cell.cellIdHex ? `${cell.cellIdHex} (${fmt(cell.cellId)})` : fmt(cell.cellId));
  setText("enodebValue", cell.eNodeB === null || cell.eNodeB === undefined ? "-" : `${cell.eNodeB} / ${fmt(cell.sector)}`);
  setText("pciValue", fmt(cell.pci));
  setText("earfcnValue", fmt(cell.earfcn || network.earfcn));
  setText("tacValue", fmt(cell.tac));
  setText("rssiValue", cell.rssi !== null && cell.rssi !== undefined ? `${cell.rssi} dBm` : fmt(signal.dbm, " dBm"));
  setText("srxlevValue", fmt(cell.srxlev));
  if ($("rawRadio")) {
    $("rawRadio").textContent = JSON.stringify(data.raw || {}, null, 2);
  }
}

function setView(view) {
  if (view === "radio" && !state.developerMode) return;
  state.view = view;
  $("messageView").hidden = view !== "messages";
  $("radioView").hidden = view !== "radio";
  setText("mainTitle", view === "radio" ? "信号/基站" : "短信");
  setText("messageCount", view === "radio" ? "实时" : $("messageCount").textContent);
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
}

function setDeveloperMode(enabled, persist = true) {
  state.developerMode = Boolean(enabled);
  document.body.classList.toggle("developer-mode", state.developerMode);

  const toggle = $("developerModeToggle");
  if (toggle) toggle.checked = state.developerMode;

  if (persist) {
    try {
      window.localStorage.setItem(DEVELOPER_MODE_KEY, String(state.developerMode));
    } catch (_error) {
      // Ignore fallback
    }
  }

  if (!state.developerMode && state.view === "radio") {
    setView("messages");
  }
}

function loadDeveloperMode() {
  let enabled = false;
  try {
    enabled = window.localStorage.getItem(DEVELOPER_MODE_KEY) === "true";
  } catch (_error) {
    // Ignore fallback
  }
  setDeveloperMode(enabled, false);
}

function setMessageBox(box) {
  state.messageBox = box;
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.box === box);
  });
  refreshAll();
}

async function refreshAll() {
  if (state.loading) {
    state.refreshQueued = true;
    return;
  }
  state.loading = true;
  state.refreshQueued = false;

  const btn = $("refreshBtn");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("spinning");
  }

  const view = state.view;
  const box = state.messageBox;
  const search = state.search;
  try {
    const statusRequest = api("/api/status")
      .then(renderStatus)
      .catch((error) => {
        setPill("离线", "bad");
        setText("subtitle", `模块不可用 · ${error.message}`);
      });
    let viewRequest;
    if (view === "radio") {
      viewRequest = api("/api/radio");
    } else {
      viewRequest = (async () => {
        await api("/api/archive/sync", { method: "POST", body: "{}" });
        const query = encodeURIComponent(search);
        return api(`/api/messages?box=${encodeURIComponent(box)}&query=${query}`);
      })();
    }
    const [, viewData] = await Promise.all([statusRequest, viewRequest]);
    if (
      view !== state.view ||
      (view === "messages" && (box !== state.messageBox || search !== state.search))
    ) {
      state.refreshQueued = true;
      return;
    }
    if (view === "radio") {
      renderRadio(viewData);
      setText("messageCount", "实时");
    } else {
      renderMessages(viewData, box);
    }
  } catch (error) {
    setPill("异常", "bad");
    setText("subtitle", error.message);
  } finally {
    state.loading = false;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("spinning");
    }
    if (state.refreshQueued) {
      window.setTimeout(refreshAll, 0);
    }
  }
}

async function readOne(index) {
  try {
    const data = await api(`/api/message?index=${encodeURIComponent(index)}`);
    const decoded = data.decoded || {};
    showResult(decoded.text ? `读取短信成功：${decoded.text}` : `读取短信成功 (索引 ${index})`, true);
    refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

async function updateArchive(id, changes) {
  try {
    await api("/api/archive/update", {
      method: "POST",
      body: JSON.stringify({ id, ...changes }),
    });
    refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

function editNote(message) {
  const note = window.prompt("为这条短信添加备注（留空可清除）", message.note || "");
  if (note === null) return;
  updateArchive(message.archiveId, { note });
}

async function deleteArchive(id) {
  if (!window.confirm("确认永久删除这条本地短信存档？如果短信仍在模块中，它也不会再次归档。")) return;
  try {
    await api("/api/archive/delete", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    state.selectedIndices.delete(id);
    showResult("本地短信存档已删除", true);
    refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteFromModem(index) {
  if (!window.confirm(`确认从 SIM/模块中删除索引为 ${index} 的短信？本地长期存档会保留。`)) return;
  try {
    await api("/api/delete", {
      method: "POST",
      body: JSON.stringify({ index }),
    });
    showResult(`已从 SIM/模块删除短信，本地存档仍保留 (索引 ${index})`, true);
    refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

function showResult(message, isOk = true) {
  const result = $("sendResult");
  if (!result) return;
  result.hidden = false;
  result.className = isOk ? "send-result ok" : "send-result error";
  result.textContent = message;
  const toast = $("toast");
  if (toast) {
    toast.hidden = false;
    toast.className = isOk ? "toast" : "toast error";
    toast.textContent = message;
    window.clearTimeout(showResult.toastTimer);
    showResult.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 3200);
  }
}

function showError(message) {
  showResult(message, false);
}

function renderAutostart(data) {
  const toggle = $("autostartToggle");
  if (toggle) {
    toggle.checked = Boolean(data.enabled);
    toggle.disabled = !data.available;
  }
  setText("autostartText", data.message || (data.enabled ? "已开启" : "未开启"));
}

async function loadAutostart() {
  try {
    renderAutostart(await api("/api/autostart"));
  } catch (error) {
    if ($("autostartToggle")) $("autostartToggle").disabled = true;
    setText("autostartText", error.message);
  }
}

async function setAutostart(enabled) {
  const toggle = $("autostartToggle");
  if (toggle) toggle.disabled = true;
  try {
    const data = await api("/api/autostart", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    renderAutostart(data);
  } catch (error) {
    if (toggle) {
      toggle.checked = !enabled;
      toggle.disabled = false;
    }
    setText("autostartText", error.message);
  }
}

function renderForwarding(data) {
  state.forwarding = data;
  const status = $("forwardingText");
  if (!status) return;
  if (data.enabled && data.lastError) {
    status.textContent = `异常：${data.lastError}`;
    status.className = "setting-error";
  } else if (data.enabled && data.lastForwardedAt) {
    status.textContent = `已开启，上次转发 ${data.lastForwardedAt.replace("T", " ")}`;
    status.className = "";
  } else if (data.enabled) {
    status.textContent = "已开启，等待新短信";
    status.className = "";
  } else {
    status.textContent = "未开启";
    status.className = "";
  }
}

async function loadForwarding() {
  try {
    renderForwarding(await api("/api/forwarding"));
  } catch (error) {
    const el = $("forwardingText");
    if (el) {
      el.textContent = error.message;
      el.className = "setting-error";
    }
  }
}

function populateForwardingForm(data) {
  if ($("forwardingEnabled")) $("forwardingEnabled").checked = Boolean(data.enabled);
  if ($("smtpHost")) $("smtpHost").value = data.smtpHost || "";
  if ($("smtpPort")) $("smtpPort").value = data.smtpPort || 587;
  if ($("smtpSecurity")) $("smtpSecurity").value = data.security || "starttls";
  if ($("smtpUsername")) $("smtpUsername").value = data.username || "";
  if ($("smtpPassword")) {
    $("smtpPassword").value = "";
    $("smtpPassword").placeholder = data.passwordSet
      ? "已安全保存，留空保持不变"
      : "请输入密码或授权码";
  }
  if ($("smtpSender")) $("smtpSender").value = data.sender || "";
  if ($("smtpRecipients")) $("smtpRecipients").value = (data.recipients || []).join("\n");
  if ($("smtpSubjectPrefix")) $("smtpSubjectPrefix").value = data.subjectPrefix || "[SIMBridge]";
  if ($("forwardingPollInterval")) $("forwardingPollInterval").value = data.pollInterval || 15;
  if ($("forwardingIncludePdu")) $("forwardingIncludePdu").checked = Boolean(data.includePdu);
  setText("forwardingFormStatus", "");
  if ($("forwardingFormStatus")) $("forwardingFormStatus").className = "form-status";
}

function forwardingPayload() {
  return {
    enabled: $("forwardingEnabled").checked,
    smtpHost: $("smtpHost").value.trim(),
    smtpPort: Number($("smtpPort").value),
    security: $("smtpSecurity").value,
    username: $("smtpUsername").value.trim(),
    password: $("smtpPassword").value,
    sender: $("smtpSender").value.trim(),
    recipients: $("smtpRecipients").value,
    subjectPrefix: $("smtpSubjectPrefix").value.trim(),
    pollInterval: Number($("forwardingPollInterval").value),
    includePdu: $("forwardingIncludePdu").checked,
  };
}

async function openForwarding() {
  $("forwardingModal").hidden = false;
  try {
    const data = await api("/api/forwarding");
    renderForwarding(data);
    populateForwardingForm(data);
  } catch (error) {
    const status = $("forwardingFormStatus");
    if (status) {
      status.textContent = error.message;
      status.className = "form-status error";
    }
  }
}

function closeForwarding() {
  $("forwardingModal").hidden = true;
}

async function saveForwarding(event) {
  event.preventDefault();
  const button = $("saveForwarding");
  if (button) button.disabled = true;
  try {
    const data = await api("/api/forwarding", {
      method: "POST",
      body: JSON.stringify(forwardingPayload()),
    });
    renderForwarding(data);
    closeForwarding();
  } catch (error) {
    const status = $("forwardingFormStatus");
    if (status) {
      status.textContent = error.message;
      status.className = "form-status error";
    }
  } finally {
    if (button) button.disabled = false;
  }
}

async function testForwarding() {
  const button = $("testForwarding");
  if (button) button.disabled = true;
  const status = $("forwardingFormStatus");
  if (status) {
    status.textContent = "正在发送测试邮件…";
    status.className = "form-status";
  }
  try {
    const data = await api("/api/forwarding/test", {
      method: "POST",
      body: JSON.stringify(forwardingPayload()),
    });
    if (status) {
      status.textContent = data.message || "测试邮件已发送成功";
      status.className = "form-status ok";
    }
  } catch (error) {
    if (status) {
      status.textContent = error.message;
      status.className = "form-status error";
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function setPairStep(step) {
  const isCodeStep = step === "code";
  if ($("loginStep")) $("loginStep").hidden = isCodeStep;
  if ($("pairCodeStep")) $("pairCodeStep").hidden = !isCodeStep;
  setText("pairDescription", isCodeStep
    ? "账号密码已通过验证。现在输入 Mac 版 SIMBridge 中显示的 6 位配对码。"
    : "先输入 Mac 端设置的账号和密码。");
  if ($("pairButton")) $("pairButton").textContent = isCodeStep ? "完成配对" : "验证账号";
  window.setTimeout(() => (isCodeStep ? $("pairCodeInput") : $("loginUsernameInput"))?.focus(), 0);
}

function showPairModal(message = "") {
  const modal = $("pairModal");
  if (modal) modal.hidden = false;
  if ($("pairError")) $("pairError").textContent = message;
  setPairStep(state.loginChallenge ? "code" : "login");
}

function hidePairModal() {
  if ($("pairModal")) $("pairModal").hidden = true;
  if ($("pairError")) $("pairError").textContent = "";
  state.loginChallenge = "";
  if ($("loginPasswordInput")) $("loginPasswordInput").value = "";
  if ($("pairCodeInput")) $("pairCodeInput").value = "";
}

async function loadMobileAccess() {
  try {
    const response = await fetch("/api/mobile", { cache: "no-store" });
    const info = await response.json();
    state.mobileAccessInfo = info;
    if (info.local) {
      const urls = info.urls || [];
      setText("mobileAccessUrl", urls.join(" 或 ") || "未检测到局域网地址，请确认 Mac 已连接 Wi‑Fi");
      setText("mobileAccountStatus", info.hasAccount ? `登录账号 ${info.username}` : "尚未设置登录账号，手机暂时无法连接");
      if ($("mobileAccountButton")) $("mobileAccountButton").textContent = info.hasAccount ? "修改账号" : "设置账号";
      setText("mobilePairCode", info.pairCode ? `配对码 ${info.pairCode}` : "");
      return true;
    }
    if (!state.authToken) {
      showPairModal(info.hasAccount ? "" : "请先在 Mac 端设置手机登录账号和密码");
      return false;
    }
    await api("/api/archive/status");
    return true;
  } catch (error) {
    showPairModal(error.message);
    return false;
  }
}

async function submitPair(event) {
  event.preventDefault();
  const button = $("pairButton");
  if (button) button.disabled = true;
  if ($("pairError")) $("pairError").textContent = "";
  try {
    if (!state.loginChallenge) {
      const username = $("loginUsernameInput").value.trim();
      const password = $("loginPasswordInput").value;
      if (!username || !password) throw new Error("请输入账号和密码");
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "账号验证失败");
      state.loginChallenge = data.challenge;
      $("loginPasswordInput").value = "";
      setPairStep("code");
    } else {
      const code = $("pairCodeInput").value.trim();
      if (!/^\d{6}$/.test(code)) throw new Error("请输入 6 位数字配对码");
      const response = await fetch("/api/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, challenge: state.loginChallenge }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        if ((data.error || "").includes("重新输入账号")) {
          state.loginChallenge = "";
          setPairStep("login");
        }
        throw new Error(data.error || "连接失败");
      }
      state.authToken = data.token;
      try {
        window.localStorage.setItem(AUTH_TOKEN_KEY, data.token);
      } catch (_error) {
        // The connection remains valid until the page is closed.
      }
      hidePairModal();
      startApp();
    }
  } catch (error) {
    if ($("pairError")) $("pairError").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

function backToLogin() {
  state.loginChallenge = "";
  if ($("pairCodeInput")) $("pairCodeInput").value = "";
  if ($("pairError")) $("pairError").textContent = "";
  setPairStep("login");
}

function openMobileAccount() {
  if ($("mobileAccountModal")) $("mobileAccountModal").hidden = false;
  if ($("mobileUsernameInput")) $("mobileUsernameInput").value = state.mobileAccessInfo?.username || "";
  if ($("mobilePasswordInput")) $("mobilePasswordInput").value = "";
  if ($("mobilePasswordConfirmInput")) $("mobilePasswordConfirmInput").value = "";
  if ($("mobileAccountFormStatus")) $("mobileAccountFormStatus").textContent = "";
  window.setTimeout(() => $("mobileUsernameInput")?.focus(), 0);
}

function closeMobileAccount() {
  if ($("mobileAccountModal")) $("mobileAccountModal").hidden = true;
}

async function saveMobileAccount(event) {
  event.preventDefault();
  const username = $("mobileUsernameInput").value.trim();
  const password = $("mobilePasswordInput").value;
  const confirmation = $("mobilePasswordConfirmInput").value;
  const status = $("mobileAccountFormStatus");
  const button = $("saveMobileAccount");
  if (password !== confirmation) {
    status.textContent = "两次输入的密码不一致";
    status.className = "form-status error";
    return;
  }
  if (button) button.disabled = true;
  if (status) status.textContent = "";
  try {
    const data = await api("/api/mobile/account", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    closeMobileAccount();
    await loadMobileAccess();
    showResult(`手机登录账号已保存：${data.username}`, true);
  } catch (error) {
    if (status) {
      status.textContent = error.message;
      status.className = "form-status error";
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function setMobileSection(section) {
  const changed = state.mobileSection !== section;
  state.mobileSection = section;
  document.body.dataset.mobileSection = section;
  document.querySelectorAll(".mobile-nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mobileSection === section);
  });
  if (state.started && changed && section === "messages") refreshAll();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

let searchTimer = null;
function updateMessageSearch(event) {
  state.search = event.target.value.trim();
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(refreshAll, 250);
}

function updateCharCount() {
  const input = $("textInput");
  if (!input) return;
  const count = input.value.length;
  const counter = $("charCount");
  if (counter) {
    counter.textContent = `${count}/70`;
    counter.className = count > 65 ? "pill warn" : "pill muted";
  }
}

function openConfirm(to, text) {
  state.pendingSend = { to, text };
  setText("confirmTo", to);
  setText("confirmText", text);
  $("confirmModal").hidden = false;
}

function closeConfirm() {
  state.pendingSend = null;
  $("confirmModal").hidden = true;
}

async function sendPending() {
  if (!state.pendingSend) return;
  const payload = state.pendingSend;
  const btn = $("confirmSend");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/send", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showResult(data.ok ? `短信已提交发送至：${data.to || payload.to}` : "模块未确认发送", data.ok);
    if ($("sendForm")) $("sendForm").reset();
    updateCharCount();
    closeConfirm();
    refreshAll();
  } catch (error) {
    showError(error.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setupAutoRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  const auto = $("autoRefresh");
  if (auto && auto.checked) {
    state.refreshTimer = window.setInterval(refreshAll, 10000);
  }
}

function bindEvents() {
  if ($("refreshBtn")) $("refreshBtn").addEventListener("click", refreshAll);
  if ($("autoRefresh")) $("autoRefresh").addEventListener("change", setupAutoRefresh);
  if ($("developerModeToggle")) {
    $("developerModeToggle").addEventListener("change", (event) => {
      setDeveloperMode(event.target.checked);
      refreshAll();
    });
  }
  if ($("autostartToggle")) $("autostartToggle").addEventListener("change", (event) => setAutostart(event.target.checked));
  if ($("forwardingSettingsBtn")) $("forwardingSettingsBtn").addEventListener("click", openForwarding);
  if ($("forwardingForm")) $("forwardingForm").addEventListener("submit", saveForwarding);
  if ($("cancelForwarding")) $("cancelForwarding").addEventListener("click", closeForwarding);
  if ($("testForwarding")) $("testForwarding").addEventListener("click", testForwarding);
  if ($("textInput")) $("textInput").addEventListener("input", updateCharCount);
  if ($("messageSearch")) $("messageSearch").addEventListener("input", updateMessageSearch);
  if ($("pairForm")) $("pairForm").addEventListener("submit", submitPair);
  if ($("backToLoginButton")) $("backToLoginButton").addEventListener("click", backToLogin);
  if ($("mobileAccountButton")) $("mobileAccountButton").addEventListener("click", openMobileAccount);
  if ($("mobileAccountForm")) $("mobileAccountForm").addEventListener("submit", saveMobileAccount);
  if ($("cancelMobileAccount")) $("cancelMobileAccount").addEventListener("click", closeMobileAccount);

  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      setView(button.dataset.view);
      refreshAll();
    });
  });
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.addEventListener("click", () => setMessageBox(button.dataset.box));
  });
  document.querySelectorAll(".mobile-nav-button").forEach((button) => {
    button.addEventListener("click", () => setMobileSection(button.dataset.mobileSection));
  });

  if ($("sendForm")) {
    $("sendForm").addEventListener("submit", (event) => {
      event.preventDefault();
      const to = $("toInput").value.trim();
      const text = $("textInput").value.trim();
      if (!to) return showError("接收号码不能为空");
      if (!text) return showError("短信内容不能为空");
      openConfirm(to, text);
    });
  }

  if ($("selectAllCheckbox")) {
    $("selectAllCheckbox").addEventListener("change", (e) => toggleSelectAll(e.target.checked));
  }
  if ($("batchDeleteBtn")) {
    $("batchDeleteBtn").addEventListener("click", batchDelete);
  }
  if ($("cancelSend")) $("cancelSend").addEventListener("click", closeConfirm);
  if ($("confirmSend")) $("confirmSend").addEventListener("click", sendPending);
  if ($("confirmModal")) {
    $("confirmModal").addEventListener("click", (event) => {
      if (event.target === $("confirmModal")) closeConfirm();
    });
  }
  if ($("forwardingModal")) {
    $("forwardingModal").addEventListener("click", (event) => {
      if (event.target === $("forwardingModal")) closeForwarding();
    });
  }
  if ($("mobileAccountModal")) {
    $("mobileAccountModal").addEventListener("click", (event) => {
      if (event.target === $("mobileAccountModal")) closeMobileAccount();
    });
  }
}

function startApp() {
  if (state.started) return;
  state.started = true;
  setupAutoRefresh();
  updateCharCount();
  loadDeveloperMode();
  setView("messages");
  setMobileSection("messages");
  loadAutostart();
  loadForwarding();
  refreshAll();
}

async function initialize() {
  bindEvents();
  document.body.dataset.mobileSection = "messages";
  if (await loadMobileAccess()) startApp();
}

initialize();
