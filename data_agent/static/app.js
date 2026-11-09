const state = {
  overview: null,
  audits: [],
  activeAudit: null,
  activeJob: null,
  activeTab: "alerts",
  pollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const LEVEL_TEXT = {
  low: "低风险",
  medium: "关注",
  high: "高风险",
  critical: "严重预警",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function clear(element) {
  if (element) element.replaceChildren();
}

function formatInteger(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("zh-CN").format(number)
    : String(value);
}

function formatPrice(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : String(value);
}

function formatPct(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number >= 0 ? "+" : ""}${(number * 100).toFixed(digits)}%`;
}

function formatDate(value, withTime = true) {
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
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(payload?.error || payload || `请求失败 (${response.status})`);
  }
  return payload;
}

function setLevelBadge(element, level, label) {
  element.textContent = label || LEVEL_TEXT[level] || "未知";
  element.dataset.level = level || "low";
}

function tickClock() {
  $("#clock").textContent = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function renderAgentStatus() {
  const configured = Boolean(state.overview?.agent?.configured);
  const dot = $("#agentDot");
  dot.classList.toggle("is-ready", configured);
  dot.classList.toggle("is-warning", !configured);
  $("#agentStatus").textContent = configured
    ? `DeepSeek · ${state.overview.agent.model}`
    : "DeepSeek 未配置";
}

function populateHistory() {
  const select = $("#historySelect");
  clear(select);
  if (!state.audits.length) {
    select.append(new Option("暂无研判记录", ""));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  state.audits.forEach((audit) => {
    const symbols = (audit.assessments || []).map((item) => item.symbol).join(", ");
    select.append(
      new Option(
        `${formatDate(audit.created_at)} · ${symbols || "未记录"}`,
        audit.audit_id,
      ),
    );
  });
  select.value = state.activeAudit?.audit_id || state.audits[0].audit_id;
}

async function loadHistory() {
  const payload = await api("/api/audits?limit=12");
  state.audits = payload.items || [];
  populateHistory();
  return state.audits;
}

async function loadAudit(auditId) {
  if (!auditId) return;
  try {
    const audit = await api(`/api/audits/${encodeURIComponent(auditId)}`);
    renderAudit(audit);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderTrace(events) {
  const list = $("#traceList");
  clear(list);
  if (!events?.length) {
    list.append(node("div", "trace-empty", "等待任务事件"));
    return;
  }
  events.forEach((event) => {
    const row = node("div", "trace-row");
    row.dataset.event = event.event || "event";
    row.append(
      node("span", "trace-time", event.time ? event.time.slice(11, 19) : "—"),
      node("span", "trace-label", event.event || "event"),
      node("span", "trace-text", event.message || ""),
    );
    list.append(row);
  });
  list.scrollTop = list.scrollHeight;
}

function renderJob(job) {
  state.activeJob = job;
  const status = job.status || "queued";
  const labels = {
    queued: "排队中",
    running: "执行中",
    complete: "已完成",
    failed: "失败",
  };
  const panel = $("#jobPanel");
  panel.hidden = false;
  $("#jobTitle").textContent = status === "complete" ? "研判完成" : "正在研判";
  $("#jobStage").textContent = labels[status] || status;
  $("#jobPercent").textContent = `${Math.round(Number(job.progress) || 0)}%`;
  $("#progressBar").style.width = `${Math.max(0, Math.min(100, Number(job.progress) || 0))}%`;
  renderTrace(job.events || []);
  const running = status === "queued" || status === "running";
  $("#analyzeButton").disabled = running;
  $("#analyzeButton").textContent = running ? "研判中" : "开始智能研判";
  if (status === "failed") {
    $("#jobTitle").textContent = "研判未完成";
    $("#jobStage").textContent = job.error || "执行失败";
    toast(job.error || "研判任务失败", true);
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  const symbols = $("#symbolInput").value.trim();
  if (!symbols) {
    toast("请输入至少一个 6 位股票代码", true);
    $("#symbolInput").focus();
    return;
  }
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        mode: "risk",
        symbols,
        history_days: Number($("#historyDays").value || 120),
        include_quotes: true,
        query: "股票风险体检：重新采集、核价、获取新闻并生成风险预警",
      }),
    });
    $("#emptyState").hidden = true;
    $("#resultView").hidden = true;
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
      state.pollTimer = window.setTimeout(() => pollJob(jobId), 850);
      return;
    }
    if (job.status === "complete" && job.result) {
      $("#jobPanel").hidden = true;
      renderAudit(job.result);
      await loadHistory();
      renderAudit(job.result);
      toast("风险体检完成");
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function renderStockList(assessments) {
  const list = $("#stockList");
  clear(list);
  assessments.forEach((item) => {
    const row = node("article", "stock-row");
    row.dataset.level = item.level;
    const identity = node("div", "stock-identity");
    identity.append(
      node("strong", "", item.name),
      node("span", "symbol", item.symbol),
    );
    const quote = node("div", "stock-quote");
    quote.append(
      node("strong", "", formatPrice(item.price)),
      node(
        "span",
        Number(item.change_pct) >= 0 ? "price-up" : "price-down",
        formatPct(item.change_pct, 2),
      ),
    );
    const metrics = node("div", "stock-metrics");
    const volatility = item.metrics?.volatility_20_annualized;
    const drawdown = item.metrics?.drawdown_20d;
    metrics.append(
      node("span", "", `波动 ${formatPct(volatility)}`),
      node("span", "", `20日回撤 ${formatPct(drawdown)}`),
    );
    const risk = node("div", "stock-risk");
    const badge = node("span", "level-badge");
    setLevelBadge(badge, item.level, item.level_label);
    risk.append(
      badge,
      node("span", "risk-headline", item.headline || "未触发预警"),
    );
    const score = node("div", "risk-score");
    score.append(
      node("span", "", "评分"),
      node("strong", "", item.score ?? "—"),
    );
    row.append(identity, quote, metrics, risk, score);
    list.append(row);
  });
}

function appendEmpty(container, message) {
  container.append(node("div", "detail-empty", message));
}

function renderAlerts(container, assessments) {
  let count = 0;
  assessments.forEach((item) => {
    (item.alerts || []).forEach((alert) => {
      if (alert.severity === "low") return;
      count += 1;
      const row = node("div", "evidence-row");
      row.dataset.level = alert.severity;
      row.append(
        node("span", "evidence-marker", LEVEL_TEXT[alert.severity] || alert.severity),
        node("strong", "evidence-title", `${item.name} · ${alert.title}`),
        node("p", "evidence-copy", alert.detail),
      );
      container.append(row);
    });
  });
  if (!count) appendEmpty(container, "当前没有触发中高风险规则。");
}

function renderNews(container, assessments) {
  const items = [];
  assessments.forEach((item) => {
    const intelligence = item.intelligence || {};
    (intelligence.news || []).forEach((news) => items.push({ ...news, stockName: item.name }));
    (intelligence.announcements || []).forEach((news) => items.push({ ...news, stockName: item.name }));
  });
  items.sort((a, b) => String(b.published_at || "").localeCompare(String(a.published_at || "")));
  if (!items.length) {
    appendEmpty(container, "没有获取到新闻或公告。");
    return;
  }
  items.forEach((item) => {
    const row = node("article", "news-row");
    const meta = node("div", "news-meta");
    meta.append(
      node("span", "quiet-badge", item.category === "announcement" ? "公告" : "新闻"),
      node("span", "", `${item.stockName} · ${item.media || "公开资讯"}`),
      node("span", "", formatDate(item.published_at)),
    );
    const link = node("a", "news-title", item.title);
    link.href = item.url || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(meta, link);
    if (item.summary) row.append(node("p", "news-summary", item.summary));
    container.append(row);
  });
}

function renderQuality(container, assessments) {
  assessments.forEach((item) => {
    const block = node("article", "quality-row");
    const head = node("div", "quality-head");
    const badge = node("span", "level-badge");
    const validStatus = item.validation?.status || "warn";
    const label = validStatus === "pass" ? "核对通过" : validStatus === "fail" ? "核对失败" : "需要关注";
    setLevelBadge(badge, validStatus === "pass" ? "low" : validStatus === "fail" ? "critical" : "medium", label);
    head.append(
      node("strong", "", `${item.name} ${item.symbol}`),
      badge,
    );
    block.append(head);
    const sources = node("div", "source-check");
    (item.validation?.cross_source?.sources || []).forEach((source) => {
      const sourceRow = node("div", "source-check-row");
      sourceRow.append(
        node("span", "", source.source || source.id),
        node("strong", "", source.error ? "不可用" : formatPrice(source.price)),
        node("span", "muted", source.updated_at || ""),
      );
      sources.append(sourceRow);
    });
    if (!sources.childElementCount) {
      sources.append(node("div", "source-check-row", "双源核价不可用"));
    }
    block.append(sources);
    const issues = item.validation?.issues || [];
    if (issues.length) {
      const list = node("ul", "issue-list");
      issues.forEach((issue) => list.append(node("li", "", issue.message)));
      block.append(list);
    }
    container.append(block);
  });
}

function renderRaw(container, audit) {
  const evidence = audit.evidence || {};
  const grid = node("div", "raw-grid");
  const rows = [
    ["行情快照", evidence.run_id || "—"],
    ["日线行数", formatInteger(evidence.daily_rows)],
    ["行情行数", formatInteger(evidence.quote_rows)],
    ["实际来源", (evidence.sources || []).join(" / ") || "—"],
    ["日线 SHA256", evidence.daily_sha256 || "—"],
    ["行情 SHA256", evidence.quote_sha256 || "—"],
  ];
  rows.forEach(([label, value]) => {
    const item = node("div", "raw-item");
    item.append(node("span", "", label), node("strong", "", value));
    grid.append(item);
  });
  container.append(grid);
  const links = node("div", "download-links");
  [["日线 CSV", "daily"], ["行情 CSV", "quotes"], ["股票池 CSV", "universe"]].forEach(([label, view]) => {
    const link = node("a", "text-link", label);
    link.href = `/api/runs/${encodeURIComponent(evidence.run_id)}/csv?view=${view}`;
    links.append(link);
  });
  const manifest = node("a", "text-link", "打开 manifest");
  manifest.href = `/api/runs/${encodeURIComponent(evidence.run_id)}`;
  manifest.target = "_blank";
  manifest.rel = "noopener noreferrer";
  links.append(manifest);
  container.append(links);
}

function renderDetail() {
  const audit = state.activeAudit;
  if (!audit) return;
  $$(".tab").forEach((button) => {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  const container = $("#detailContent");
  clear(container);
  if (state.activeTab === "news") renderNews(container, audit.assessments || []);
  else if (state.activeTab === "quality") renderQuality(container, audit.assessments || []);
  else if (state.activeTab === "raw") renderRaw(container, audit);
  else renderAlerts(container, audit.assessments || []);
}

function renderAudit(audit) {
  state.activeAudit = audit;
  $("#emptyState").hidden = true;
  $("#resultView").hidden = false;
  const summary = audit.summary || {};
  const assessments = audit.assessments || [];
  const quality = audit.quality || {};
  setLevelBadge($("#overallLevel"), summary.level, summary.level_label);
  $("#overallHeadline").textContent = summary.headline || "研判完成";
  $("#resultTime").textContent = formatDate(audit.created_at);
  $("#metricVerified").textContent = `${formatInteger(quality.dual_source_verified)} / ${formatInteger(assessments.length)}`;
  const counts = summary.counts || {};
  $("#metricHighRisk").textContent = formatInteger((counts.high || 0) + (counts.critical || 0));
  const newsCount = assessments.reduce(
    (total, item) => total + (item.intelligence?.news?.length || 0) + (item.intelligence?.announcements?.length || 0),
    0,
  );
  $("#metricNews").textContent = formatInteger(newsCount);
  $("#metricIssues").textContent = formatInteger(quality.issue_total);
  $("#stockCount").textContent = `${assessments.length} 只`;
  $("#deepseekBrief").textContent = audit.brief || "暂无研判文本";
  $("#briefBadge").textContent = audit.brief_error ? "规则兜底" : "DeepSeek";
  $("#briefBadge").classList.toggle("is-warning", Boolean(audit.brief_error));
  const fallback = $("#briefFallback");
  fallback.hidden = !audit.brief_error;
  fallback.textContent = audit.brief_error
    ? `DeepSeek 简报未生成，当前展示规则结论：${audit.brief_error}`
    : "";
  renderStockList(assessments);
  renderDetail();
  $("#auditId").textContent = audit.audit_id || "—";
  $("#runId").textContent = audit.evidence?.run_id || "—";
  populateHistory();
  if (audit.audit_id) $("#historySelect").value = audit.audit_id;
}

async function init() {
  $("#analysisForm").addEventListener("submit", submitAnalysis);
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderDetail();
    });
  });
  $("#historySelect").addEventListener("change", (event) => loadAudit(event.target.value));
  tickClock();
  window.setInterval(tickClock, 1000);
  try {
    state.overview = await api("/api/overview");
    renderAgentStatus();
    await loadHistory();
    if (state.audits.length) renderAudit(state.audits[0]);
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
