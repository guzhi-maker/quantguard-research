const state = {
  overview: null,
  sources: [],
  probeStatuses: {},
  runs: [],
  activeRun: null,
  activeJob: null,
  dataView: "daily",
  dataOffset: 0,
  dataQuery: "",
  dataLimit: 50,
  dataTotal: 0,
  pollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function formatInteger(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : String(value);
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("is-error", Boolean(isError));
  element.classList.add("is-visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("is-visible"), 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = payload?.error || payload || `请求失败 (${response.status})`;
    throw new Error(String(message));
  }
  return payload;
}

function navigate(view) {
  $$(".nav-item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });
  $$(".page").forEach((page) => {
    page.classList.toggle("is-active", page.dataset.page === view);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (view === "data") {
    const runId = state.activeRun?.run_id || state.runs[0]?.run_id;
    if (runId) loadRun(runId);
  }
}

function setJobStage(label, status = "queued") {
  const stage = $("#jobStage");
  stage.textContent = label;
  stage.className = "status-label";
  if (status === "running") stage.classList.add("is-running");
  if (status === "complete") stage.classList.add("is-complete");
  if (status === "failed") stage.classList.add("is-failed");
}

function setProgress(percent) {
  const safe = Math.max(0, Math.min(100, Number(percent) || 0));
  $("#progressBar").style.width = `${safe}%`;
  $("#jobPercent").textContent = `${Math.round(safe)}%`;
}

function renderAgentStatus() {
  const configured = Boolean(state.overview?.agent?.configured);
  $("#agentDot").className = `status-dot ${configured ? "is-ok" : "is-warn"}`;
  $("#agentSidebarText").textContent = configured ? "DeepSeek 已就绪" : "DeepSeek 未配置";
  const chip = $("#agentChip");
  chip.textContent = configured
    ? `DeepSeek · ${state.overview.agent.model}`
    : "DeepSeek 未配置";
  chip.classList.toggle("is-warn", !configured);
  $("#formHint").textContent = configured
    ? "智能体只调用已注册数据工具，不生成行情数值。"
    : "智能体需要本地 DEEPSEEK_API_KEY；固定流程采集可直接运行。";
}

function renderSources() {
  const preview = $("#sourcePreview");
  const cards = $("#sourceCards");
  const tbody = $("#sourceTableBody");
  preview.replaceChildren();
  cards.replaceChildren();
  tbody.replaceChildren();

  state.sources.forEach((source, index) => {
    const status = state.probeStatuses[source.id];
    const line = node("div", "source-line");
    const title = node("div", "source-title");
    title.append(node("strong", "", source.name));
    title.append(node("span", "", `${source.role} · ${source.adjustment}`));
    const latency = node("span", "source-health");
    if (status?.status === "ok") {
      latency.classList.add("is-ok");
      latency.textContent = `${status.latency_ms} ms`;
    } else if (status?.status === "error") {
      latency.classList.add("is-error");
      latency.textContent = "不可用";
    } else {
      latency.textContent = "未探测";
    }
    const detail = node("button", "row-link", "详情");
    detail.type = "button";
    detail.addEventListener("click", () => navigate("sources"));
    line.append(title, latency, detail);
    preview.append(line);

    const card = node("article", "source-card");
    const top = node("div", "source-card-top");
    const heading = node("div");
    heading.append(node("span", "priority", `PRIORITY ${source.priority}`));
    heading.append(node("h2", "", source.name));
    const health = node("span", status?.status === "ok" ? "status-label is-complete" : "status-label", status?.status === "ok" ? "在线" : status?.status === "error" ? "异常" : source.role);
    top.append(heading, health);
    card.append(top, node("p", "", source.constraints[0] || "公开接口"));
    const tags = node("div", "tag-row");
    source.capabilities.forEach((capability) => tags.append(node("span", "tag", capability)));
    card.append(tags);
    const list = node("dl");
    [
      ["复权口径", source.adjustment],
      ["成交额", source.amount_quality],
      ["访问", source.access],
      ["字段", source.fields.join(", ")],
    ].forEach(([label, value]) => {
      const row = node("div");
      row.append(node("dt", "", label), node("dd", "", value));
      list.append(row);
    });
    card.append(list);
    cards.append(card);

    const tr = node("tr");
    [
      source.name,
      `P${source.priority}`,
      source.capabilities.join(" / "),
      source.adjustment,
      source.amount_quality,
      source.access,
      status?.status === "ok" ? `${status.latency_ms} ms` : status?.status === "error" ? "异常" : "未探测",
    ].forEach((value, cellIndex) => {
      const cell = node("td", cellIndex === 1 ? "mono" : "", value);
      tr.append(cell);
    });
    tbody.append(tr);
  });
}

function runStatusLabel(manifest) {
  if (manifest.status === "complete") return "完成";
  if (manifest.status === "failed") return "失败";
  return manifest.status || "未知";
}

function runMode(manifest) {
  if (manifest.agent?.provider === "deepseek") return "DeepSeek";
  if (manifest.agent?.provider === "fixed") return "固定流程";
  return "—";
}

function runRows(manifest) {
  return manifest.summary?.daily_rows ?? manifest.files?.daily?.rows ?? 0;
}

function appendRunButton(tr, manifest, label = "打开") {
  const cell = node("td");
  const button = node("button", "row-link", label);
  button.type = "button";
  button.addEventListener("click", () => {
    navigate("data");
    loadRun(manifest.run_id);
  });
  cell.append(button);
  tr.append(cell);
}

function renderRuns() {
  const recentBody = $("#recentRunsBody");
  const fullBody = $("#runsTableBody");
  recentBody.replaceChildren();
  fullBody.replaceChildren();
  $("#runCountChip").textContent = `${state.runs.length} 份快照`;

  const selector = $("#runSelector");
  selector.replaceChildren();
  if (!state.runs.length) {
    selector.append(new Option("暂无采集记录", ""));
  } else {
    state.runs.forEach((run) => {
      selector.append(new Option(`${run.run_id} · ${formatInteger(runRows(run))} 行`, run.run_id));
    });
    selector.value = state.activeRun?.run_id || state.runs[0].run_id;
  }

  if (!state.runs.length) {
    const recentEmpty = node("tr", "table-empty");
    const recentCell = node("td", "", "暂无采集记录");
    recentCell.colSpan = 8;
    recentEmpty.append(recentCell);
    recentBody.append(recentEmpty);
    const fullEmpty = node("tr", "table-empty");
    const fullCell = node("td", "", "暂无采集记录");
    fullCell.colSpan = 10;
    fullEmpty.append(fullCell);
    fullBody.append(fullEmpty);
    renderLatestMetrics(null);
    return;
  }

  state.runs.slice(0, 5).forEach((run) => {
    const tr = node("tr");
    [
      run.run_id,
      formatDate(run.created_at, true),
      formatInteger(run.summary?.symbols),
      formatInteger(runRows(run)),
      `${run.summary?.date_min || "—"} → ${run.summary?.date_max || "—"}`,
      runMode(run),
      runStatusLabel(run),
    ].forEach((value, index) => {
      tr.append(node("td", index === 0 || index === 2 || index === 3 || index === 6 ? "mono" : "", value));
    });
    appendRunButton(tr, run);
    recentBody.append(tr);
  });

  state.runs.forEach((run) => {
    const tr = node("tr");
    [
      run.run_id,
      formatDate(run.created_at, true),
      runMode(run),
      formatInteger(run.summary?.symbols),
      formatInteger(runRows(run)),
      `${run.summary?.date_min || "—"} → ${run.summary?.date_max || "—"}`,
      (run.summary?.sources || []).join(" / "),
      formatInteger(run.summary?.failures),
      runStatusLabel(run),
    ].forEach((value, index) => {
      tr.append(node("td", [0, 3, 4, 7, 8].includes(index) ? "mono" : "", value));
    });
    appendRunButton(tr, run);
    fullBody.append(tr);
  });

  renderLatestMetrics(state.runs[0]);
}

function renderLatestMetrics(run) {
  if (!run) {
    $("#metricRun").textContent = "—";
    $("#metricRunTime").textContent = "暂无采集记录";
    $("#metricSymbols").textContent = "—";
    $("#metricRows").textContent = "—";
    $("#metricRange").textContent = "—";
    $("#metricFailures").textContent = "—";
    $("#handoffManifest").textContent = "待生成";
    $("#handoffDaily").textContent = "待生成";
    $("#handoffHash").textContent = "待生成";
    $("#handoffLineage").textContent = "待生成";
    return;
  }
  const summary = run.summary || {};
  $("#metricRun").textContent = run.run_id.replace("RAW-", "");
  $("#metricRunTime").textContent = formatDate(run.created_at, true);
  $("#metricSymbols").textContent = formatInteger(summary.symbols);
  $("#metricRows").textContent = formatInteger(runRows(run));
  $("#metricRange").textContent = summary.date_min && summary.date_max
    ? `${summary.date_min.slice(5)} — ${summary.date_max.slice(5)}`
    : "—";
  $("#metricFailures").textContent = formatInteger(summary.failures);
  $("#handoffManifest").textContent = run.manifest_path ? "已生成" : "已生成";
  $("#handoffDaily").textContent = formatInteger(run.files?.daily?.rows);
  $("#handoffHash").textContent = run.files?.daily?.sha256 ? `${run.files.daily.sha256.slice(0, 8)}…` : "—";
  $("#handoffLineage").textContent = `${(run.lineage || []).length} 源`;
}

function renderTrace(events) {
  const trace = $("#traceList");
  trace.replaceChildren();
  if (!events?.length) {
    trace.append(node("div", "empty-inline", "任务已提交，等待执行事件。"));
    return;
  }
  events.forEach((event) => {
    const row = node("div", "trace-item");
    if (event.event === "error" || event.event === "failed") row.classList.add("is-error");
    if (event.event === "complete") row.classList.add("is-complete");
    row.append(
      node("span", "trace-time", event.time ? event.time.slice(11, 19) : "—"),
      node("span", "trace-event", event.event || "event"),
      node("span", "trace-message", event.message || "")
    );
    trace.append(row);
  });
  trace.scrollTop = trace.scrollHeight;
}

function renderJob(job) {
  state.activeJob = job;
  const status = job.status || "queued";
  const labels = { queued: "排队中", running: "执行中", complete: "已完成", failed: "失败" };
  setJobStage(labels[status] || status, status);
  setProgress(job.progress || (status === "complete" ? 100 : 0));
  renderTrace(job.events || []);
  const result = job.result;
  $("#jobRunId").textContent = result?.run_id || "—";
  $("#jobSymbols").textContent = result?.summary ? formatInteger(result.summary.symbols) : "—";
  $("#jobRows").textContent = result?.summary ? formatInteger(result.summary.daily_rows) : "—";
  $("#jobSources").textContent = result?.summary?.sources?.join(" + ") || "—";
  $("#jobFailures").textContent = result?.summary ? formatInteger(result.summary.failures) : "—";
  $("#openLatestRun").disabled = !result?.run_id;
  const running = status === "queued" || status === "running";
  $("#agentRunButton").disabled = running;
  $("#fixedRunButton").disabled = running;
  if (status === "failed") {
    $("#jobRunId").textContent = "任务终止";
    setJobStage("执行失败", "failed");
    toast(job.error || "采集任务失败", true);
  }
}

async function loadOverview() {
  const overview = await api("/api/overview");
  state.overview = overview;
  state.sources = overview.sources || [];
  state.runs = overview.recent_runs || [];
  renderAgentStatus();
  renderSources();
  renderRuns();
  if (!state.activeRun && state.runs.length) {
    state.activeRun = state.runs[0];
    renderLatestMetrics(state.activeRun);
  }
}

async function refreshRuns() {
  const payload = await api("/api/runs?limit=50");
  state.runs = payload.items || [];
  renderRuns();
}

async function probeSources() {
  const buttons = [$("#probeSourcesButton"), $("#probeSourcesPageButton")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const payload = await api("/api/sources/probe", {
      method: "POST",
      body: JSON.stringify({ source_ids: state.sources.map((source) => source.id) }),
    });
    (payload.items || []).forEach((result) => {
      state.probeStatuses[result.id] = result;
    });
    renderSources();
    const online = (payload.items || []).filter((item) => item.status === "ok").length;
    toast(`${online}/${payload.items.length} 个数据源可用`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function collectionPayload(mode) {
  return {
    mode,
    query: $("#taskQuery").value.trim(),
    universe_size: Number($("#universeSize").value || 80),
    history_days: Number($("#historyDays").value || 500),
    include_quotes: $("#includeQuotes").checked,
    symbols: $("#symbolInput").value.trim(),
  };
}

async function submitCollection(mode) {
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(collectionPayload(mode)),
    });
    renderJob(job);
    pollJob(job.id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function pollJob(jobId) {
  window.clearTimeout(state.pollTimer);
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJob(job);
    if (job.status === "queued" || job.status === "running") {
      state.pollTimer = window.setTimeout(() => pollJob(jobId), 900);
      return;
    }
    if (job.status === "complete" && job.result?.run_id) {
      await refreshRuns();
      await loadRun(job.result.run_id);
      toast(`采集完成：${job.result.run_id}`);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadRun(runId) {
  if (!runId) return;
  try {
    const manifest = await api(`/api/runs/${encodeURIComponent(runId)}`);
    state.activeRun = manifest;
    state.dataOffset = 0;
    $("#runSelector").value = runId;
    $("#datasetRunId").textContent = runId;
    $("#datasetMeta").textContent = [
      `${manifest.summary?.symbols || 0} 只股票`,
      `${formatInteger(runRows(manifest))} 行日线`,
      `${manifest.summary?.date_min || "—"} 至 ${manifest.summary?.date_max || "—"}`,
      (manifest.summary?.sources || []).join(" + "),
    ].join(" · ");
    renderLatestMetrics(manifest);
    await loadRows();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderDatasetTable(rows, columns) {
  const head = $("#datasetHead");
  const body = $("#datasetBody");
  head.replaceChildren();
  body.replaceChildren();
  if (!columns?.length) return;
  const headRow = node("tr");
  columns.forEach((column) => headRow.append(node("th", ["close", "open", "high", "low", "volume", "amount", "price", "change_pct", "turnover", "pe", "pb", "market_cap"].includes(column) ? "numeric" : "", column)));
  head.append(headRow);
  if (!rows.length) {
    const tr = node("tr", "table-empty");
    const td = node("td", "", "没有匹配的原始数据");
    td.colSpan = columns.length;
    tr.append(td);
    body.append(tr);
    return;
  }
  rows.forEach((row) => {
    const tr = node("tr");
    columns.forEach((column) => {
      const numeric = ["close", "open", "high", "low", "volume", "amount", "price", "change_pct", "turnover", "pe", "pb", "market_cap"].includes(column);
      tr.append(node("td", numeric ? "numeric" : ["symbol", "date"].includes(column) ? "mono" : "", row[column] ?? ""));
    });
    body.append(tr);
  });
}

async function loadRows() {
  if (!state.activeRun) return;
  const manifest = state.activeRun;
  const params = new URLSearchParams({
    view: state.dataView,
    offset: String(state.dataOffset),
    limit: String(state.dataLimit),
    q: state.dataQuery,
  });
  try {
    const result = await api(`/api/runs/${encodeURIComponent(manifest.run_id)}/rows?${params}`);
    state.dataTotal = result.total;
    const columns = manifest.files?.[state.dataView]?.columns || [];
    renderDatasetTable(result.rows || [], columns);
    $("#datasetTotal").textContent = `${formatInteger(result.total)} 行`;
    const page = Math.floor(state.dataOffset / state.dataLimit) + 1;
    const pages = Math.max(1, Math.ceil(result.total / state.dataLimit));
    $("#datasetPage").textContent = `第 ${page} / ${pages} 页`;
    $("#prevPage").disabled = state.dataOffset <= 0;
    $("#nextPage").disabled = state.dataOffset + state.dataLimit >= result.total;
  } catch (error) {
    toast(error.message, true);
  }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.view));
  });
  $$("[data-go-view]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.goView));
  });
  $("#refreshButton").addEventListener("click", () => {
    loadOverview().then(() => toast("状态已刷新")).catch((error) => toast(error.message, true));
  });
  $("#refreshRunsButton").addEventListener("click", () => {
    refreshRuns().then(() => toast("采集记录已刷新")).catch((error) => toast(error.message, true));
  });
  $("#probeSourcesButton").addEventListener("click", probeSources);
  $("#probeSourcesPageButton").addEventListener("click", probeSources);
  $("#collectionForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitCollection(event.submitter?.dataset.mode || "agent");
  });
  $("#includeQuotes").addEventListener("change", () => {
    $("#quoteSwitchLabel").textContent = $("#includeQuotes").checked ? "保存" : "跳过";
  });
  $("#openLatestRun").addEventListener("click", () => {
    const runId = state.activeJob?.result?.run_id;
    if (!runId) return;
    navigate("data");
    loadRun(runId);
  });
  $("#runSelector").addEventListener("change", () => loadRun($("#runSelector").value));
  $$(".segment").forEach((button) => {
    button.addEventListener("click", () => {
      state.dataView = button.dataset.view;
      state.dataOffset = 0;
      $$(".segment").forEach((item) => item.classList.toggle("is-active", item === button));
      loadRows();
    });
  });
  $("#datasetSearchButton").addEventListener("click", () => {
    state.dataQuery = $("#datasetQuery").value.trim();
    state.dataOffset = 0;
    loadRows();
  });
  $("#datasetQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("#datasetSearchButton").click();
    }
  });
  $("#prevPage").addEventListener("click", () => {
    state.dataOffset = Math.max(0, state.dataOffset - state.dataLimit);
    loadRows();
  });
  $("#nextPage").addEventListener("click", () => {
    state.dataOffset += state.dataLimit;
    loadRows();
  });
  $$("[data-export-view]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!state.activeRun) {
        toast("请先选择一份采集记录", true);
        return;
      }
      const url = `/api/runs/${encodeURIComponent(state.activeRun.run_id)}/csv?view=${button.dataset.exportView}`;
      window.location.assign(url);
    });
  });
}

function tickClock() {
  $("#currentTime").textContent = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

async function init() {
  bindEvents();
  tickClock();
  window.setInterval(tickClock, 1000);
  try {
    await loadOverview();
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
