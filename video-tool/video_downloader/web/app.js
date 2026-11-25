"use strict";

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  idle: "空闲",
  queued: "排队中",
  probing: "探测中",
  downloading: "下载中",
  merging: "合并中",
  done: "已完成",
  failed: "失败",
  incomplete: "下载不完整",
  cancelled: "已取消",
};

let polling = false;
let pollTimer = null;
const noticeTimers = new Map();
let systemProxy = {};
let proxyTesting = false;
let toolsAvailable = null;
let allowLocalInput = false;
let taskFilter = "all";
let currentJobs = [];
const cards = new Map();
const expanded = new Set();
const loadingLogs = new Set();
const retrying = new Set();
const batchNames = new Map();
let batchFields = [];
let submitting = false;
let settingsInitialized = false;

async function api(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* non-JSON response */
  }
  if (!res.ok) {
    throw new Error((data && data.error) || `请求失败（HTTP ${res.status}）`);
  }
  return data;
}

function formatSize(bytes) {
  const n = Number(bytes);
  if (!n || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatTime(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return "—";
  }
  const total = Math.max(0, Math.floor(Number(seconds)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function showNotice(message, isError = false, id = "notice") {
  const box = $(id);
  hideNotice(id);
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
  if (!isError) {
    noticeTimers.set(id, setTimeout(() => hideNotice(id), 5000));
  }
}

function hideNotice(id = "notice") {
  $(id).hidden = true;
  clearTimeout(noticeTimers.get(id));
  noticeTimers.delete(id);
}

function renderTools(data) {
  const info = $("tool-info");
  const error = $("tools-error");
  const status = $("tool-status");
  toolsAvailable = Boolean(data.tools);
  status.classList.toggle("ready", toolsAvailable);
  status.textContent = toolsAvailable ? "FFmpeg 就绪" : "需要配置 FFmpeg";

  if (data.tools) {
    info.textContent = `${data.tools.ffmpeg}  ·  ${data.tools.ffprobe}`;
    error.hidden = true;
    if (!$("ffmpeg-path").dataset.init) {
      $("ffmpeg-path").value = data.tools.ffmpeg;
      $("ffmpeg-path").dataset.init = "1";
    }
  } else {
    info.textContent = "未检测到 ffmpeg / ffprobe";
    error.hidden = false;
    error.textContent = data.tools_error || "请在下方填写 ffmpeg 路径，或先运行 check_env.py";
  }
}

function proxyMode() {
  return $("proxy-mode").value;
}

function proxyValue() {
  const mode = proxyMode();
  if (mode === "custom") return $("proxy-url").value.trim();
  if (mode === "direct") return "direct";
  return "";
}

function saveProxyPrefs() {
  try {
    localStorage.setItem("vt.proxyMode", proxyMode());
    localStorage.setItem("vt.proxyUrl", $("proxy-url").value.trim());
  } catch (_) {
    /* localStorage may be unavailable */
  }
}

function updateProxyUI() {
  const mode = proxyMode();
  const custom = mode === "custom";
  const url = $("proxy-url").value.trim();
  $("proxy-url").hidden = !custom;
  $("custom-proxy").hidden = !custom;
  $("test-proxy").disabled = proxyTesting || !custom || !url;

  let text = "";
  if (mode === "inherit") {
    text = systemProxy.env_http
      ? `跟随环境：ffmpeg 将使用 http_proxy=${systemProxy.env_http}`
      : "跟随环境：未检测到 http_proxy；ffmpeg 不会自动使用系统代理";
  } else if (mode === "direct") {
    text = "直连：清除代理环境变量；Clash TUN 模式仍可能接管流量";
  } else {
    text = url
      ? `指定代理：${url}（ffmpeg 与 ffprobe 都使用）`
      : "指定代理：请填写 http://host:port";
  }
  const summary = $("proxy-summary");
  summary.textContent = text;
  summary.classList.toggle("warn", custom && !url);
}

function renderSystemProxy(data) {
  systemProxy = (data && data.system_proxy) || {};
  const system = systemProxy.system || {};
  const env = systemProxy.env_http || "";
  const label = $("system-proxy");
  const useButton = $("use-system-proxy");

  if (system.http) {
    label.textContent = `系统代理：${system.http}（ffmpeg 默认不使用）`;
    useButton.hidden = false;
    useButton.dataset.proxy = system.http;
  } else if (system.pac) {
    label.textContent = `系统代理：PAC ${system.pac}`;
    useButton.hidden = true;
  } else if (env) {
    label.textContent = `环境变量 http_proxy：${env}`;
    useButton.hidden = true;
  } else {
    label.textContent = "未检测到系统代理或 http_proxy 环境变量";
    useButton.hidden = true;
  }
  updateProxyUI();
}

function syncProxyInput(focus = false) {
  const custom = proxyMode() === "custom";
  $("proxy-url").hidden = !custom;
  if (custom && focus) $("proxy-url").focus();
  updateProxyUI();
}

async function testProxy() {
  const proxy = $("proxy-url").value.trim();
  if (!proxy) {
    showNotice("请先填写代理地址，例如 http://127.0.0.1:7890", true);
    return;
  }
  const button = $("test-proxy");
  const feedback = $("proxy-feedback");
  feedback.hidden = false;
  feedback.classList.remove("warn");
  feedback.textContent = "正在检查代理端口…";
  proxyTesting = true;
  button.disabled = true;
  button.textContent = "测试中…";
  try {
    const data = await api("/api/video_downloader/test_proxy", { proxy });
    if ($("proxy-url").value.trim() === proxy) {
      feedback.textContent = `连接成功 · ${data.ms} ms（仅检查端口连通性）`;
    }
  } catch (err) {
    if ($("proxy-url").value.trim() === proxy) {
      feedback.textContent = err.message;
      feedback.classList.add("warn");
    }
  } finally {
    proxyTesting = false;
    button.textContent = "测试连接";
    updateProxyUI();
  }
}

function urlIssue(url) {
  if (!url || url.length > 8192 || /\s/.test(url)) return "无效的视频地址";
  try {
    const parsed = new URL(url);
    if (allowLocalInput && parsed.protocol === "file:" && parsed.pathname) return "";
    if (/^https?:\/\/[^/]/i.test(url) && parsed.hostname && parsed.port !== "0") return "";
  } catch (_) {
    return "无效的视频地址";
  }
  return allowLocalInput ? "请填写 HTTP、HTTPS 或 file 地址" : "请填写 HTTP 或 HTTPS 地址";
}

function draftRows() {
  const occurrences = new Map();
  let validIndex = 0;
  return $("url").value.split(/\r?\n/).flatMap((text, index) => {
    const url = text.trim();
    if (!url) return [];
    const occurrence = occurrences.get(url) || 0;
    occurrences.set(url, occurrence + 1);
    const issue = urlIssue(url);
    return [{
      url, line: index + 1, key: JSON.stringify([url, occurrence]),
      issue, duplicate: !issue && occurrence > 0,
      validIndex: !issue && occurrence === 0 ? validIndex++ : null,
    }];
  });
}

function displaySource(url) {
  try {
    const parsed = new URL(url);
    return parsed.host + parsed.pathname;
  } catch (_) {
    return url;
  }
}

function syncUrlState() {
  const rows = draftRows();
  const activeKeys = new Set(rows.map((row) => row.key));
  for (const key of batchNames.keys()) {
    if (!activeKeys.has(key)) batchNames.delete(key);
  }

  $("batch-heading").hidden = rows.length === 0;
  const container = $("batch-filenames");
  container.hidden = rows.length === 0;
  container.replaceChildren();
  batchFields = rows.slice(0, 50).map((row) => {
    const field = document.createElement("div");
    field.className = "batch-item";
    const label = document.createElement("label");
    const source = document.createElement("p");
    source.className = "hint batch-source";
    source.textContent = displaySource(row.url);
    source.title = source.textContent;
    const input = document.createElement("input");
    input.id = `batch-name-${row.line}`;
    input.type = "text";
    input.autocomplete = "off";
    input.placeholder = "填写文件名，留空自动命名";
    input.value = batchNames.get(row.key) || "";
    input.addEventListener("input", () => {
      batchNames.set(row.key, input.value);
      updateNamePreviews();
    });
    const preview = document.createElement("p");
    preview.className = "name-preview";
    preview.id = `batch-preview-${row.line}`;
    input.setAttribute("aria-describedby", preview.id);
    field.append(label, source, input, preview);
    container.append(field);
    return { row, label, input, preview };
  });
  syncNamingUI();
}

function namingMode() {
  return document.querySelector('input[name="naming-mode"]:checked').value;
}

function namingPayload(rows) {
  const mode = namingMode();
  return {
    filename: mode === "prefix" ? $("filename").value.trim() : "",
    filenames: rows.map((row) => mode === "individual" ? (batchNames.get(row.key) || "").trim() : ""),
  };
}

function syncNamingUI() {
  const mode = namingMode();
  $("prefix-field").hidden = mode !== "prefix";
  $("prefix-hint").textContent = draftRows().filter((row) => row.validIndex !== null).length > 1
    ? "按有效地址顺序编号，例如：课程_01.mp4、课程_02.mp4。"
    : "单个视频直接使用此前缀作为文件名，例如：课程.mp4。";
  $("naming-help").textContent = {
    auto: "根据视频地址自动生成文件名。",
    prefix: "为本次视频统一命名。",
    individual: "留空的项目仍自动命名。",
  }[mode];
  $("batch-title").textContent = mode === "individual" ? "填写视频文件名" : "待提交地址";
  for (const { row, label, input } of batchFields) {
    input.hidden = mode !== "individual" || row.duplicate;
    label.textContent = input.hidden ? `第 ${row.line} 行` : `第 ${row.line} 行文件名`;
    if (input.hidden) label.removeAttribute("for");
    else label.htmlFor = input.id;
  }
  updateNamePreviews();
  updateSubmitState();
}

function outputName(name, url, index = null) {
  let base = name.trim();
  if (!base) {
    try { base = new URL(url).pathname.split("/").pop() || ""; } catch (_) { /* incomplete address */ }
    if (!base.includes(".")) return "自动命名";
  }
  base = base.split(/[\\/]/).pop().replace(/[<>:\"/\\|?*]/g, "_");
  base = base.replace(/\.(m3u8|m3u|mpd)$/i, ".mp4");
  if (!base || base === "." || base === "..") return "自动命名";
  if (!/\.(mp4|mkv|mov|m4v)$/i.test(base)) base += ".mp4";
  if (index !== null) base = base.replace(/(\.[^.]+)$/, `_${String(index + 1).padStart(2, "0")}$1`);
  return base;
}

function updateNamePreviews() {
  const rows = draftRows();
  const accepted = rows.filter((row) => row.validIndex !== null);
  const mode = namingMode();
  const prefix = mode === "prefix" ? $("filename").value.trim() : "";
  for (const { row, input, preview } of batchFields) {
    const own = mode === "individual" ? input.value.trim() : "";
    const index = prefix && accepted.length > 1 ? row.validIndex : null;
    const name = outputName(own || prefix, row.url, index);
    preview.classList.toggle("invalid", Boolean(row.issue));
    preview.classList.toggle("duplicate", row.duplicate);
    if (row.issue) preview.textContent = row.issue;
    else if (row.duplicate) preview.textContent = "与前面的地址重复，本次跳过";
    else if (mode === "prefix" && !prefix) preview.textContent = "填写前缀后显示文件名";
    else preview.textContent = `预计文件名 · ${name}`;
  }
}

function updateSubmitState() {
  const rows = draftRows();
  const valid = rows.filter((row) => row.validIndex !== null).length;
  const invalid = rows.filter((row) => row.issue).length;
  const duplicates = rows.length - valid - invalid;
  const missingPrefix = namingMode() === "prefix" && !$("filename").value.trim();
  $("url-count").textContent = rows.length
    ? [`${valid} 个有效`, invalid && `${invalid} 个无效`, duplicates && `${duplicates} 个重复`].filter(Boolean).join(" · ")
    : "0 个地址";
  $("start").disabled = submitting || !valid || rows.length > 50 || missingPrefix || toolsAvailable === false;
  $("start").textContent = submitting ? "正在创建任务…" : valid > 1 ? `下载 ${valid} 个视频` : "开始下载";
  if (toolsAvailable === false) $("submit-summary").textContent = "请先配置 FFmpeg";
  else if (rows.length > 50) $("submit-summary").textContent = "一次最多添加 50 行";
  else if (!rows.length) $("submit-summary").textContent = "先添加视频地址";
  else if (!valid) $("submit-summary").textContent = "请修正无效地址";
  else if (missingPrefix) $("submit-summary").textContent = "请填写统一前缀";
  else $("submit-summary").textContent = `可提交 ${valid} 个视频`;
}

function basename(path) {
  if (!path) return "";
  const parts = String(path).split(/[\\/]/);
  return parts[parts.length - 1] || String(path);
}

function makeButton(text, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  return button;
}

function createCard(job) {
  const root = document.createElement("article");
  root.className = "task";
  root.dataset.id = job.id;

  const top = document.createElement("div");
  top.className = "task-top";
  const title = document.createElement("div");
  title.className = "task-title";
  const name = document.createElement("div");
  name.className = "task-name";
  const nameText = document.createElement("span");
  const badge = document.createElement("span");
  badge.className = "badge";
  name.append(nameText, badge);
  const sub = document.createElement("div");
  sub.className = "task-sub";
  const sourceRow = document.createElement("div");
  sourceRow.className = "task-source";
  const sourceLabel = document.createElement("span");
  sourceLabel.textContent = "下载链接：";
  const sourceDetails = document.createElement("details");
  sourceDetails.className = "task-url";
  const sourceSummary = document.createElement("summary");
  const fullSource = document.createElement("div");
  fullSource.className = "task-url-full";
  sourceDetails.append(sourceSummary, fullSource);
  const copyUrlBtn = makeButton("复制", "link task-copy");
  copyUrlBtn.setAttribute("aria-label", "复制下载链接");
  copyUrlBtn.addEventListener("click", async () => {
    const url = currentJobs.find((item) => item.id === job.id)?.url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      taskFeedback(job.id, "下载链接已复制");
    } catch (_) {
      sourceDetails.open = true;
      taskFeedback(job.id, "无法自动复制，请展开链接后手动复制。", true);
    }
  });
  sourceRow.append(sourceLabel, sourceDetails, copyUrlBtn);
  title.append(name, sub, sourceRow);

  const actions = document.createElement("div");
  actions.className = "task-actions";
  const retryBtn = makeButton("重试", "primary small");
  const editBtn = makeButton("编辑后重试", "ghost small");
  const cancelBtn = makeButton("取消", "ghost small");
  const openBtn = makeButton("打开目录", "ghost small");
  const removeBtn = makeButton("移除", "ghost small");
  const logBtn = makeButton("日志", "link");
  const restartBtn = makeButton("重新开始", "link task-restart");
  actions.append(retryBtn, editBtn, cancelBtn, openBtn, removeBtn, logBtn, restartBtn);
  top.append(title);

  const bar = document.createElement("div");
  bar.className = "bar";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", "下载进度");
  const fill = document.createElement("div");
  fill.className = "bar-fill";
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "task-meta";
  const message = document.createElement("div");
  message.className = "task-message";
  const feedback = document.createElement("p");
  feedback.className = "task-feedback";
  feedback.setAttribute("role", "status");
  feedback.hidden = true;
  const editor = document.createElement("div");
  const log = document.createElement("pre");
  log.className = "log task-log";
  log.hidden = true;
  log.setAttribute("aria-live", "polite");

  root.append(top, bar, meta, message, actions, feedback, editor, log);

  cancelBtn.addEventListener("click", () => cancelJob(job.id));
  retryBtn.addEventListener("click", () => retryJob(job.id));
  editBtn.addEventListener("click", () => prepareRetry(job.id));
  restartBtn.addEventListener("click", () => restartJob(job.id));
  openBtn.addEventListener("click", () => openTaskFolder(job));
  removeBtn.addEventListener("click", () => removeJob(job.id));
  logBtn.addEventListener("click", () => toggleLogs(job.id));

  const refs = { root, nameText, badge, sub, sourceRow, sourceSummary, fullSource, copyUrlBtn, bar, fill, meta, message, feedback, editor, log, retryBtn, editBtn, restartBtn, cancelBtn, openBtn, removeBtn, logBtn };
  cards.set(job.id, refs);
  return refs;
}

function updateCard(job) {
  const refs = cards.get(job.id) || createCard(job);
  if (refs.finishedAt !== job.finished_at) refs.logsComplete = false;
  refs.finishedAt = job.finished_at;
  refs.nameText.textContent = basename(job.output) || "（未命名）";
  refs.sub.textContent = job.output ? `落盘位置：${job.output}` : "";
  refs.sub.title = job.output || "";
  refs.sourceRow.hidden = !job.url;
  refs.sourceSummary.textContent = job.url ? displaySource(job.url) : "";
  refs.fullSource.textContent = job.url || "";
  refs.copyUrlBtn.disabled = !job.url;

  const label = STATUS_TEXT[job.status] || job.status;
  refs.badge.textContent = label;
  const badgeClass = job.running
    ? "running"
    : job.status === "done"
      ? "done"
      : ["failed", "incomplete"].includes(job.status)
        ? "failed"
        : job.status === "cancelled"
          ? "cancelled"
          : "";
  refs.badge.className = `badge ${badgeClass}`.trim();

  if (job.percent !== null && job.percent !== undefined) {
    const pct = Math.max(0, Math.min(job.status === "done" ? 100 : 99, Number(job.percent)));
    refs.fill.style.width = `${pct}%`;
    refs.bar.setAttribute("aria-valuenow", pct.toFixed(1));
    refs.fill.classList.remove("indeterminate");
  } else if (job.running) {
    refs.fill.style.width = "";
    refs.bar.removeAttribute("aria-valuenow");
    refs.fill.classList.add("indeterminate");
  } else {
    refs.fill.style.width = "0";
    refs.bar.removeAttribute("aria-valuenow");
    refs.fill.classList.remove("indeterminate");
  }

  const bits = [];
  if (job.percent !== null && job.percent !== undefined) {
    const pct = Math.max(0, Math.min(job.status === "done" ? 100 : 99, Number(job.percent)));
    bits.push(
      `${pct.toFixed(1)}%  ${formatTime(job.out_time)}${
        job.total_time ? " / " + formatTime(job.total_time) : ""
      }`,
    );
  } else if (job.running) {
    bits.push(`${formatTime(job.out_time)} / 未知`);
  }
  if (job.bytes_per_second !== undefined && job.status === "downloading") bits.push(`速度 ${formatSize(job.bytes_per_second) || "0 B"}/s`);
  else if (job.speed && job.status === "downloading") bits.push(`处理倍率 ${job.speed}`);
  if (job.size) bits.push(`本次传输 ${formatSize(job.size)}`);
  if (job.elapsed) bits.push(`已耗时 ${formatTime(job.elapsed)}`);
  if (job.eta_seconds !== null && job.eta_seconds !== undefined) bits.push(`预计剩余约 ${formatTime(job.eta_seconds)}`);
  if (job.idle_seconds >= 10) bits.push(`${Math.floor(job.idle_seconds)} 秒没有新进展`);
  if (job.part) bits.push(`分段 ${job.part}`);
  if (job.strict_errors !== undefined) bits.push(job.strict_errors ? "严格校验" : "容错处理");
  bits.push(job.proxy === "direct" ? "代理 直连" : job.proxy ? `代理 ${job.proxy}` : "代理 跟随环境");
  const meta = bits.join("  ·  ");
  if (refs.meta.textContent !== meta) refs.meta.textContent = meta;

  refs.message.textContent = job.status === "queued" && job.queue_position ? `排队第 ${job.queue_position} 位，可随时取消` : job.message || "";
  refs.message.classList.toggle("failed", ["failed", "incomplete"].includes(job.status));

  refs.cancelBtn.hidden = !job.running;
  refs.retryBtn.hidden = job.running || !["failed", "incomplete", "cancelled"].includes(job.status);
  refs.retryBtn.disabled = retrying.has(job.id);
  refs.retryBtn.textContent = retrying.has(job.id) ? "提交中…" : job.status === "cancelled" ? "继续下载" : "重试";
  refs.editBtn.hidden = refs.retryBtn.hidden;
  refs.editBtn.disabled = retrying.has(job.id);
  refs.restartBtn.hidden = !!job.running;
  refs.restartBtn.disabled = retrying.has(job.id);
  refs.removeBtn.hidden = !!job.running;
  refs.openBtn.hidden = !job.output;
  const open = expanded.has(job.id);
  refs.log.hidden = !open;
  refs.logBtn.textContent = open ? "收起日志" : "日志";
  refs.logBtn.setAttribute("aria-expanded", String(open));
  return refs;
}

function matchesFilter(job) {
  if (taskFilter === "all") return true;
  if (taskFilter === "active") return job.running;
  if (taskFilter === "failed") return ["failed", "incomplete"].includes(job.status);
  return job.status === taskFilter;
}

function renderJobs(jobs) {
  currentJobs = jobs;
  const list = $("task-list");
  const seen = new Set();
  const active = document.activeElement;
  const canReorder = !active || !list.contains(active);
  const currentOrder = [...list.children].map((element) => element.dataset.id).join("|");
  const desiredOrder = jobs.map((job) => job.id).join("|");
  const reorder = canReorder && currentOrder !== desiredOrder;

  for (const job of jobs) {
    seen.add(job.id);
    const refs = updateCard(job);
    refs.root.hidden = !matchesFilter(job);
    if (!refs.root.isConnected) {
      list.appendChild(refs.root);
    } else if (reorder) {
      list.appendChild(refs.root);
    }
  }

  for (const [id, refs] of [...cards.entries()]) {
    if (!seen.has(id)) {
      refs.root.remove();
      cards.delete(id);
      expanded.delete(id);
      loadingLogs.delete(id);
    }
  }

  $("task-empty").hidden = jobs.some(matchesFilter);
  $("task-empty").textContent = jobs.length
    ? "当前筛选下没有任务。"
    : "还没有任务。添加视频地址后，进度会显示在这里。";
  const running = jobs.filter((job) => job.running).length;
  const queued = jobs.filter((job) => job.status === "queued").length;
  const done = jobs.filter((job) => !job.running && job.status === "done").length;
  const failed = jobs.filter((job) => ["failed", "incomplete"].includes(job.status)).length;
  $("view-tasks").hidden = jobs.length === 0;
  $("cancel-all").disabled = running === 0;
  $("clear-finished").disabled = done === 0;
  for (const button of $("task-filters").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.filter === taskFilter));
  }
  $("task-counts").textContent = jobs.length
    ? `运行 ${running - queued} · 等待 ${queued} · 完成 ${done} · 失败 ${failed}`
    : "暂无任务";
}

async function loadLogs(id) {
  if (!expanded.has(id) || loadingLogs.has(id)) return;
  const refs = cards.get(id);
  if (!refs || refs.root.hidden || refs.logsComplete) return;
  const running = currentJobs.some((job) => job.id === id && job.running);
  loadingLogs.add(id);
  try {
    const data = await api("/api/video_downloader/logs", { id });
    refs.logsComplete = !running;
    const text = (data.logs || []).join("\n");
    if (refs.log.textContent !== text) {
      const nearBottom = refs.log.scrollTop + refs.log.clientHeight >= refs.log.scrollHeight - 24;
      refs.log.textContent = text;
      if (nearBottom) refs.log.scrollTop = refs.log.scrollHeight;
    }
  } catch (_) {
    /* task may have been removed */
  } finally {
    loadingLogs.delete(id);
  }
}

function toggleLogs(id) {
  if (expanded.has(id)) {
    expanded.delete(id);
  } else {
    expanded.add(id);
  }
  const refs = cards.get(id);
  if (refs) {
    const open = expanded.has(id);
    refs.log.hidden = !open;
    refs.logBtn.textContent = open ? "收起日志" : "日志";
    refs.logBtn.setAttribute("aria-expanded", String(open));
  }
  if (expanded.has(id)) loadLogs(id);
}

async function refresh() {
  if (polling) return;
  clearTimeout(pollTimer);
  polling = true;
  try {
    const data = await api("/api/video_downloader/state");
    const previousTools = toolsAvailable;
    $("connection-status").hidden = true;
    if (!settingsInitialized && data.config && data.config.max_concurrent !== undefined) {
      $("max-concurrent").value = String(data.config.max_concurrent);
      $("hls-workers").value = String(data.config.hls_workers);
      $("strict-errors").checked = data.config.strict_errors;
      $("native-hls").checked = data.config.native_hls;
      settingsInitialized = true;
    }
    renderTools(data);
    renderSystemProxy(data);
    renderJobs(data.jobs || []);
    const localInputEnabled = Boolean(data.config?.allow_local_input);
    if (localInputEnabled !== allowLocalInput) {
      allowLocalInput = localInputEnabled;
      syncUrlState();
    } else if (previousTools !== toolsAvailable) {
      updateSubmitState();
    }

    const outDir = $("out-dir");
    if (!outDir.dataset.init && data.config) {
      outDir.value = data.config.output_dir || "";
      outDir.dataset.init = "1";
    }
    if (!document.hidden) for (const id of expanded) loadLogs(id);
  } catch (err) {
    const status = $("connection-status");
    status.textContent = "暂时无法连接本地服务，正在重试…";
    status.hidden = false;
    console.error(err);
  } finally {
    polling = false;
    const active = currentJobs.some((job) => job.running);
    pollTimer = setTimeout(refresh, document.hidden ? 15000 : active ? 800 : 5000);
  }
}

function downloadSettings() {
  return {
    max_concurrent: Number($("max-concurrent").value),
    hls_workers: Number($("hls-workers").value),
    strict_errors: $("strict-errors").checked,
    native_hls: $("native-hls").checked,
  };
}

function showBatchResult(rows, results) {
  const list = $("batch-result");
  const labels = { created: "已新建", reused: "已复用", duplicate: "已跳过重复地址", failed: "提交失败", invalid: "地址无效" };
  list.replaceChildren(...results.map((result, index) => {
    const row = rows[index];
    const item = document.createElement("li");
    item.className = result.status;
    item.textContent = [`第 ${row.line} 行`, labels[result.status], result.output || result.error].filter(Boolean).join(" · ");
    return item;
  }));
  const outcome = $("batch-outcome");
  outcome.hidden = false;
  outcome.open = true;
}

async function startDownload() {
  if (submitting || $("start").disabled) return;
  hideNotice();
  $("batch-outcome").hidden = true;
  const rows = draftRows();
  const accepted = rows.filter((row) => row.validIndex !== null);
  if (!accepted.length) return;
  const submittedText = $("url").value;
  const submittedNamingMode = namingMode();
  const submittedNames = namingPayload(rows);
  const proxy = proxyValue();
  if (proxyMode() === "custom" && !proxy) {
    showNotice("请填写代理地址，例如 http://127.0.0.1:7890", true);
    $("download-settings").open = true;
    $("proxy-url").focus();
    return;
  }

  const body = {
    urls: accepted.map((row) => row.url),
    out_dir: $("out-dir").value.trim(),
    ...namingPayload(accepted),
    headers: $("headers").value,
    proxy,
    include_results: true,
  };
  const settings = downloadSettings();
  submitting = true;
  updateSubmitState();
  try {
    await api("/api/video_downloader/settings", settings);
    const data = await api("/api/video_downloader/start_batch", body);
    const submittedResults = new Map(data.results.map((result) => [result.index, result]));
    const results = rows.map((row) => {
      if (row.issue) return { status: "invalid", error: row.issue };
      if (row.duplicate) return { status: "duplicate" };
      return submittedResults.get(row.validIndex);
    });
    showBatchResult(rows, results);
    const created = results.filter(({ status }) => status === "created").length;
    const reused = results.filter(({ status }) => status === "reused").length;
    const drafts = rows.filter((row, index) => ["failed", "invalid"].includes(results[index].status));
    const skipped = results.filter(({ status }) => status === "duplicate").length;
    showNotice([`新建 ${created} 个`, reused && `复用 ${reused} 个`, skipped && `跳过 ${skipped} 个重复地址`, drafts.length && `${drafts.length} 行待修正或重试`].filter(Boolean).join(" · "), drafts.length > 0);
    if (data.jobs.length) taskFilter = "all";

    const namesUnchanged = namingMode() === submittedNamingMode && JSON.stringify(namingPayload(rows)) === JSON.stringify(submittedNames);
    if ($("url").value === submittedText && namesUnchanged) {
      const names = drafts.map((row) => {
        const own = batchNames.get(row.key);
        if (own) return own;
        if (body.filename && row.validIndex !== null) {
          return accepted.length > 1 ? outputName(body.filename, row.url, row.validIndex) : body.filename;
        }
        return "";
      });
      $("url").value = drafts.map((row) => row.url).join("\n");
      batchNames.clear();
      draftRows().forEach((row, index) => batchNames.set(row.key, names[index]));
      if (names.some(Boolean)) {
        document.querySelector('input[name="naming-mode"][value="individual"]').checked = true;
      }
      syncUrlState();
    }
    await refresh();
  } catch (err) {
    showNotice(err.message, true);
  } finally {
    submitting = false;
    updateSubmitState();
  }
}

function taskFeedback(id, message, isError = false) {
  const feedback = cards.get(id)?.feedback;
  if (!feedback) return;
  feedback.textContent = message;
  feedback.classList.toggle("failed", isError);
  feedback.hidden = !message;
}

async function retryJob(id, options = {}) {
  if (retrying.has(id)) return false;
  retrying.add(id);
  const refs = cards.get(id);
  const job = currentJobs.find((item) => item.id === id);
  if (job) updateCard(job);
  taskFeedback(id, "");
  try {
    const data = await api("/api/video_downloader/retry", { id, settings: downloadSettings(), ...options });
    if (refs) {
      refs.editor.replaceChildren();
      refs.logsComplete = false;
    }
    if (data.job) {
      if (!matchesFilter(data.job)) taskFilter = "all";
      renderJobs(currentJobs.map((item) => item.id === id ? data.job : item));
    }
    taskFeedback(id, options.restart ? "已从头开始" : "已提交重试，可用的分片会继续复用");
    return true;
  } catch (error) {
    taskFeedback(id, error.message, true);
    return false;
  } finally {
    retrying.delete(id);
    const current = currentJobs.find((item) => item.id === id);
    if (current) updateCard(current);
    await refresh();
  }
}

async function prepareRetry(id) {
  const job = currentJobs.find((item) => item.id === id);
  const refs = cards.get(id);
  if (!job || job.running || !refs) return;
  if (refs.editor.firstElementChild) {
    refs.editor.querySelector('[name="url"]').focus();
    return;
  }
  refs.editBtn.disabled = true;
  taskFeedback(id, "");
  try {
    const details = await api("/api/video_downloader/edit_details", { id });
    const form = $("retry-template").content.firstElementChild.cloneNode(true);
    const urlField = form.elements.namedItem("url");
    const headersField = form.elements.namedItem("headers");
    const proxyField = form.elements.namedItem("proxy");
    form.querySelector(".retry-target").textContent = "保存位置 · " + details.output;
    urlField.value = details.url;
    headersField.value = details.headers;
    proxyField.value = details.proxy;
    form.querySelector(".close-editor").addEventListener("click", () => {
      refs.editor.replaceChildren();
      taskFeedback(id, "");
      refs.editBtn.focus();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const url = urlField.value.trim();
      const issue = urlIssue(url);
      if (issue) {
        taskFeedback(id, issue, true);
        urlField.focus();
        return;
      }
      const submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      await retryJob(id, { changes: { url, headers: headersField.value, proxy: proxyField.value.trim() } });
      submit.disabled = false;
    });
    refs.editor.append(form);
    urlField.focus();
  } catch (error) {
    taskFeedback(id, error.message, true);
  } finally {
    refs.editBtn.disabled = false;
  }
}

function restartJob(id) {
  const job = currentJobs.find((item) => item.id === id);
  if (!job || job.running) return;
  const confirmed = window.confirm("从头下载此任务？已有分片会被删除，下载成功后将覆盖同名文件。\n\n" + job.output);
  if (confirmed) retryJob(id, { restart: true });
}

async function cancelJob(id) {
  taskFeedback(id, "");
  try {
    await api("/api/video_downloader/cancel", { id });
    taskFeedback(id, "已请求取消，正在等待下载停止");
  } catch (err) {
    taskFeedback(id, err.message, true);
  }
  refresh();
}

async function cancelAll() {
  hideNotice("queue-notice");
  const running = currentJobs.filter((job) => job.running);
  if (!running.length) {
    showNotice("没有正在运行的任务", true, "queue-notice");
    return;
  }
  if (running.length > 1 && !window.confirm(`取消 ${running.length} 个运行中的任务？已下载的分段会保留。`)) {
    return;
  }
  try {
    const data = await api("/api/video_downloader/cancel_all");
    showNotice(`已请求取消 ${data.cancelled} 个任务`, false, "queue-notice");
  } catch (err) {
    showNotice(err.message, true, "queue-notice");
  }
  refresh();
}

async function clearFinished() {
  hideNotice("queue-notice");
  const finished = currentJobs.filter((job) => !job.running && job.status === "done").length;
  if (!finished) {
    showNotice("没有已完成的任务", true, "queue-notice");
    return;
  }
  if (!window.confirm(`清除 ${finished} 条已完成任务记录？已下载的文件会保留。`)) {
    return;
  }
  try {
    const data = await api("/api/video_downloader/clear_finished", { completed_only: true });
    showNotice(`已清除 ${data.removed} 条任务记录`, false, "queue-notice");
  } catch (err) {
    showNotice(err.message, true, "queue-notice");
  }
  refresh();
}

async function removeJob(id) {
  taskFeedback(id, "");
  if (!window.confirm("移除这条任务记录？文件不会被删除。")) return;
  try {
    await api("/api/video_downloader/remove", { id });
  } catch (err) {
    taskFeedback(id, err.message, true);
  }
  refresh();
}

async function openTaskFolder(job) {
  taskFeedback(job.id, "");
  let path = job.output || "";
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  if (index > 0) path = path.slice(0, index);
  try {
    await api("/api/video_downloader/open_folder", { path });
  } catch (err) {
    taskFeedback(job.id, err.message, true);
  }
}

$("start").addEventListener("click", startDownload);
$("cancel-all").addEventListener("click", cancelAll);
$("clear-finished").addEventListener("click", clearFinished);
$("task-filters").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  taskFilter = button.dataset.filter;
  renderJobs(currentJobs);
  for (const id of expanded) loadLogs(id);
});
$("view-tasks").addEventListener("click", (event) => {
  event.preventDefault();
  $("tasks").focus({ preventScroll: true });
  $("tasks").scrollIntoView({ block: "start" });
});
$("tool-status").addEventListener("click", () => {
  const tools = $("tool-settings");
  tools.open = true;
  tools.querySelector("summary").focus();
});
$("tool-settings").addEventListener("toggle", () => {
  $("tool-status").setAttribute("aria-expanded", String($("tool-settings").open));
});
$("select-dir").addEventListener("click", async () => {
  const button = $("select-dir");
  button.disabled = true;
  button.textContent = "选择中…";
  hideNotice();
  try {
    const data = await api("/api/video_downloader/select_folder", { path: $("out-dir").value.trim() });
    if (!data.cancelled) {
      $("out-dir").value = data.path;
      $("out-dir").dataset.init = "1";
      showNotice("保存目录已更新");
    }
  } catch (err) {
    showNotice(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "选择文件夹";
  }
});
$("open-dir").addEventListener("click", () => {
  hideNotice();
  api("/api/video_downloader/open_folder", { path: $("out-dir").value.trim() }).catch((err) =>
    showNotice(err.message, true),
  );
});
$("detect").addEventListener("click", detectFfmpeg);
$("test-proxy").addEventListener("click", testProxy);
$("url").addEventListener("input", () => {
  hideNotice();
  $("batch-outcome").open = false;
  syncUrlState();
});
$("filename").addEventListener("input", () => {
  updateNamePreviews();
  updateSubmitState();
});
$("save-settings").addEventListener("click", async () => {
  const button = $("save-settings");
  button.disabled = true;
  try {
    await api("/api/video_downloader/settings", downloadSettings());
    showNotice("设置已保存，并发上限已更新；其他设置用于新任务");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll('input[name="naming-mode"]').forEach((radio) => {
  radio.addEventListener("change", syncNamingUI);
});

async function detectFfmpeg() {
  hideNotice();
  const payload = { path: $("ffmpeg-path").value.trim() };
  if (!payload.path) payload.reset = true;
  try {
    const data = await api("/api/video_downloader/detect", payload);
    if (data.tools) {
      $("ffmpeg-path").value = data.tools.ffmpeg;
      showNotice(`已检测到 ffmpeg ${data.tools.version}`);
    }
    await refresh();
  } catch (err) {
    showNotice(err.message, true);
  }
}

$("proxy-mode").addEventListener("change", () => {
  $("proxy-feedback").hidden = true;
  saveProxyPrefs();
  syncProxyInput(true);
});
$("proxy-url").addEventListener("input", () => {
  $("proxy-feedback").hidden = true;
  saveProxyPrefs();
  updateProxyUI();
});
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("proxy-feedback").hidden = true;
    $("proxy-mode").value = "custom";
    $("proxy-url").value = `http://127.0.0.1:${chip.dataset.port}`;
    saveProxyPrefs();
    syncProxyInput(true);
  });
});
$("use-system-proxy").addEventListener("click", () => {
  $("proxy-feedback").hidden = true;
  const proxy = $("use-system-proxy").dataset.proxy;
  if (!proxy) return;
  $("proxy-mode").value = "custom";
  $("proxy-url").value = proxy;
  saveProxyPrefs();
  syncProxyInput(true);
});

try {
  const savedMode = localStorage.getItem("vt.proxyMode");
  const savedUrl = localStorage.getItem("vt.proxyUrl");
  if (["inherit", "direct", "custom"].includes(savedMode)) $("proxy-mode").value = savedMode;
  if (savedUrl) $("proxy-url").value = savedUrl;
} catch (_) {
  /* localStorage may be unavailable */
}

syncProxyInput();
syncUrlState();
document.addEventListener("visibilitychange", refresh);
refresh();
