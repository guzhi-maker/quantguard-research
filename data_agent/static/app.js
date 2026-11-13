const state = {
  overview: null,
  audits: [],
  datasets: [],
  coverageByRun: {},
  exportsByRun: {},
  factorsByRun: {},
  fundamentalsByRun: {},
  factorSamplesByRun: {},
  activeFactorGroup: "all",
  activeFactorSymbol: null,
  activeAudit: null,
  activeDatasetId: null,
  selectedSymbol: null,
  activeJob: null,
  jobKind: null,
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

function currentDataset() {
  return (
    state.datasets.find((item) => item.run_id === state.activeDatasetId) ||
    state.datasets[0] ||
    null
  );
}

function renderPool() {
  const select = $("#datasetSelect");
  clear(select);
  const links = $("#datasetLinks");
  clear(links);
  const info = $("#datasetInfo");
  clear(info);

  if (!state.datasets.length) {
    select.append(new Option("暂无数据快照", ""));
    select.disabled = true;
    $("#poolSymbolsCount").textContent = "—";
    $("#poolRowsCount").textContent = "—";
    $("#poolDateRange").textContent = "—";
    $("#poolFailures").textContent = "—";
    info.append(node("span", "", "还没有本地快照，先采集一次数据池。"));
    renderCoverage();
    return;
  }

  select.disabled = false;
  state.datasets.forEach((item) => {
    const summary = item.summary || {};
    select.append(
      new Option(
        `${formatDate(item.created_at, false)} · ${formatInteger(summary.symbols)} 只 / ${formatInteger(summary.daily_rows)} 行`,
        item.run_id,
      ),
    );
  });

  const dataset = currentDataset();
  select.value = dataset.run_id;
  const summary = dataset.summary || {};
  const quality = dataset.quality || {};
  const request = dataset.request || {};
  const symbols = Number(summary.symbols) || 0;
  const rows = Number(summary.daily_rows) || 0;
  $("#poolSymbolsCount").textContent = formatInteger(summary.symbols);
  $("#poolRowsCount").textContent = formatInteger(summary.daily_rows);
  $("#poolDateRange").textContent = summary.date_min && summary.date_max
    ? `${summary.date_min} ~ ${summary.date_max}`
    : "—";
  $("#poolFailures").textContent = formatInteger(summary.failures);

  [
    ["快照编号", dataset.run_id],
    ["采集时间", formatDate(dataset.created_at)],
    ["平均每股", symbols ? `${(rows / symbols).toFixed(1)} 行` : "—"],
    ["数据源", (summary.sources || []).join(" / ") || "—"],
    ["请求参数", `${formatInteger(request.universe_size)} 只 / ${formatInteger(request.history_days)} 日 · 实时行情 ${request.include_quotes === false ? "否" : "是"}`],
    ["质量检查", `重复键 ${formatInteger(quality.duplicate_keys)} · 非法价 ${formatInteger(quality.invalid_price_rows)} · 行情行 ${formatInteger(summary.quote_rows)}`],
  ].forEach(([label, value]) => {
    const row = node("div", "dataset-info-row");
    row.append(node("span", "", label), node("strong", "", value));
    info.append(row);
  });

  [
    ["日线 CSV", "daily"],
    ["行情 CSV", "quotes"],
    ["股票池 CSV", "universe"],
  ].forEach(([label, view]) => {
    const link = node("a", "text-link", label);
    link.href = `/api/runs/${encodeURIComponent(dataset.run_id)}/csv?view=${view}`;
    links.append(link);
  });
  const manifest = node("a", "text-link", "manifest");
  manifest.href = `/api/runs/${encodeURIComponent(dataset.run_id)}`;
  manifest.target = "_blank";
  manifest.rel = "noopener noreferrer";
  links.append(manifest);
  renderCoverage();
}

async function loadDatasets() {
  const payload = await api("/api/runs?limit=20");
  state.datasets = payload.items || [];
  renderPool();
  await loadDerived(currentDataset()?.run_id);
  await loadFactorData(currentDataset()?.run_id);
}

function appendDerivedLink(container, runId, label, path) {
  if (!path) return;
  const link = node("a", "text-link", label);
  link.href = `/api/runs/${encodeURIComponent(runId)}/derived-file?path=${encodeURIComponent(path)}`;
  container.append(link);
}

function renderCoverage() {
  const runId = currentDataset()?.run_id;
  const coverage = runId ? state.coverageByRun[runId] : null;
  const derive = runId ? state.exportsByRun[runId] : null;
  const stateBadge = $("#coverageState");
  const buckets = $("#coverageBuckets");
  const years = $("#coverageYears");
  const issues = $("#coverageIssues");
  const links = $("#coverageLinks");
  const buildButton = $("#buildExportsButton");
  clear(buckets);
  clear(years);
  clear(issues);
  clear(links);
  if (!coverage) {
    buildButton.hidden = !runId;
    stateBadge.textContent = runId ? "未生成派生层" : "暂无快照";
    stateBadge.classList.add("is-warning");
    ["#coverageSessions", "#coverageCompleteness", "#coverageMedianRows", "#coverageMissingSessions"].forEach((selector) => {
      $(selector).textContent = "—";
    });
    issues.append(node("p", "coverage-note", "当前快照尚未生成覆盖度与列式研究导出。"));
    return;
  }
  stateBadge.textContent = "审计完成";
  buildButton.hidden = Boolean(derive);
  stateBadge.classList.remove("is-warning");
  const summary = coverage.summary || {};
  const rows = summary.rows_per_symbol || {};
  $("#coverageSessions").textContent = formatInteger(summary.trading_sessions);
  $("#coverageCompleteness").textContent = summary.lifetime_completeness == null
    ? "—"
    : `${(Number(summary.lifetime_completeness) * 100).toFixed(2)}%`;
  $("#coverageMedianRows").textContent = formatInteger(rows.median);
  $("#coverageMissingSessions").textContent = formatInteger(summary.internal_missing_sessions);

  const coverageBuckets = coverage.coverage_buckets || [];
  const bucketMax = Math.max(1, ...coverageBuckets.map((item) => Number(item.symbols) || 0));
  coverageBuckets.forEach((item) => {
    const row = node("div", "coverage-bar-row");
    const track = node("div", "coverage-bar-track");
    const bar = node("div", "coverage-bar-fill");
    bar.style.width = `${Math.max(1, ((Number(item.symbols) || 0) / bucketMax) * 100)}%`;
    track.append(bar);
    row.append(
      node("span", "coverage-bar-label", `${item.bucket} 行`),
      track,
      node("strong", "coverage-bar-value", formatInteger(item.symbols)),
    );
    buckets.append(row);
  });

  const yearMax = Math.max(1, ...(coverage.by_year || []).map((item) => Number(item.daily_rows) || 0));
  (coverage.by_year || []).forEach((item) => {
    const row = node("div", "coverage-year-row");
    const track = node("div", "coverage-year-track");
    const bar = node("div", "coverage-year-fill");
    bar.style.width = `${Math.max(1, ((Number(item.daily_rows) || 0) / yearMax) * 100)}%`;
    track.append(bar);
    row.append(
      node("strong", "", item.year),
      track,
      node("span", "", `${formatInteger(item.trading_sessions)} 日 · ${formatInteger(item.daily_rows)} 行`),
    );
    years.append(row);
  });

  [
    ["少于 950 个交易日", `${formatInteger(summary.symbols_below_950_rows)} 只`],
    ["区间内存在会话缺口", `${formatInteger(summary.symbols_with_internal_gaps)} 只`],
    ["达到请求历史长度", `${formatInteger(summary.symbols_meeting_requested_history)} 只`],
  ].forEach(([label, value]) => {
    const row = node("div", "coverage-issue-row");
    row.append(node("span", "", label), node("strong", "", value));
    issues.append(row);
  });
  (coverage.limitations || []).slice(0, 3).forEach((text) => {
    issues.append(node("p", "coverage-note", text));
  });

  if (coverage.files) {
    [[
      "覆盖度明细 CSV", coverage.files.coverage_by_symbol?.path,
    ], ["年度覆盖 CSV", coverage.files.coverage_by_year?.path], [
      "覆盖度 JSON", coverage.files.coverage?.path,
    ]].forEach(([label, path]) => appendDerivedLink(links, runId, label, path));
  }
  if (derive?.parquet?.available !== false) {
    (derive?.parquet?.market_daily?.files || []).forEach((item) => {
      appendDerivedLink(
        links,
        runId,
        `${item.year} 日线 Parquet`,
        `parquet/market_daily/${item.path}`,
      );
    });
    if (derive?.parquet?.market_quotes) {
      appendDerivedLink(links, runId, "行情 Parquet", "parquet/market_quotes.parquet");
    }
    if (derive?.parquet?.universe) {
      appendDerivedLink(links, runId, "股票池 Parquet", "parquet/universe.parquet");
    }
  }
  if (derive?.duckdb?.status === "ready") {
    appendDerivedLink(links, runId, "DuckDB 数据库", derive.duckdb.path);
  }
}

async function loadDerived(runId) {
  const buildButton = $("#buildExportsButton");
  buildButton.disabled = false;
  buildButton.textContent = "生成研究导出";
  if (!runId) {
    renderCoverage();
    return;
  }
  if (state.coverageByRun[runId] && state.exportsByRun[runId]) {
    renderCoverage();
    return;
  }
  $("#coverageState").textContent = "读取中";
  const [coverageResult, exportResult] = await Promise.allSettled([
    api(`/api/runs/${encodeURIComponent(runId)}/coverage`),
    api(`/api/runs/${encodeURIComponent(runId)}/derived`),
  ]);
  if (coverageResult.status === "fulfilled") {
    state.coverageByRun[runId] = coverageResult.value;
  } else {
    delete state.coverageByRun[runId];
  }
  if (exportResult.status === "fulfilled") {
    state.exportsByRun[runId] = exportResult.value;
  } else {
    delete state.exportsByRun[runId];
  }
  if (currentDataset()?.run_id === runId) renderCoverage();
}

const FACTOR_GROUP_LABELS = {
  all: "全部",
  value: "价值",
  quality: "质量",
  growth: "成长",
  price: "量价 Alpha",
  risk: "风险中性",
};

function formatFactorValue(field, value, unit) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (field === "ln_cap" || field === "beta_20d") return number.toFixed(2);
  if (unit === "ratio") return `${(number * 100).toFixed(2)}%`;
  if (unit === "percent") return `${number.toFixed(2)}%`;
  return number.toFixed(2);
}

function appendFactorLink(container, runId, label, path) {
  if (!path) return;
  const link = node("a", "text-link", label);
  link.href = `/api/runs/${encodeURIComponent(runId)}/factor-file?path=${encodeURIComponent(path)}`;
  container.append(link);
}

function appendFundamentalLink(container, runId, fundId, label, path) {
  if (!path || !fundId) return;
  const link = node("a", "text-link", label);
  link.href = `/api/runs/${encodeURIComponent(runId)}/fundamentals-file?fund_id=${encodeURIComponent(fundId)}&path=${encodeURIComponent(path)}`;
  container.append(link);
}

function currentFactorSample() {
  const runId = currentDataset()?.run_id;
  return runId ? state.factorSamplesByRun[runId] : null;
}

function currentFactorFactors() {
  const runId = currentDataset()?.run_id;
  return runId ? state.factorsByRun[runId]?.by_factor || null : null;
}

function renderFactorGroups(factors) {
  const container = $("#factorGroupTabs");
  clear(container);
  if (!factors) return;
  const groups = ["all", ...new Set(Object.values(factors).map((item) => item.group))];
  groups.forEach((group) => {
    const button = node("button", "segment-button", FACTOR_GROUP_LABELS[group] || group);
    button.type = "button";
    button.dataset.group = group;
    button.setAttribute("role", "tab");
    button.classList.toggle("is-active", state.activeFactorGroup === group);
    button.addEventListener("click", () => {
      state.activeFactorGroup = group;
      renderFactorPanel();
    });
    container.append(button);
  });
}

function renderFactorDictionary(factors) {
  const container = $("#factorDictionary");
  const meta = $("#factorCatalogMeta");
  clear(container);
  if (!factors) {
    meta.textContent = "等待因子面板";
    container.append(node("div", "factor-dictionary-empty", "当前快照还没有因子面板。"));
    return;
  }
  const entries = Object.entries(factors).filter(([, item]) => (
    state.activeFactorGroup === "all" || item.group === state.activeFactorGroup
  ));
  meta.textContent = `${entries.length} / 24 因子 · 公告日对齐 · 可回溯`;
  if (!entries.length) {
    container.append(node("div", "factor-dictionary-empty", "该分组暂无因子。"));
    return;
  }
  entries.forEach(([field, item]) => {
    const entry = node("article", "factor-entry");
    const code = node("div", "factor-code");
    code.append(
      node("strong", "", field),
      node("span", "factor-coverage", `${(Number(item.coverage || 0) * 100).toFixed(1)}% / ${formatInteger(item.symbols)} 只`),
    );
    const copy = node("div", "factor-copy");
    const head = node("div", "factor-copy-head");
    head.append(
      node("strong", "", item.label),
      node("span", "", item.as_of_rule),
    );
    copy.append(
      head,
      node("p", "", item.meaning),
      node("p", "", item.usage),
      node("code", "", item.formula),
    );
    entry.append(code, copy);
    container.append(entry);
  });
}

function populateFactorSampleOptions(samples, factors) {
  const select = $("#factorStockSelect");
  const datalist = $("#factorStockOptions");
  clear(select);
  clear(datalist);
  const rows = samples?.rows || [];
  if (!rows.length) {
    select.append(new Option("暂无可抽查股票", ""));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  rows.forEach((row) => {
    select.append(new Option(`${row.name} · ${row.symbol}`, row.symbol));
    const option = node("option");
    option.value = `${row.symbol} ${row.name}`;
    datalist.append(option);
  });
  const selected = rows.some((row) => row.symbol === state.activeFactorSymbol)
    ? state.activeFactorSymbol
    : rows[0].symbol;
  state.activeFactorSymbol = selected;
  select.value = selected;
  $("#factorSnapshotDate").textContent = samples.latest_date || "—";
  if (!factors) return;
}

function renderFactorSnapshot(factors) {
  const summary = $("#factorSnapshotSummary");
  const container = $("#factorSnapshot");
  clear(summary);
  clear(container);
  const samples = currentFactorSample();
  const row = (samples?.rows || []).find((item) => item.symbol === state.activeFactorSymbol);
  if (!row || !factors) {
    container.append(node("div", "factor-dictionary-empty", "选择股票后查看最近交易日的因子值。"));
    return;
  }
  const status = row.factor_ready ? "24 因子齐全" : "部分因子缺失";
  const missing = row.missing_groups
    ? row.missing_groups.split(",").map((item) => FACTOR_GROUP_LABELS[item] || item).join(" / ")
    : "无";
  [
    ["股票", `${row.name} · ${row.symbol}`],
    ["申万行业", `${row.industry || "未分类"}`],
    ["财报期", row.report_date || "—"],
    ["公告日", row.ann_date || "—"],
    ["可用状态", status],
    ["缺失因子组", missing || "无"],
  ].forEach(([label, value]) => {
    const cell = node("div");
    cell.append(node("span", "", label), node("strong", "", value));
    summary.append(cell);
  });
  Object.entries(factors)
    .filter(([, item]) => state.activeFactorGroup === "all" || item.group === state.activeFactorGroup)
    .forEach(([field, item]) => {
      const factorRow = node("div", "factor-snapshot-row");
      factorRow.append(
        node("span", "", `${item.label} · ${field}`),
        node("strong", "", formatFactorValue(field, row[field], item.unit)),
        node("small", "", `${item.formula} · ${item.as_of_rule}`),
      );
      container.append(factorRow);
    });
}

function renderFactorPanel() {
  const runId = currentDataset()?.run_id;
  const factors = runId ? state.factorsByRun[runId] : null;
  const fundamentals = runId ? state.fundamentalsByRun[runId] : null;
  const available = Boolean(factors?.available);
  const snapshot = factors?.summary || {};
  const fundSnapshot = fundamentals?.snapshot || {};
  const fundSummary = fundSnapshot.summary || {};
  const buildButton = $("#buildFactorButton");
  const links = $("#factorLinks");
  clear(links);
  buildButton.hidden = available || !fundamentals?.available || !runId;
  buildButton.disabled = false;
  buildButton.textContent = "构建因子面板";

  if (!runId) {
    $("#factorState").textContent = "暂无快照";
    ["#factorReadyRatio", "#factorRows", "#factorSymbols", "#factorDate"].forEach((selector) => {
      $(selector).textContent = "—";
    });
    renderFactorGroups(null);
    renderFactorDictionary(null);
    renderFactorSnapshot(null);
    return;
  }

  if (fundamentals?.available) {
    const priceSources = factors?.source_summary?.unadjusted_price_rows_by_source || {};
    const sourceText = Object.entries(priceSources)
      .map(([source, rows]) => `${source} ${formatInteger(rows)} 行`)
      .join(" / ");
    $("#fundamentalState").textContent = `基本面 ${fundSnapshot.fund_id} · 财务 ${formatInteger(fundSummary.financial_rows)} 行 · 股本 ${formatInteger(fundSummary.share_capital_rows)} 行 · 分红 ${formatInteger(fundSummary.dividend_rows)} 行 · 不复权价 ${formatInteger(fundSummary.unadjusted_price_rows)} 行${sourceText ? ` · 来源 ${sourceText}` : ""}`;
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "财务 CSV", "financials.csv");
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "股本 CSV", "share_capital.csv");
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "分红 CSV", "dividends.csv");
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "不复权价 CSV", "prices_unadjusted.csv");
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "沪深300 CSV", "index_daily.csv");
    appendFundamentalLink(links, runId, fundSnapshot.fund_id, "公司资料 CSV", "profiles.csv");
  } else {
    $("#fundamentalState").textContent = "当前行情快照尚未采集基本面数据";
  }

  if (!available) {
    $("#factorState").textContent = "尚未构建";
    ["#factorReadyRatio", "#factorRows", "#factorSymbols", "#factorDate"].forEach((selector) => {
      $(selector).textContent = "—";
    });
    renderFactorGroups(null);
    renderFactorDictionary(null);
    renderFactorSnapshot(null);
    return;
  }

  $("#factorState").textContent = `可研究 ${(Number(snapshot.factor_ready_ratio || 0) * 100).toFixed(1)}%`;
  $("#factorReadyRatio").textContent = `${(Number(snapshot.factor_ready_ratio || 0) * 100).toFixed(1)}%`;
  $("#factorRows").textContent = formatInteger(snapshot.rows);
  $("#factorSymbols").textContent = formatInteger(snapshot.symbols);
  $("#factorDate").textContent = snapshot.date_max || "—";
  renderFactorGroups(factors.by_factor);
  renderFactorDictionary(factors.by_factor);
  populateFactorSampleOptions(currentFactorSample(), factors.by_factor);
  renderFactorSnapshot(factors.by_factor);

  appendFactorLink(links, runId, "因子字典 CSV", "factor_dictionary.csv");
  appendFactorLink(links, runId, "因子 Manifest", "factor_manifest.json");
  appendFactorLink(links, runId, "覆盖度 JSON", "factor_coverage.json");
  appendFactorLink(links, runId, "季度财务 Parquet", "financials_quarterly.parquet");
  appendFactorLink(links, runId, "因子 DuckDB", "factors.duckdb");
  (fundamentalFiles(factors) || []).forEach(([year, path]) => {
    appendFactorLink(links, runId, `${year} 因子 Parquet`, path);
  });
}

function fundamentalFiles(factors) {
  const files = factors?.files || {};
  const years = Object.keys(files)
    .filter((path) => path.startsWith("factor_panel/year="))
    .map((path) => [path.match(/year=(\d{4})/)?.[1], path])
    .filter(([year]) => year);
  return years;
}

async function loadFactorData(runId) {
  if (!runId) {
    renderFactorPanel();
    return;
  }
  $("#factorState").textContent = "读取中";
  const [factorResult, fundResult] = await Promise.allSettled([
    api(`/api/runs/${encodeURIComponent(runId)}/factors`),
    api(`/api/runs/${encodeURIComponent(runId)}/fundamentals`),
  ]);
  if (factorResult.status === "fulfilled") {
    state.factorsByRun[runId] = factorResult.value;
  } else {
    delete state.factorsByRun[runId];
  }
  if (fundResult.status === "fulfilled") {
    state.fundamentalsByRun[runId] = fundResult.value;
  } else {
    delete state.fundamentalsByRun[runId];
  }
  if (state.factorsByRun[runId]?.available && !state.factorSamplesByRun[runId]) {
    try {
      state.factorSamplesByRun[runId] = await api(`/api/runs/${encodeURIComponent(runId)}/factor-sample?limit=30`);
    } catch (error) {
      toast(error.message, true);
    }
  }
  if (currentDataset()?.run_id === runId) renderFactorPanel();
}

async function refreshFactorSample() {
  const runId = currentDataset()?.run_id;
  if (!runId) return;
  const query = $("#factorStockQuery").value.trim();
  try {
    state.factorSamplesByRun[runId] = await api(
      `/api/runs/${encodeURIComponent(runId)}/factor-sample?limit=30&q=${encodeURIComponent(query)}`,
    );
    state.activeFactorSymbol = null;
    renderFactorPanel();
  } catch (error) {
    toast(error.message, true);
  }
}

async function buildFactorPanel() {
  const runId = currentDataset()?.run_id;
  if (!runId) return;
  const button = $("#buildFactorButton");
  button.disabled = true;
  button.textContent = "构建中";
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ mode: "factors", run_id: runId, rebuild: false }),
    });
    state.jobKind = "factors";
    renderJob(job);
    pollJob(job.id);
  } catch (error) {
    button.disabled = false;
    button.textContent = "构建因子面板";
    toast(error.message, true);
  }
}

async function buildResearchExports() {
  const runId = currentDataset()?.run_id;
  if (!runId) return;
  const button = $("#buildExportsButton");
  button.disabled = true;
  button.textContent = "生成中";
  $("#coverageState").textContent = "正在生成";
  try {
    const result = await api(`/api/runs/${encodeURIComponent(runId)}/exports`, {
      method: "POST",
      body: JSON.stringify({ rebuild: false }),
    });
    delete state.coverageByRun[runId];
    delete state.exportsByRun[runId];
    await loadDerived(runId);
    toast(`研究导出已生成：${formatInteger(result.parquet_rows)} 行`);
  } catch (error) {
    button.disabled = false;
    button.textContent = "重新生成";
    $("#coverageState").textContent = "生成失败";
    toast(error.message, true);
  }
}

async function submitCollection(event) {
  event.preventDefault();
  const symbolText = $("#poolSymbols").value.trim();
  const universeSize = Number($("#universeSize").value || 3000);
  const historyDays = Number($("#poolHistoryDays").value || 1000);
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        mode: "fixed",
        universe_size: universeSize,
        history_days: historyDays,
        include_quotes: true,
        symbols: symbolText,
        query: "研究数据池：公开行情批量采集",
      }),
    });
    state.jobKind = "dataset";
    renderJob(job);
    pollJob(job.id);
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
  const isDataset = state.jobKind === "dataset";
  const isFactors = state.jobKind === "factors";
  $("#jobTitle").textContent = status === "complete"
    ? (isFactors ? "因子面板完成" : isDataset ? "采集完成" : "研判完成")
    : (isFactors ? "正在构建因子面板" : isDataset ? "正在采集数据池" : "正在研判");
  $("#jobStage").textContent = labels[status] || status;
  $("#jobPercent").textContent = `${Math.round(Number(job.progress) || 0)}%`;
  $("#progressBar").style.width = `${Math.max(0, Math.min(100, Number(job.progress) || 0))}%`;
  renderTrace(job.events || []);
  const running = status === "queued" || status === "running";
  $("#collectButton").disabled = running;
  $("#analyzeButton").disabled = running;
  $("#buildFactorButton").disabled = running;
  $("#collectButton").textContent = running && isDataset ? "采集中" : "采集数据池";
  $("#analyzeButton").textContent = running && !isDataset ? "研判中" : "生成研究报告";
  if (status === "failed") {
    $("#jobTitle").textContent = isDataset ? "采集未完成" : "研判未完成";
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
    state.jobKind = "risk";
    renderJob(job);
    pollJob(job.id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function pollJob(jobId) {
  window.clearTimeout(state.pollTimer);
  const jobKind = state.jobKind;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJob(job);
    if (job.status === "queued" || job.status === "running") {
      state.pollTimer = window.setTimeout(() => pollJob(jobId), 850);
      return;
    }
    if (job.status === "complete" && job.result) {
      $("#jobPanel").hidden = true;
      const result = job.result || {};
      if (jobKind === "factors") {
        state.jobKind = null;
        delete state.factorsByRun[result.raw_run_id];
        delete state.factorSamplesByRun[result.raw_run_id];
        await loadFactorData(result.raw_run_id || currentDataset()?.run_id);
        toast(`因子面板已生成：${formatInteger(result.summary?.rows)} 行`);
        return;
      }
      if (jobKind === "dataset") {
        state.activeDatasetId = result.run_id || null;
        state.jobKind = null;
        await loadDatasets();
        toast(`数据池已保存：${formatInteger(result.summary?.daily_rows)} 行`);
        return;
      }
      renderAudit(job.result);
      await loadHistory();
      renderAudit(job.result);
      state.jobKind = null;
      toast("研究报告已生成");
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
    row.classList.toggle("is-selected", item.symbol === state.selectedSymbol);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.addEventListener("click", () => {
      state.selectedSymbol = item.symbol;
      renderReport();
      renderStockList(assessments);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      state.selectedSymbol = item.symbol;
      renderReport();
      renderStockList(assessments);
    });
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

function selectedAssessment() {
  const assessments = state.activeAudit?.assessments || [];
  return assessments.find((item) => item.symbol === state.selectedSymbol) || assessments[0] || null;
}

function renderReport() {
  const audit = state.activeAudit;
  const assessments = audit?.assessments || [];
  const tabs = $("#stockTabs");
  clear(tabs);
  if (!assessments.length) return;
  const current = selectedAssessment();
  state.selectedSymbol = current.symbol;

  assessments.forEach((item) => {
    const button = node("button", "stock-tab");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.dataset.level = item.level;
    button.classList.toggle("is-active", item.symbol === current.symbol);
    button.append(node("strong", "", item.name), node("span", "", item.symbol));
    button.addEventListener("click", () => {
      state.selectedSymbol = item.symbol;
      renderReport();
      renderStockList(assessments);
    });
    tabs.append(button);
  });

  const identity = $("#reportIdentity");
  clear(identity);
  const title = node("div", "report-title");
  const badge = node("span", "level-badge");
  setLevelBadge(badge, current.level, current.level_label);
  title.append(
    node("h3", "", `${current.name} ${current.symbol}`),
    badge,
  );
  const validationStatus = current.validation?.status || "warn";
  const validationLabel =
    validationStatus === "pass" ? "核对通过" : validationStatus === "fail" ? "核对失败" : "需要关注";
  const crossStatus = current.validation?.cross_source?.status || "unavailable";
  const crossLabel =
    crossStatus === "verified" ? "一致" : crossStatus === "mismatch" ? "偏差超阈值" : "不可用";
  const facts = node("div", "report-facts");
  [
    ["最新价", formatPrice(current.price)],
    ["当日涨跌", formatPct(current.change_pct, 2)],
    ["风险分", current.score ?? "—"],
    ["数据核对", validationLabel],
    ["双源核价", crossLabel],
  ].forEach(([label, value]) => {
    const cell = node("div", "report-fact");
    cell.append(node("span", "", label), node("strong", "", value));
    facts.append(cell);
  });
  identity.append(title, facts);

  const chain = $("#researchChain");
  clear(chain);
  (current.research_steps || []).forEach((step) => {
    const item = node("li", "research-step");
    item.dataset.status = step.status || "done";
    const head = node("div", "research-step-head");
    head.append(
      node("span", "step-index", step.step),
      node("strong", "", step.action),
      node("span", "quiet-badge", step.actor),
      node("span", "step-status", step.status === "warn" ? "需复核" : "完成"),
    );
    const grid = node("div", "step-grid");
    [["输入", step.input], ["输出", step.output]].forEach(([label, value]) => {
      const cell = node("div", "step-cell");
      cell.append(node("span", "", label), node("p", "", value));
      grid.append(cell);
    });
    item.append(head, grid);
    chain.append(item);
  });

  const table = $("#indicatorTable");
  clear(table);
  (current.indicator_details || []).forEach((indicator) => {
    const row = node("article", "indicator-row");
    const head = node("div", "indicator-head");
    head.append(
      node("strong", "", indicator.label),
      node("span", "indicator-value", indicator.display),
      node("span", "quiet-badge", indicator.window),
    );
    const grid = node("dl", "indicator-grid");
    [
      ["公式与输入", indicator.formula],
      ["衡量含义", indicator.meaning],
      ["后续建模用法", indicator.usage],
      ["证据来源", indicator.source],
    ].forEach(([label, value]) => {
      const cell = node("div", "indicator-cell");
      cell.append(node("dt", "", label), node("dd", "", value));
      grid.append(cell);
    });
    row.append(head, grid);
    table.append(row);
  });
}

function renderResearchDesign(audit) {
  const container = $("#researchDesign");
  clear(container);
  const design = audit?.research_design || {};
  const labels = {
    universe: "研究样本",
    labels: "预测标签",
    features: "特征工程",
    split: "时间切分",
    leakage_checks: "泄漏检查",
    evaluation: "模型评估",
    portfolio: "组合落地",
  };
  Object.entries(design).forEach(([key, value]) => {
    const cell = node("div", "design-item");
    cell.append(node("dt", "", labels[key] || key), node("dd", "", value));
    container.append(cell);
  });
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
  if (!assessments.some((item) => item.symbol === state.selectedSymbol)) {
    state.selectedSymbol = assessments[0]?.symbol || null;
  }
  renderReport();
  renderResearchDesign(audit);
  $("#auditId").textContent = audit.audit_id || "—";
  $("#runId").textContent = audit.evidence?.run_id || "—";
  populateHistory();
  if (audit.audit_id) $("#historySelect").value = audit.audit_id;
}

function runLayoutCheck() {
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const root = document.documentElement;
  const overflowX = Math.max(root.scrollWidth, document.body.scrollWidth) - viewport.width;
  const overflowEls = [];
  document.querySelectorAll("body *").forEach((element) => {
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return;
    const scrollable = ["auto", "scroll"].includes(style.overflowX);
    if (!scrollable && element.scrollWidth > element.clientWidth + 2) {
      overflowEls.push({
        tag: element.tagName,
        id: element.id,
        className: String(element.className || ""),
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      });
    }
  });

  const selectors = [
    ".app-header",
    ".pool-section",
    ".intake",
    ".job-panel",
    ".verdict",
    ".brief-section",
    ".report-section",
    ".stocks-section",
    ".detail-section",
    ".design-section",
    ".audit-footer",
  ];
  const boxes = selectors
    .map((selector) => {
      const element = document.querySelector(selector);
      if (!element || element.hidden) return null;
      const rect = element.getBoundingClientRect();
      return { selector, top: rect.top + window.scrollY, bottom: rect.bottom + window.scrollY };
    })
    .filter(Boolean);
  const overlaps = [];
  for (let index = 0; index < boxes.length - 1; index += 1) {
    if (boxes[index].bottom > boxes[index + 1].top + 2) {
      overlaps.push([boxes[index].selector, boxes[index + 1].selector]);
    }
  }

  const report = {
    viewport,
    overflowX,
    overflowEls: overflowEls.slice(0, 20),
    overlaps,
    indicators: document.querySelectorAll(".indicator-row").length,
    steps: document.querySelectorAll(".research-step").length,
  };
  const pre = document.createElement("pre");
  pre.id = "layoutReport";
  pre.textContent = JSON.stringify(report);
  pre.style.display = "none";
  document.body.append(pre);
}

async function init() {
  $("#analysisForm").addEventListener("submit", submitAnalysis);
  $("#dataPoolForm").addEventListener("submit", submitCollection);
  $("#datasetSelect").addEventListener("change", (event) => {
    state.activeDatasetId = event.target.value;
    renderPool();
    loadDerived(state.activeDatasetId);
    loadFactorData(state.activeDatasetId);
  });
  $("#buildExportsButton").addEventListener("click", buildResearchExports);
  $("#buildFactorButton").addEventListener("click", buildFactorPanel);
  $("#factorSampleButton").addEventListener("click", refreshFactorSample);
  $("#factorStockQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      refreshFactorSample();
    }
  });
  $("#factorStockSelect").addEventListener("change", (event) => {
    state.activeFactorSymbol = event.target.value;
    renderFactorSnapshot(currentFactorFactors());
  });
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
    await loadDatasets();
    await loadHistory();
    if (state.audits.length) renderAudit(state.audits[0]);
    if (new URLSearchParams(window.location.search).has("layout")) {
      window.setTimeout(runLayoutCheck, 800);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
