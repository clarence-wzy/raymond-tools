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
let noticeTimer = null;
let systemProxy = {};
let proxyTesting = false;
let currentJobs = [];
const cards = new Map();
const expanded = new Set();
const loadingLogs = new Set();
const retrying = new Set();
const batchNames = new Map();
let batchNameInputs = [];
let batchPreviews = [];
let batchLabels = [];
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

function showNotice(message, isError = false) {
  const box = $("notice");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
  if (noticeTimer) clearTimeout(noticeTimer);
  if (!isError) {
    noticeTimer = setTimeout(() => {
      box.hidden = true;
    }, 5000);
  }
}

function hideNotice() {
  $("notice").hidden = true;
  if (noticeTimer) clearTimeout(noticeTimer);
}

function renderTools(data) {
  const info = $("tool-info");
  const error = $("tools-error");

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

function urls() {
  return $("url").value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function syncUrlState() {
  const list = urls();
  const count = list.length;
  $("url-count").textContent = `${count} 个地址`;
  $("batch-heading").hidden = count === 0;
  const container = $("batch-filenames");
  container.hidden = count === 0;
  container.replaceChildren();
  batchNameInputs = [];
  batchPreviews = [];
  batchLabels = [];
  const occurrences = new Map();
  const activeKeys = new Set();
  list.forEach((url, index) => {
    const occurrence = occurrences.get(url) || 0;
    occurrences.set(url, occurrence + 1);
    const key = JSON.stringify([url, occurrence]);
    activeKeys.add(key);
    const field = document.createElement("div");
    field.className = "batch-item";
    const label = document.createElement("label");
    label.htmlFor = `batch-name-${index}`;
    label.textContent = `第 ${index + 1} 个视频文件名`;
    const source = document.createElement("p");
    source.className = "hint batch-source";
    try {
      const parsed = new URL(url);
      source.textContent = parsed.hostname + parsed.pathname;
    } catch (_) {
      source.textContent = url;
    }
    source.title = url;
    const input = document.createElement("input");
    input.id = label.htmlFor;
    input.type = "text";
    input.autocomplete = "off";
    input.placeholder = "输入文件名，留空自动命名";
    input.value = batchNames.get(key) || "";
    input.addEventListener("input", () => {
      batchNames.set(key, input.value);
      updateNamePreviews();
    });
    const preview = document.createElement("p");
    preview.className = "name-preview";
    preview.id = `batch-preview-${index}`;
    input.setAttribute("aria-describedby", preview.id);
    field.append(label, source, input, preview);
    container.append(field);
    batchNameInputs.push(input);
    batchPreviews.push(preview);
    batchLabels.push(label);
  });
  for (const key of batchNames.keys()) {
    if (!activeKeys.has(key)) batchNames.delete(key);
  }
  syncNamingUI();
}

function namingMode() {
  return document.querySelector('input[name="naming-mode"]:checked').value;
}

function namingPayload() {
  const mode = namingMode();
  return {
    filename: mode === "prefix" ? $("filename").value.trim() : "",
    filenames: batchNameInputs.map((input) => mode === "individual" ? input.value.trim() : ""),
  };
}

function syncNamingUI() {
  const mode = namingMode();
  $("prefix-field").hidden = mode !== "prefix";
  $("prefix-hint").textContent = urls().length > 1
    ? "按地址顺序编号，例如：课程_01.mp4、课程_02.mp4。"
    : "单个视频直接使用此前缀作为文件名，例如：课程.mp4。";
  $("naming-help").textContent = {
    auto: "根据视频地址自动生成文件名，无需填写。",
    prefix: "填写一个前缀，为本次视频统一命名。",
    individual: "在每个视频地址下填写名称；留空的项目仍自动命名。",
  }[mode];
  $("batch-title").textContent = mode === "individual" ? "填写视频文件名" : "文件名预览";
  batchNameInputs.forEach((input, index) => {
    input.hidden = mode !== "individual";
    const label = batchLabels[index];
    label.textContent = mode === "individual" ? `第 ${index + 1} 个视频文件名` : `第 ${index + 1} 个视频`;
    if (mode === "individual") label.htmlFor = input.id;
    else label.removeAttribute("for");
  });
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
  const list = urls();
  const mode = namingMode();
  const prefix = mode === "prefix" ? $("filename").value.trim() : "";
  batchPreviews.forEach((preview, index) => {
    const own = mode === "individual" ? batchNameInputs[index].value.trim() : "";
    const name = outputName(own || prefix, list[index], prefix && list.length > 1 ? index : null);
    preview.textContent = mode === "prefix" && !prefix ? "填写前缀后显示文件名" : `保存为：${name}`;
  });
}

function updateSubmitState() {
  const count = urls().length;
  const missingPrefix = namingMode() === "prefix" && !$("filename").value.trim();
  $("start").disabled = submitting || count === 0 || count > 50 || missingPrefix;
  $("start").textContent = submitting ? "正在创建任务…" : count > 1 ? `下载 ${count} 个视频` : "开始下载";
  $("submit-summary").textContent = count > 50 ? "一次最多添加 50 个地址" : !count ? "先添加视频地址" : missingPrefix ? "请填写统一前缀" : `已准备 ${count} 个视频`;
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
      showNotice("下载链接已复制");
    } catch (_) {
      sourceDetails.open = true;
      showNotice("无法自动复制，请展开链接后手动复制。", true);
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
  actions.append(retryBtn, editBtn, cancelBtn, openBtn, removeBtn, logBtn);
  top.append(title, actions);

  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("div");
  fill.className = "bar-fill";
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "task-meta";
  const message = document.createElement("div");
  message.className = "task-message";
  const log = document.createElement("pre");
  log.className = "log task-log";
  log.hidden = true;
  log.setAttribute("aria-live", "polite");

  root.append(top, bar, meta, message, log);

  cancelBtn.addEventListener("click", () => cancelJob(job.id));
  retryBtn.addEventListener("click", () => retryJob(job.id));
  editBtn.addEventListener("click", () => prepareRetry(job.id));
  openBtn.addEventListener("click", () => openTaskFolder(job));
  removeBtn.addEventListener("click", () => removeJob(job.id));
  logBtn.addEventListener("click", () => toggleLogs(job.id));

  const refs = { root, nameText, badge, sub, sourceRow, sourceSummary, fullSource, copyUrlBtn, fill, meta, message, log, retryBtn, editBtn, cancelBtn, openBtn, removeBtn, logBtn };
  cards.set(job.id, refs);
  return refs;
}

function updateCard(job) {
  const refs = cards.get(job.id) || createCard(job);
  refs.nameText.textContent = basename(job.output) || "（未命名）";
  refs.sub.textContent = job.output ? `落盘位置：${job.output}` : "";
  refs.sub.title = job.output || "";
  refs.sourceRow.hidden = !job.url;
  refs.sourceSummary.textContent = job.url || "";
  refs.sourceSummary.title = job.url ? `${job.url}\n点击展开完整链接` : "";
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
    refs.fill.classList.remove("indeterminate");
  } else if (job.running) {
    refs.fill.style.width = "";
    refs.fill.classList.add("indeterminate");
  } else {
    refs.fill.style.width = "0";
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
  refs.meta.textContent = bits.join("  ·  ");

  refs.message.textContent = job.status === "queued" && job.queue_position ? `排队第 ${job.queue_position} 位，可随时取消` : job.message || "";
  refs.message.classList.toggle("failed", ["failed", "incomplete"].includes(job.status));

  refs.cancelBtn.hidden = !job.running;
  refs.retryBtn.hidden = job.running || !["failed", "incomplete", "cancelled"].includes(job.status);
  refs.retryBtn.disabled = retrying.has(job.id);
  refs.retryBtn.textContent = retrying.has(job.id) ? "提交中…" : job.status === "cancelled" ? "继续下载" : "重试";
  refs.editBtn.hidden = refs.retryBtn.hidden;
  refs.removeBtn.hidden = !!job.running;
  refs.openBtn.hidden = !job.output;
  const open = expanded.has(job.id);
  refs.log.hidden = !open;
  refs.logBtn.textContent = open ? "收起日志" : "日志";
  refs.logBtn.setAttribute("aria-expanded", String(open));
  return refs;
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

  $("task-empty").hidden = jobs.length > 0;
  const running = jobs.filter((job) => job.running).length;
  const queued = jobs.filter((job) => job.status === "queued").length;
  $("cancel-all").disabled = running === 0;
  $("clear-finished").disabled = jobs.length === running;
  $("task-counts").textContent = jobs.length
    ? `运行 ${running - queued} · 等待 ${queued} · 共 ${jobs.length}`
    : "";
}

async function loadLogs(id) {
  if (!expanded.has(id) || loadingLogs.has(id)) return;
  const refs = cards.get(id);
  if (!refs) return;
  loadingLogs.add(id);
  try {
    const data = await api("/api/video_downloader/logs", { id });
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
  polling = true;
  try {
    const data = await api("/api/video_downloader/state");
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

    const outDir = $("out-dir");
    if (!outDir.dataset.init && data.config) {
      outDir.value = data.config.output_dir || "";
      outDir.dataset.init = "1";
    }
    for (const id of [...expanded]) loadLogs(id);
  } catch (err) {
    console.error(err);
  } finally {
    polling = false;
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

async function startDownload() {
  if (submitting) return;
  hideNotice();
  const list = urls();
  const submittedText = $("url").value;
  const submittedNamingMode = namingMode();
  if (!list.length) {
    showNotice("请先填写视频地址", true);
    return;
  }
  const mode = proxyMode();
  const proxy = proxyValue();
  if (mode === "custom" && !proxy) {
    showNotice("请填写代理地址，例如 http://127.0.0.1:7890", true);
    $("proxy-url").focus();
    return;
  }

  const body = {
    urls: list,
    out_dir: $("out-dir").value.trim(),
    ...namingPayload(),
    headers: $("headers").value,
    restart: $("restart").checked,
    proxy,
  };
  const settings = downloadSettings();
  submitting = true;
  updateSubmitState();
  try {
    // Apply the visible controls before creating jobs, including retries.
    await api("/api/video_downloader/settings", settings);
    const data = await api("/api/video_downloader/start_batch", body);
    if (data.errors && data.errors.length) {
      showNotice(
        `已创建 ${data.jobs.length} 个任务；${data.errors.length} 个失败，地址已保留：${data.errors[0].error}`,
        true,
      );
    } else {
      showNotice(data.reused
        ? `新建 ${data.jobs.length - data.reused} 个任务，复用 ${data.reused} 个已有任务，可在任务卡查看原文件名`
        : `已添加 ${data.jobs.length} 个任务，按并发上限自动开始`);
    }
    // Keep edits made while the request was in flight; retain failed addresses for correction.
    const currentNames = namingPayload();
    const namesUnchanged = namingMode() === submittedNamingMode && currentNames.filename === body.filename && JSON.stringify(currentNames.filenames) === JSON.stringify(body.filenames);
    if ($("url").value === submittedText && namesUnchanged) {
      const failures = data.errors || [];
      const failedNames = failures.map((error) => {
        const index = list.indexOf(error.url);
        return body.filenames[index] || (body.filename && list.length > 1 ? outputName(body.filename, error.url, index) : body.filename);
      });
      $("url").value = (data.errors || []).map((error) => error.url).join("\n");
      const occurrences = new Map();
      failures.forEach((error, index) => {
        const occurrence = occurrences.get(error.url) || 0;
        occurrences.set(error.url, occurrence + 1);
        batchNames.set(JSON.stringify([error.url, occurrence]), failedNames[index]);
      });
      if (failedNames.some(Boolean)) {
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
    refresh();
  }
}

async function retryJob(id) {
  if (retrying.has(id)) return;
  retrying.add(id);
  const job = currentJobs.find((item) => item.id === id);
  if (job) updateCard(job);
  try {
    await api("/api/video_downloader/retry", { id, settings: downloadSettings() });
    showNotice("原任务已继续，将复用已有缓存");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    retrying.delete(id);
    await refresh();
  }
}

async function prepareRetry(id) {
  let job = currentJobs.find((item) => item.id === id);
  if (!job || job.running || !job.url || !job.output || submitting) return;
  if ($("url").value.trim() && !window.confirm("将载入此任务的地址与文件名，替换表单中尚未提交的视频，继续？")) return;
  try {
    job = { ...job, ...await api("/api/video_downloader/edit_details", { id }) };
  } catch (error) {
    showNotice(error.message, true);
    return;
  }
  const index = Math.max(job.output.lastIndexOf("/"), job.output.lastIndexOf("\\"));
  if (index < 0) {
    showNotice("无法识别原保存目录，请手动选择目录后重试。", true);
    return;
  }
  $("out-dir").value = job.output.slice(0, index + 1);
  $("out-dir").dataset.init = "1";
  $("url").value = job.url;
  $("restart").checked = false;
  batchNames.set(JSON.stringify([job.url, 0]), job.output.slice(index + 1));
  document.querySelector('input[name="naming-mode"][value="individual"]').checked = true;
  $("headers").value = job.headers || "";
  $("proxy-mode").value = !job.proxy ? "inherit" : job.proxy === "direct" ? "direct" : "custom";
  $("proxy-url").value = job.proxy && job.proxy !== "direct" ? job.proxy : "";
  syncProxyInput();
  syncUrlState();
  $("out-dir").scrollIntoView({ behavior: "smooth", block: "start" });
  showNotice("已载入原任务的地址、文件名及请求设置。编辑后点击“开始下载”；直接续传可用任务卡上的“重试”。");
}

async function cancelJob(id) {
  hideNotice();
  try {
    await api("/api/video_downloader/cancel", { id });
  } catch (err) {
    showNotice(err.message, true);
  }
  refresh();
}

async function cancelAll() {
  hideNotice();
  const running = currentJobs.filter((job) => job.running);
  if (!running.length) {
    showNotice("没有正在运行的任务", true);
    return;
  }
  if (running.length > 1 && !window.confirm(`取消 ${running.length} 个运行中的任务？已下载的分段会保留。`)) {
    return;
  }
  try {
    const data = await api("/api/video_downloader/cancel_all");
    showNotice(`已请求取消 ${data.cancelled} 个任务`);
  } catch (err) {
    showNotice(err.message, true);
  }
  refresh();
}

async function clearFinished() {
  hideNotice();
  const finished = currentJobs.filter((job) => !job.running).length;
  if (!finished) {
    showNotice("没有已完成或已结束的任务", true);
    return;
  }
  if (!window.confirm(`清除 ${finished} 条已结束任务记录？文件不会被删除。`)) {
    return;
  }
  try {
    const data = await api("/api/video_downloader/clear_finished");
    showNotice(`已清除 ${data.removed} 条任务记录`);
  } catch (err) {
    showNotice(err.message, true);
  }
  refresh();
}

async function removeJob(id) {
  hideNotice();
  if (!window.confirm("移除这条任务记录？文件不会被删除。")) return;
  try {
    await api("/api/video_downloader/remove", { id });
  } catch (err) {
    showNotice(err.message, true);
  }
  refresh();
}

async function openTaskFolder(job) {
  hideNotice();
  let path = job.output || "";
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  if (index > 0) path = path.slice(0, index);
  try {
    await api("/api/video_downloader/open_folder", { path });
  } catch (err) {
    showNotice(err.message, true);
  }
}

$("start").addEventListener("click", startDownload);
$("cancel-all").addEventListener("click", cancelAll);
$("clear-finished").addEventListener("click", clearFinished);
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
$("url").addEventListener("input", syncUrlState);
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
refresh();
setInterval(refresh, 800);
