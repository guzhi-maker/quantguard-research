"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STORAGE_WATCHLIST = "quantguard.research.v3.watchlist";
const STORAGE_HISTORY = "quantguard.research.v3.history";
const MODEL_LABELS = {
  factor: "多因子基线",
  ridge: "Ridge 回归",
  random_forest: "随机森林",
  gradient_boosting: "梯度提升",
};
const COST_LABELS = {
  commission: "比例佣金",
  minimum_commission_extra: "最低佣金增量",
  transfer_fee: "过户费",
  stamp_tax: "印花税",
  slippage: "滑点",
  impact: "冲击成本",
  total: "总成本",
};
const state = {
  dataMode: "live",
  result: null,
  riskResult: null,
  watchlist: [],
  history: [],
  chartRows: [],
  chartGeometry: null,
  activeTab: "audit",
  activeSuggestion: -1,
  suggestions: [],
  controller: null,
  searchController: null,
  searchTimer: null,
  toastTimer: null,
  elapsedTimer: null,
  runStartedAt: 0,
  tourStep: 0,
  datasetView: "raw",
  datasetOffset: 0,
  datasetLimit: 50,
  datasetPage: null,
  datasetController: null,
};

const elements = {
  researchForm: $("#researchForm"),
  capital: $("#capitalInput"),
  universe: $("#universeInput"),
  days: $("#daysInput"),
  topN: $("#topNInput"),
  horizon: $("#horizonSelect"),
  model: $("#modelSelect"),
  rebalance: $("#rebalanceSelect"),
  exitDays: $("#exitDaysInput"),
  participation: $("#participationInput"),
  industryCap: $("#industryCapInput"),
  contribution: $("#contributionInput"),
  years: $("#yearsInput"),
  runButton: $("#runButton"),
  runHint: $("#runHint"),
  runStatus: $("#runStatus"),
  runStatusText: $("#runStatusText"),
  sourceDot: $("#sourceDot"),
  sourceLabel: $("#sourceLabel"),
  sidebarSourceDetail: $("#sidebarSourceDetail"),
  headerAsOf: $("#headerAsOf"),
  liveMarketBody: $("#liveMarketBody"),
  liveMarketTime: $("#liveMarketTime"),
  globalNotice: $("#globalNotice"),
  noticeText: $("#noticeText"),
  noticeClose: $("#noticeClose"),
  toast: $("#toast"),
  experimentBadge: $("#experimentBadge"),
  researchQuestion: $("#researchQuestion"),
  researchVerdict: $("#researchVerdict"),
  verdictTitle: $("#verdictTitle"),
  verdictText: $("#verdictText"),
  verdictTag: $("#verdictTag"),
  metricHoldoutIc: $("#metricHoldoutIc"),
  metricHoldoutExcess: $("#metricHoldoutExcess"),
  metricCostDrag: $("#metricCostDrag"),
  metricDrawdown: $("#metricDrawdown"),
  metricRows: $("#metricRows"),
  metricCapacity: $("#metricCapacity"),
  selectedModelLabel: $("#selectedModelLabel"),
  selectionReason: $("#selectionReason"),
  developmentWindow: $("#developmentWindow"),
  holdoutWindow: $("#holdoutWindow"),
  rollingProtocol: $("#rollingProtocol"),
  predictionDate: $("#predictionDate"),
  autoSelectionBadge: $("#autoSelectionBadge"),
  performanceBadge: $("#performanceBadge"),
  performanceBody: $("#performanceBody"),
  validationIc: $("#validationIc"),
  holdoutIc: $("#holdoutIc"),
  topCapture: $("#topCapture"),
  holdoutR2: $("#holdoutR2"),
  evidenceNote: $("#evidenceNote"),
  overviewActions: $("#overviewActions"),
  researchSourceBadge: $("#researchSourceBadge"),
  qualityLoaded: $("#qualityLoaded"),
  qualityRequested: $("#qualityRequested"),
  qualityDates: $("#qualityDates"),
  qualityDateRange: $("#qualityDateRange"),
  qualityDuplicate: $("#qualityDuplicate"),
  qualityInvalid: $("#qualityInvalid"),
  qualityFilled: $("#qualityFilled"),
  qualityMissing: $("#qualityMissing"),
  qualityModeBadge: $("#qualityModeBadge"),
  dataSourceDetail: $("#dataSourceDetail"),
  dataAdjustment: $("#dataAdjustment"),
  dataMarketDate: $("#dataMarketDate"),
  dataSnapshotVersion: $("#dataSnapshotVersion"),
  excludedShortHistory: $("#excludedShortHistory"),
  failedSymbols: $("#failedSymbols"),
  cleaningRules: $("#cleaningRules"),
  industryAuditBars: $("#industryAuditBars"),
  qualityTableBody: $("#qualityTableBody"),
  datasetWorkbenchId: $("#datasetWorkbenchId"),
  datasetExperimentId: $("#datasetExperimentId"),
  datasetRawRows: $("#datasetRawRows"),
  datasetModelRows: $("#datasetModelRows"),
  datasetDateSpan: $("#datasetDateSpan"),
  datasetSymbolCount: $("#datasetSymbolCount"),
  datasetFilterForm: $("#datasetFilterForm"),
  datasetQuery: $("#datasetQuery"),
  datasetDateFrom: $("#datasetDateFrom"),
  datasetDateTo: $("#datasetDateTo"),
  datasetApplyFilter: $("#datasetApplyFilter"),
  datasetResetFilter: $("#datasetResetFilter"),
  datasetStatus: $("#datasetStatus"),
  datasetHead: $("#datasetHead"),
  datasetBody: $("#datasetBody"),
  datasetPageInfo: $("#datasetPageInfo"),
  datasetPreviousPage: $("#datasetPreviousPage"),
  datasetNextPage: $("#datasetNextPage"),
  datasetRawDownload: $("#datasetRawDownload"),
  datasetModelDownload: $("#datasetModelDownload"),
  featureCatalogBody: $("#featureCatalogBody"),
  featureMissingBars: $("#featureMissingBars"),
  correlationDate: $("#correlationDate"),
  correlationBody: $("#correlationBody"),
  labelHorizonBadge: $("#labelHorizonBadge"),
  labelFormula: $("#labelFormula"),
  splitDevelopmentText: $("#splitDevelopmentText"),
  splitHoldoutText: $("#splitHoldoutText"),
  sampleRowsDevelopment: $("#sampleRowsDevelopment"),
  sampleRowsHoldout: $("#sampleRowsHoldout"),
  foldCount: $("#foldCount"),
  retrainProtocol: $("#retrainProtocol"),
  labelDistribution: $("#labelDistribution"),
  marketGroupBody: $("#marketGroupBody"),
  foldBody: $("#foldBody"),
  leakageList: $("#leakageList"),
  modelSelectedBadge: $("#modelSelectedBadge"),
  modelComparisonBody: $("#modelComparisonBody"),
  hyperparameterList: $("#hyperparameterList"),
  importanceModel: $("#importanceModel"),
  importanceBars: $("#importanceBars"),
  fitCountBadge: $("#fitCountBadge"),
  rollingFitsBody: $("#rollingFitsBody"),
  navChart: $("#navChart"),
  navChartShell: $("#navChartShell"),
  chartEmpty: $("#chartEmpty"),
  navTooltip: $("#navTooltip"),
  tooltipDate: $("#tooltipDate"),
  tooltipValue: $("#tooltipValue"),
  chartStart: $("#chartStart"),
  chartEnd: $("#chartEnd"),
  chartAriaText: $("#chartAriaText"),
  backtestPeriodBody: $("#backtestPeriodBody"),
  costTotalBadge: $("#costTotalBadge"),
  costBody: $("#costBody"),
  capacityBadge: $("#capacityBadge"),
  stressBody: $("#stressBody"),
  portfolioCapitalBadge: $("#portfolioCapitalBadge"),
  portfolioInvested: $("#portfolioInvested"),
  portfolioCash: $("#portfolioCash"),
  portfolioBuildCost: $("#portfolioBuildCost"),
  portfolioBuildCostBps: $("#portfolioBuildCostBps"),
  portfolioMinimumCommission: $("#portfolioMinimumCommission"),
  portfolioExitDays: $("#portfolioExitDays"),
  portfolioIndustryCap: $("#portfolioIndustryCap"),
  syncTargetButton: $("#syncTargetButton"),
  portfolioHoldingsBody: $("#portfolioHoldingsBody"),
  riskActionCount: $("#riskActionCount"),
  portfolioRiskActions: $("#portfolioRiskActions"),
  industryExposureBars: $("#industryExposureBars"),
  capacityLimit: $("#capacityLimit"),
  capacityHeadroom: $("#capacityHeadroom"),
  executionDaily: $("#executionDaily"),
  executionLargest: $("#executionLargest"),
  stockSearchForm: $("#stockSearchForm"),
  stockSearch: $("#stockSearch"),
  searchSuggestions: $("#searchSuggestions"),
  clearWatchlistButton: $("#clearWatchlistButton"),
  analyzeRiskButton: $("#analyzeRiskButton"),
  watchlistStatus: $("#watchlistStatus"),
  watchlistBody: $("#watchlistBody"),
  riskReviewResult: $("#riskReviewResult"),
  archiveExperimentId: $("#archiveExperimentId"),
  archiveModel: $("#archiveModel"),
  archiveHoldoutIc: $("#archiveHoldoutIc"),
  archiveExcess: $("#archiveExcess"),
  archiveMode: $("#archiveMode"),
  archiveGeneratedAt: $("#archiveGeneratedAt"),
  archiveQuestion: $("#archiveQuestion"),
  archiveData: $("#archiveData"),
  archiveFeatures: $("#archiveFeatures"),
  archiveSplit: $("#archiveSplit"),
  archiveModels: $("#archiveModels"),
  archiveConclusion: $("#archiveConclusion"),
  limitationsList: $("#limitationsList"),
  disclaimerText: $("#disclaimerText"),
  historyCount: $("#historyCount"),
  experimentHistory: $("#experimentHistory"),
  exportMarkdownButton: $("#exportMarkdownButton"),
  exportJsonButton: $("#exportJsonButton"),
  openTourButton: $("#openTourButton"),
  tourOpenButton: $("#tourOpenButton"),
  footerSource: $("#footerSource"),
  tourOverlay: $("#tourOverlay"),
  tourStepLabel: $("#tourStepLabel"),
  tourTitle: $("#tourTitle"),
  tourBody: $("#tourBody"),
  tourPoints: $("#tourPoints"),
  tourProgressBar: $("#tourProgressBar"),
  tourPreviousButton: $("#tourPreviousButton"),
  tourNextButton: $("#tourNextButton"),
};

const TOUR_STEPS = [
  {
    title: "研究问题",
    body: "先给出可检验假设：模型预测的是未来横截面相对收益，不是确定性涨跌。结论必须同时看开发期与留出期。",
    target: "#overview",
    tab: null,
    points: ["问题、标签与评价指标先于模型", "自动选择不会回看留出期改参数"],
  },
  {
    title: "数据质量与血缘",
    body: "展示公开证券行情的数据源、有效股票与日期、重复键、非法价格、前向填充、短历史和缺失率。",
    target: "#research",
    tab: "audit",
    points: ["当前股票池存在幸存者偏差", "清洗规则进入研究档案和导出报告"],
  },
  {
    title: "特征工程与标签",
    body: "17 个价格、波动、流动性和市场状态特征只使用信号日及之前的数据。标签未来窗口未结束前，样本不能进入训练。",
    target: "#research",
    tab: "features",
    points: ["截面 Z-Score 并截断极端值", "未来超额只在标签与评估中使用"],
  },
  {
    title: "模型训练与样本外评估",
    body: "同一时间切分下对照因子基线、Ridge、随机森林与梯度提升。看 Rank IC、R²、Top-20% 捕获和留出期是否复现。",
    target: "#research",
    tab: "training",
    points: ["预处理只在训练折拟合", "留出期每 20 个交易日滚动重训"],
  },
  {
    title: "组合、成本与风险",
    body: "预测得分转换为整手持仓，再计入容量、行业上限、佣金、税费、滑点和冲击。样本外净超额与风险行动一起汇报。",
    target: "#portfolio",
    tab: null,
    points: ["小资金整手约束会留下现金", "不承诺收益，负结果同样保留"],
  },
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isFiniteNumber(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

function formatNumber(value, digits = 2) {
  if (!isFiniteNumber(value)) return "--";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatInteger(value) {
  if (!isFiniteNumber(value)) return "--";
  return Math.round(Number(value)).toLocaleString("zh-CN");
}

function formatPct(value, digits = 1, signed = false) {
  if (!isFiniteNumber(value)) return "--";
  const number = Number(value) * 100;
  const sign = signed && number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}%`;
}

function formatDecimal(value, digits = 2) {
  if (!isFiniteNumber(value)) return "--";
  return Number(value).toFixed(digits);
}

function formatMoney(value) {
  if (!isFiniteNumber(value)) return "--";
  const number = Number(value);
  const absolute = Math.abs(number);
  const sign = number < 0 ? "-" : "";
  if (absolute >= 100_000_000) return `${sign}${(absolute / 100_000_000).toFixed(2)} 亿`;
  if (absolute >= 10_000) return `${sign}${(absolute / 10_000).toFixed(2)} 万`;
  return `${sign}${absolute.toFixed(0)} 元`;
}

function formatSignedMoney(value) {
  if (!isFiniteNumber(value)) return "--";
  return `${Number(value) > 0 ? "+" : ""}${formatMoney(value)}`;
}

function metricTone(value, invert = false) {
  if (!isFiniteNumber(value)) return "";
  const number = Number(value);
  if (invert) {
    if (number < 0) return "is-positive";
    if (number > 0) return "is-negative";
    return "";
  }
  if (number > 0) return "is-positive";
  if (number < 0) return "is-negative";
  return "";
}

function setMetric(element, text, tone = "") {
  if (!element) return;
  element.textContent = text;
  element.classList.remove("is-positive", "is-negative", "is-warning");
  if (tone) element.classList.add(tone);
}

function setBadge(element, text, tone = "") {
  if (!element) return;
  element.textContent = text;
  element.classList.remove("is-positive", "is-negative", "is-warning");
  if (tone) element.classList.add(tone);
}

function showToast(message) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3200);
}

function showNotice(message) {
  elements.noticeText.textContent = message;
  elements.globalNotice.hidden = false;
}

function clearNotice() {
  elements.globalNotice.hidden = true;
}

function setRunState(kind, label) {
  elements.runStatus.dataset.state = kind;
  elements.runStatusText.textContent = label;
}

function updateElapsed() {
  if (!state.runStartedAt) return;
  const elapsed = (performance.now() - state.runStartedAt) / 1000;
  elements.runHint.textContent = `已运行 ${elapsed.toFixed(1)} 秒 · 数据、特征、训练与回测保持同一口径`;
}

function setLoading(loading) {
  elements.runButton.disabled = loading;
  elements.runButton.classList.toggle("is-loading", loading);
  const label = $("#runButton span");
  if (label) label.textContent = loading ? "正在运行研究" : "运行完整研究";
  window.clearInterval(state.elapsedTimer);
  if (loading) {
    state.runStartedAt = performance.now();
    updateElapsed();
    state.elapsedTimer = window.setInterval(updateElapsed, 250);
    setRunState("running", "数据 → 特征 → 模型 → 回测");
  } else {
    window.clearInterval(state.elapsedTimer);
    state.elapsedTimer = null;
    state.runStartedAt = 0;
  }
}

function updateModeUI() {
  $$(".mode-button").forEach((button) => {
    const active = button.dataset.mode === state.dataMode;
    button.classList.toggle("is-active", active);
    if (button.tagName === "BUTTON") {
      button.setAttribute("aria-pressed", String(active));
    }
  });
  elements.sourceDot.classList.remove("is-live", "is-error");
  elements.sourceLabel.textContent = state.result ? "实时行情已载入" : "实时行情待请求";
  elements.sidebarSourceDetail.textContent = state.result?.meta?.source
    ? `${state.result.meta.source} · 前复权`
    : "公开实时行情 · 前复权";
  elements.sourceDot.classList.add("is-live");
  elements.footerSource.textContent = "行情来自公开证券数据接口，可能存在延迟；历史结果不代表未来收益。";
}

async function loadLiveMarketSnapshot() {
  try {
    const response = await fetch("/api/market/snapshot?limit=12&data_mode=live", { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `行情抓取失败 (${response.status})`);
    const items = Array.isArray(payload.items) ? payload.items : [];
    elements.liveMarketBody.innerHTML = items.length
      ? items.map((item) => {
          const tone = metricTone(item.change_pct);
          const toneClass = tone === "is-positive" ? "is-positive-text" : tone === "is-negative" ? "is-negative-text" : "";
          return `
            <tr>
              <td class="text-cell">${escapeHtml(item.symbol)}</td>
              <td><span class="stock-cell"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.industry || "A股")}</small></span></td>
              <td>${formatNumber(item.price, 2)}</td>
              <td class="${toneClass}">${formatPct(item.change_pct, 2, true)}</td>
              <td>${formatMoney(item.amount)}</td>
              <td>${formatPct(item.turnover_rate, 2)}</td>
            </tr>
          `;
        }).join("")
      : '<tr><td colspan="6" class="table-empty">当前没有返回行情记录。</td></tr>';
    elements.liveMarketTime.textContent = `${payload.source || "公开证券行情"} · ${payload.fetched_at || "刚刚更新"}`;
    if (!state.result && elements.headerAsOf.textContent === "--" && payload.fetched_at) {
      elements.headerAsOf.textContent = payload.fetched_at.slice(0, 10);
    }
    elements.sourceLabel.textContent = `实时行情已载入 · ${items.length} 只`;
    elements.sidebarSourceDetail.textContent = `${payload.source || "公开证券行情"} · ${payload.fetched_at || "刚刚更新"}`;
    elements.sourceDot.classList.remove("is-error");
    elements.sourceDot.classList.add("is-live");
  } catch (error) {
    elements.liveMarketBody.innerHTML = `<tr><td colspan="6" class="table-empty">${escapeHtml(`实时行情暂不可用：${error.message}`)}</td></tr>`;
    elements.liveMarketTime.textContent = "连接失败，可重新运行或检查网络";
    elements.sourceLabel.textContent = "实时行情抓取失败";
    elements.sidebarSourceDetail.textContent = "未使用演示数据替代";
    elements.sourceDot.classList.remove("is-live");
    elements.sourceDot.classList.add("is-error");
  }
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  $$(".tab-button").forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$(".tab-panel").forEach((panel) => {
    const active = panel.dataset.panel === tabName;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
}

function readParameters() {
  const capitalWan = Number(elements.capital.value);
  const universeSize = Number(elements.universe.value);
  const days = Number(elements.days.value);
  const topN = Number(elements.topN.value);
  const exitDays = Number(elements.exitDays.value);
  const participation = Number(elements.participation.value);
  const industryCap = Number(elements.industryCap.value);
  const contribution = Number(elements.contribution.value);
  const years = Number(elements.years.value);

  if (!Number.isFinite(capitalWan) || capitalWan < 0.1 || capitalWan > 50_000_000) {
    throw new Error("研究资金需在 0.1 万元到 5,000 亿元之间。");
  }
  if (!Number.isInteger(universeSize) || universeSize < 10 || universeSize > 200) {
    throw new Error("股票池上限需在 10 到 200 只之间。");
  }
  if (!Number.isInteger(days) || days < 180 || days > 1000) {
    throw new Error("历史窗口需在 180 到 1000 个交易日之间。");
  }
  if (!Number.isInteger(topN) || topN < 3 || topN > Math.min(20, universeSize)) {
    throw new Error("目标持仓数需在 3 到 20 只之间，且不超过股票池数量。");
  }
  if (!Number.isInteger(exitDays) || exitDays < 1 || exitDays > 20) {
    throw new Error("清仓窗口需在 1 到 20 个交易日之间。");
  }
  if (!Number.isFinite(participation) || participation < 1 || participation > 30) {
    throw new Error("ADV 参与率需在 1% 到 30% 之间。");
  }
  if (!Number.isFinite(industryCap) || industryCap < 10 || industryCap > 50) {
    throw new Error("行业上限需在 10% 到 50% 之间。");
  }
  if (!Number.isFinite(contribution) || contribution < 0 || contribution > 100000) {
    throw new Error("每月投入需在 0 到 10 万元之间。");
  }
  if (!Number.isInteger(years) || years < 1 || years > 10) {
    throw new Error("积累情景年限需在 1 到 10 年之间。");
  }

  return {
    data_mode: state.dataMode,
    capital: Math.round(capitalWan * 10_000 * 100) / 100,
    universe_size: universeSize,
    days,
    top_n: topN,
    label_horizon: Number(elements.horizon.value),
    model: elements.model.value,
    rebalance: elements.rebalance.value,
    exit_days: exitDays,
    participation_rate: participation / 100,
    industry_cap: industryCap / 100,
    monthly_contribution: contribution,
    horizon_years: years,
  };
}

function applyParameters(params) {
  if (!params) return;
  state.dataMode = "live";
  if (isFiniteNumber(params.capital)) {
    elements.capital.value = String(Number(params.capital) / 10_000);
  }
  const mappings = [
    [elements.universe, params.universe_size],
    [elements.days, params.days ?? params.history_days],
    [elements.topN, params.top_n],
    [elements.exitDays, params.exit_days],
    [elements.contribution, params.monthly_contribution],
    [elements.years, params.horizon_years],
  ];
  mappings.forEach(([element, value]) => {
    if (isFiniteNumber(value)) element.value = String(value);
  });
  if (isFiniteNumber(params.participation_rate)) {
    elements.participation.value = String(Number(params.participation_rate) * 100);
  }
  if (isFiniteNumber(params.industry_cap)) {
    elements.industryCap.value = String(Number(params.industry_cap) * 100);
  }
  const selectMappings = [
    [elements.horizon, params.label_horizon],
    [elements.model, params.model],
    [elements.rebalance, params.rebalance],
  ];
  selectMappings.forEach(([element, value]) => {
    if (value !== undefined && value !== null) element.value = String(value);
  });
  updateModeUI();
}

async function runResearch({ automatic = false } = {}) {
  let payload;
  try {
    payload = readParameters();
  } catch (error) {
    showNotice(error.message);
    setRunState("error", "参数需要修正");
    return;
  }

  if (state.controller) state.controller.abort();
  const controller = new AbortController();
  state.controller = controller;
  setLoading(true);
  clearNotice();
  updateModeUI();

  try {
    const response = await fetch("/api/lab", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `研究请求失败 (${response.status})`);
    if (state.controller !== controller) return;
    state.result = result;
    state.dataMode = result.meta?.data_mode || payload.data_mode;
    renderAll(result);
    saveExperiment(result, payload);
    const elapsed = ((performance.now() - state.runStartedAt) / 1000).toFixed(1);
    setRunState("ready", `完成 · ${elapsed} 秒`);
    elements.runHint.textContent = `${result.meta?.source || "行情"} · ${result.meta?.generated_at || "刚刚完成"}`;
    updateModeUI();
    if (!automatic) showToast(`实验 ${result.meta?.experiment_id || ""} 已完成`);
  } catch (error) {
    if (state.controller !== controller) return;
    if (error.name === "AbortError") {
      setRunState("error", "运行已取消");
      showNotice("本次研究请求已取消，页面上一次结果仍被保留。");
    } else {
      setRunState("error", "研究运行失败");
      showNotice(`研究未完成：${error.message}。请检查网络或稍后重试，页面会保留上一份真实结果。`);
      if (payload.data_mode === "live") {
        elements.sourceDot.classList.add("is-error");
      }
    }
  } finally {
    if (state.controller === controller) {
      state.controller = null;
      setLoading(false);
    }
  }
}

function renderAll(result) {
  renderHeader(result);
  renderOverview(result);
  renderAudit(result);
  renderFeatures(result);
  renderSamples(result);
  renderTraining(result);
  renderBacktest(result);
  renderPortfolio(result);
  renderArchive(result);
  renderHistory();
}

function renderHeader(result) {
  const meta = result.meta || {};
  const summary = result.summary || {};
  const source = meta.source || "行情来源未知";
  elements.experimentBadge.textContent = meta.experiment_id || "实验编号缺失";
  elements.headerAsOf.textContent = meta.market_data_as_of || "--";
  elements.researchSourceBadge.textContent =
    `实时行情 · ${meta.universe_size_loaded || "--"} 只 · ${meta.history_days || "--"} 日`;
  elements.portfolioCapitalBadge.textContent = `研究资金 ${formatMoney(meta.capital)}`;
  elements.sourceLabel.textContent = "实时行情已载入";
  elements.sidebarSourceDetail.textContent = `${meta.source || "公开行情"} · 前复权`;
  elements.sourceDot.classList.remove("is-error");
  elements.sourceDot.classList.add("is-live");
  elements.researchQuestion.textContent =
    result.report_context?.research_question || elements.researchQuestion.textContent;
  elements.footerSource.textContent =
    `${source} · ${meta.market_data_as_of || "行情时间未知"} · 历史结果不代表未来收益。`;
  elements.researchSourceBadge.classList.remove("is-warning");
  setBadge(elements.autoSelectionBadge, meta.model === "auto" ? "自动选择" : "指定模型", "is-positive");
  elements.evidenceNote.textContent = "Rank IC 与净超额均需在留出期和滚动训练中复核；单次回测不能证明策略长期有效。";
  if (!summary) return;
}

function computeVerdict(result) {
  const summary = result.summary || {};
  const isDemo = result.meta?.data_mode === "demo";
  const holdoutIc = Number(summary.holdout_ic);
  const holdoutExcess = Number(summary.holdout_net_excess);
  const validationIc = Number(summary.validation_ic);
  const hasIc = Number.isFinite(holdoutIc);
  const hasExcess = Number.isFinite(holdoutExcess);

  if (!hasIc || !hasExcess) {
    return {
      tone: "warning",
      title: "样本外指标不完整",
      text: "先检查样本量和模型状态，不根据不完整的绩效下结论。",
      tag: "证据不足",
    };
  }
  if (holdoutIc > 0 && holdoutExcess > 0) {
    return {
      tone: "positive",
      title: "留出期方向与净超额同时为正",
      text: `验证期与留出期 Rank IC 分别为 ${formatDecimal(validationIc, 3)} 与 ${formatDecimal(holdoutIc, 3)}，净超额 ${formatPct(holdoutExcess, 1, true)}。仍需滚动复核和交易可行性检查。${isDemo ? " 演示数据只用于验证流程。" : ""}`,
      tag: "初步通过",
    };
  }
  if (holdoutIc <= 0) {
    return {
      tone: "negative",
      title: "留出期排序能力没有复现",
      text: `验证期 Rank IC ${formatDecimal(validationIc, 3)}，留出期 ${formatDecimal(holdoutIc, 3)}。当前样本没有证明该机器学习模型具备稳定预测能力，不能把回测净值当作策略有效。`,
      tag: "不支持升级",
    };
  }
  return {
    tone: "warning",
    title: "留出期有排序信号，但成本后未转正",
    text: `留出期 Rank IC ${formatDecimal(holdoutIc, 3)}，净超额 ${formatPct(holdoutExcess, 1, true)}。需要继续核查成本、换手和执行容量。`,
    tag: "仅保留观察",
  };
}

function renderOverview(result) {
  const summary = result.summary || {};
  const training = result.training || {};
  const performance = result.performance || {};
  const quality = result.data_quality || {};
  const capacity = result.capacity || {};
  const verdict = computeVerdict(result);

  elements.researchVerdict.dataset.tone = verdict.tone;
  elements.verdictTitle.textContent = verdict.title;
  elements.verdictText.textContent = verdict.text;
  elements.verdictTag.textContent = verdict.tag;

  setMetric(elements.metricHoldoutIc, formatDecimal(summary.holdout_ic, 3), metricTone(summary.holdout_ic));
  setMetric(
    elements.metricHoldoutExcess,
    formatPct(summary.holdout_net_excess, 1, true),
    metricTone(summary.holdout_net_excess),
  );
  setMetric(elements.metricCostDrag, formatPct(summary.annual_cost_drag, 2), "is-warning");
  setMetric(elements.metricDrawdown, formatPct(summary.max_drawdown, 1), "is-warning");
  elements.metricRows.textContent = formatInteger(quality.labeled_rows);
  setMetric(
    elements.metricCapacity,
    formatPct(summary.capacity_utilization, 1),
    Number(summary.capacity_utilization) > 0.8 ? "is-warning" : metricTone(1),
  );

  elements.selectedModelLabel.textContent =
    training.selected_label || MODEL_LABELS[training.selected_model] || "--";
  elements.selectionReason.textContent = training.selection_reason || training.selection_rule || "--";
  elements.developmentWindow.textContent =
    training.development_start && training.development_end
      ? `${training.development_start} → ${training.development_end}`
      : "--";
  elements.holdoutWindow.textContent =
    training.holdout_start && training.holdout_end
      ? `${training.holdout_start} → ${training.holdout_end}`
      : "--";
  elements.rollingProtocol.textContent =
    training.selected_model === "factor"
      ? "固定打分；不拟合监督标签"
      : `每 ${training.retrain_step || 20} 个交易日扩展重训`;
  elements.predictionDate.textContent = quality.latest_prediction_date || "--";

  const performanceRows = [
    ["全样本", performance.full],
    ["开发期", performance.development],
    ["留出期", performance.holdout],
  ];
  elements.performanceBody.innerHTML = performanceRows
    .map(([label, row]) => {
      const data = row || {};
      return `
        <tr class="${label === "留出期" ? "is-selected" : ""}">
          <td class="text-cell">${escapeHtml(label)}</td>
          <td>${formatPct(data.gross_annual_return, 1, true)}</td>
          <td>${formatPct(data.net_annual_return, 1, true)}</td>
          <td>${formatPct(data.benchmark_annual_return, 1, true)}</td>
          <td class="${metricTone(data.net_excess_annual_return)}">${formatPct(data.net_excess_annual_return, 1, true)}</td>
          <td class="is-warning-text">${formatPct(data.max_drawdown, 1)}</td>
        </tr>
      `;
    })
    .join("");
  setBadge(
    elements.performanceBadge,
    Number(summary.holdout_net_excess) >= 0 ? "净超额为正" : "净超额为负",
    Number(summary.holdout_net_excess) >= 0 ? "is-positive" : "is-negative",
  );

  elements.validationIc.textContent = formatDecimal(summary.validation_ic, 3);
  elements.validationIc.className = metricTone(summary.validation_ic);
  elements.holdoutIc.textContent = formatDecimal(summary.holdout_ic, 3);
  elements.holdoutIc.className = metricTone(summary.holdout_ic);
  elements.topCapture.textContent = formatPct(summary.top_bucket_capture, 1);
  elements.holdoutR2.textContent = formatDecimal(summary.holdout_r2, 3);
  elements.holdoutR2.className = metricTone(summary.holdout_r2);

  const actions = result.risk?.actions || [];
  elements.overviewActions.innerHTML = actions.length
    ? actions
        .slice(0, 3)
        .map(
          (action, index) => `
            <li>
              <span class="action-index">${String(index + 1).padStart(2, "0")}</span>
              <p><strong>${escapeHtml(action.title)}</strong><br>${escapeHtml(action.action)}</p>
            </li>
          `,
        )
        .join("")
    : `<li><span class="action-index">01</span><p>当前未产生额外风险行动，仍需复核成交与样本外稳定性。</p></li>`;
}

function renderBarList(container, rows, options = {}) {
  if (!container) return;
  if (!Array.isArray(rows) || !rows.length) {
    container.innerHTML = `<p class="empty-copy">${escapeHtml(options.empty || "暂无数据")}</p>`;
    return;
  }
  const maximum = Math.max(...rows.map((row) => Math.abs(Number(row.value) || 0)), options.minimum || 1);
  container.innerHTML = rows
    .map((row) => {
      const numeric = Number(row.value) || 0;
      const width = Math.min(100, (Math.abs(numeric) / maximum) * 100);
      const tone = row.tone ? ` ${escapeHtml(row.tone)}` : options.tone ? ` ${escapeHtml(options.tone)}` : "";
      return `
        <div class="bar-row" title="${escapeHtml(row.title || row.label)}">
          <span class="bar-label">${escapeHtml(row.label)}</span>
          <span class="bar-track"><span class="bar-fill${tone}" style="width:${width.toFixed(2)}%"></span></span>
          <span class="bar-value">${escapeHtml(row.display ?? formatDecimal(numeric, 2))}</span>
        </div>
      `;
    })
    .join("");
}

function renderAudit(result) {
  const quality = result.data_quality || {};
  const meta = result.meta || {};
  const exploration = result.exploration || {};
  elements.qualityLoaded.textContent = formatInteger(quality.loaded_symbols);
  elements.qualityRequested.textContent = `请求 ${formatInteger(quality.requested_symbols)} 只`;
  elements.qualityDates.textContent = formatInteger(quality.trading_days);
  elements.qualityDateRange.textContent =
    quality.date_start && quality.date_end ? `${quality.date_start} → ${quality.date_end}` : "--";
  elements.qualityDuplicate.textContent = formatInteger(quality.duplicate_records);
  elements.qualityInvalid.textContent = formatInteger(quality.invalid_price_records);
  elements.qualityFilled.textContent = formatInteger(quality.forward_filled_cells);
  elements.qualityMissing.textContent = formatPct(quality.missing_close_pct, 2);
  setBadge(
    elements.qualityModeBadge,
    meta.data_mode === "demo" ? "确定性演示数据" : "实时公开行情",
    "is-positive",
  );
  elements.dataSourceDetail.textContent = meta.source || "--";
  const sourceText = String(meta.source || "");
  elements.dataAdjustment.textContent = sourceText.includes("腾讯")
    ? "腾讯前复权日行情；成交额按成交量 × 收盘价估算，行业暂按 A 股归类"
    : sourceText.includes("新浪财经日行情")
      ? "新浪财经日行情；成交额按成交量 × 收盘价估算，行业暂按 A 股归类"
      : "公开日行情；成交额口径以数据源说明为准，行业分类取当前可得快照";
  elements.dataMarketDate.textContent = meta.market_data_as_of || "--";
  elements.dataSnapshotVersion.textContent = `${meta.source || "公开行情"} · ${meta.generated_at || meta.market_data_as_of || "本次运行"}`;
  const excluded = quality.excluded_short_history || [];
  elements.excludedShortHistory.textContent =
    quality.excluded_short_history_count || 0
      ? `${formatInteger(quality.excluded_short_history_count)} 只 · ${excluded.slice(0, 5).join("、")}`
      : "0 只";
  const failed = quality.failed_symbols || [];
  elements.failedSymbols.textContent = failed.length
    ? failed.map((row) => `${row.symbol}: ${row.error}`).join("；")
    : "无";

  elements.cleaningRules.innerHTML = (quality.cleaning_rules || [])
    .map((rule) => `<li>${escapeHtml(rule)}</li>`)
    .join("");

  const industryRows = Object.entries(quality.industry_counts || {})
    .map(([industry, count]) => ({
      label: industry,
      value: Number(count) || 0,
      display: `${formatInteger(count)} 只`,
    }))
    .sort((left, right) => right.value - left.value);
  renderBarList(elements.industryAuditBars, industryRows, { empty: "暂无行业分布" });

  const missingRows = [
    ["收盘价", quality.missing_close_pct, "缺口最多前向填充 5 日"],
    ["成交额", quality.missing_amount_pct, "缺失不伪造，入模时训练折中位数填充"],
    ["成交量", quality.missing_volume_pct, "缺失不伪造，用于成交活性特征"],
    ...(exploration.feature_missing_top || []).slice(0, 8).map((row) => [
      row.label || row.feature,
      row.missing_pct,
      "训练折中位数填充",
    ]),
  ];
  elements.qualityTableBody.innerHTML = missingRows
    .map(([field, missing, policy]) => {
      const missingValue = Number(missing) || 0;
      const status = missingValue > 0.35 ? "关注" : missingValue > 0.1 ? "抽样检查" : "通过";
      return `
        <tr>
          <td class="text-cell">${escapeHtml(field)}</td>
          <td>${formatPct(missingValue, 2)}</td>
          <td class="text-cell">${escapeHtml(policy)}</td>
          <td class="${missingValue > 0.35 ? "is-warning-text" : "is-positive-text"}">${status}</td>
        </tr>
      `;
    })
    .join("");
  resetDatasetBrowser(result);
}

function setDatasetStatus(message, tone = "") {
  elements.datasetStatus.textContent = message;
  elements.datasetStatus.classList.remove("is-loading", "is-error");
  if (tone) elements.datasetStatus.classList.add(`is-${tone}`);
}

function updateDatasetViewButtons() {
  $$("[data-dataset-view]").forEach((button) => {
    const active = button.dataset.datasetView === state.datasetView;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function clearDatasetTable(message) {
  const columnCount = elements.datasetHead.querySelectorAll("th").length || 1;
  elements.datasetBody.innerHTML = `<tr><td colspan="${columnCount}" class="table-empty">${escapeHtml(message)}</td></tr>`;
}

function formatDatasetCell(value, kind) {
  if (value === null || value === undefined || value === "") return "--";
  if (kind === "percent") return formatPct(value, 2, true);
  if (kind === "integer") return formatInteger(value);
  if (kind === "decimal") {
    const numeric = Number(value);
    return formatNumber(numeric, Math.abs(numeric) >= 1_000 ? 2 : 4);
  }
  return escapeHtml(value);
}

function renderDatasetTable(payload) {
  const columns = payload.columns || [];
  elements.datasetHead.innerHTML = `<tr>${columns
    .map((column) => `<th title="${escapeHtml(column.key)}">${escapeHtml(column.label)}</th>`)
    .join("")}</tr>`;
  const table = elements.datasetHead.closest("table");
  if (table) table.style.minWidth = `${Math.max(680, columns.length * 116)}px`;

  const rows = payload.rows || [];
  if (!rows.length) {
    clearDatasetTable("当前筛选没有匹配记录。");
    return;
  }
  elements.datasetBody.innerHTML = rows
    .map((row) => {
      const cells = columns.map((column) => {
        const value = row[column.key];
        const classes = [];
        if (["text", "date"].includes(column.kind)) classes.push("text-cell");
        if (column.kind === "percent") {
          const tone = metricTone(value);
          if (tone) classes.push(`${tone}-text`);
        }
        const className = classes.length ? ` class="${classes.join(" ")}"` : "";
        return `<td${className}>${formatDatasetCell(value, column.kind)}</td>`;
      });
      return `<tr>${cells.join("")}</tr>`;
    })
    .join("");
}

function renderDatasetPagination(payload) {
  const total = Number(payload?.total) || 0;
  const sourceTotal = Number(payload?.source_total ?? total) || 0;
  const limit = Number(payload?.limit) || state.datasetLimit;
  const offset = Number(payload?.offset) || 0;
  const rowCount = payload?.rows?.length || 0;
  const pageCount = total ? Math.ceil(total / limit) : 0;
  const page = total ? Math.floor(offset / limit) + 1 : 0;
  elements.datasetPageInfo.textContent = `第 ${page} / ${pageCount} 页 · 每页 ${limit} 行 · 共 ${formatInteger(total)} 行`;
  elements.datasetPreviousPage.disabled = !payload || state.datasetController !== null || offset <= 0;
  elements.datasetNextPage.disabled = !payload || state.datasetController !== null || offset + rowCount >= total;

  if (!payload) return;
  if (!rowCount) {
    setDatasetStatus(total === sourceTotal ? "数据底稿为空。" : `筛选后 0 行，完整底稿共 ${formatInteger(sourceTotal)} 行。`);
    return;
  }
  const start = offset + 1;
  const end = offset + rowCount;
  const filteredNote = total !== sourceTotal ? `；筛选后 ${formatInteger(total)} 行` : "";
  setDatasetStatus(`显示第 ${formatInteger(start)}-${formatInteger(end)} 行${filteredNote}。数据底稿共 ${formatInteger(sourceTotal)} 行。`);
}

function setDatasetLoading(loading) {
  elements.datasetApplyFilter.disabled = loading;
  if (loading) {
    setDatasetStatus("正在读取数据页...", "loading");
    elements.datasetPreviousPage.disabled = true;
    elements.datasetNextPage.disabled = true;
  }
}

async function loadDatasetPage({ resetOffset = false } = {}) {
  const datasetId = state.result?.meta?.dataset_id;
  if (!datasetId) {
    clearDatasetTable("运行研究后可按页查看完整数据。");
    setDatasetStatus("当前结果没有可用的数据底稿。");
    return;
  }
  if (resetOffset) state.datasetOffset = 0;
  if (state.datasetController) state.datasetController.abort();
  const controller = new AbortController();
  state.datasetController = controller;
  const parameters = new URLSearchParams({
    view: state.datasetView,
    offset: String(state.datasetOffset),
    limit: String(state.datasetLimit),
  });
  const query = elements.datasetQuery.value.trim();
  if (query) parameters.set("q", query);
  if (elements.datasetDateFrom.value) parameters.set("date_from", elements.datasetDateFrom.value);
  if (elements.datasetDateTo.value) parameters.set("date_to", elements.datasetDateTo.value);
  setDatasetLoading(true);
  try {
    const response = await fetch(`/api/dataset/${encodeURIComponent(datasetId)}?${parameters.toString()}`, {
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `数据页读取失败 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    if (state.datasetController !== controller) return;
    state.datasetPage = payload;
    state.datasetOffset = Number(payload.offset) || 0;
    state.datasetView = payload.view || state.datasetView;
    updateDatasetViewButtons();
    renderDatasetTable(payload);
    renderDatasetPagination(payload);
  } catch (error) {
    if (error.name === "AbortError") return;
    state.datasetPage = null;
    clearDatasetTable(error.status === 404 ? "数据快照已失效，请重新运行研究。" : "数据页暂时无法读取。");
    elements.datasetPageInfo.textContent = "第 0 / 0 页 · 每页 50 行";
    elements.datasetPreviousPage.disabled = true;
    elements.datasetNextPage.disabled = true;
    setDatasetStatus(error.message, "error");
  } finally {
    if (state.datasetController === controller) {
      state.datasetController = null;
      elements.datasetApplyFilter.disabled = false;
      renderDatasetPagination(state.datasetPage);
    }
  }
}

function resetDatasetBrowser(result) {
  if (state.datasetController) state.datasetController.abort();
  state.datasetPage = null;
  state.datasetView = "raw";
  state.datasetOffset = 0;
  elements.datasetQuery.value = "";
  elements.datasetDateFrom.value = "";
  elements.datasetDateTo.value = "";
  updateDatasetViewButtons();

  const meta = result.meta || {};
  const quality = result.data_quality || {};
  const rows = meta.dataset_rows || {};
  const datasetId = meta.dataset_id || null;
  elements.datasetWorkbenchId.textContent = datasetId || "--";
  elements.datasetExperimentId.textContent = meta.experiment_id || "--";
  elements.datasetRawRows.textContent = isFiniteNumber(rows.raw_rows) ? `${formatInteger(rows.raw_rows)} 行` : "--";
  elements.datasetModelRows.textContent = isFiniteNumber(rows.model_rows) ? `${formatInteger(rows.model_rows)} 行` : "--";
  elements.datasetDateSpan.textContent = quality.date_start && quality.date_end ? `${quality.date_start} → ${quality.date_end}` : "--";
  elements.datasetSymbolCount.textContent = isFiniteNumber(meta.universe_size_loaded) ? `${formatInteger(meta.universe_size_loaded)} 只` : "--";
  elements.datasetRawDownload.disabled = !datasetId;
  elements.datasetModelDownload.disabled = !datasetId;
  if (!datasetId) {
    clearDatasetTable("运行研究后可按页查看完整数据。");
    setDatasetStatus("当前结果没有可用的数据底稿。", "error");
    return;
  }
  clearDatasetTable("正在载入第一页数据...");
  loadDatasetPage();
}

async function downloadDatasetCsv(view) {
  const datasetId = state.result?.meta?.dataset_id;
  if (!datasetId) return;
  const button = view === "raw" ? elements.datasetRawDownload : elements.datasetModelDownload;
  const label = button.querySelector("span");
  const originalLabel = label.textContent;
  button.disabled = true;
  label.textContent = "正在准备";
  try {
    const response = await fetch(`/api/dataset/${encodeURIComponent(datasetId)}/csv?view=${encodeURIComponent(view)}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `CSV 下载失败 (${response.status})`);
    }
    const blob = await response.blob();
    const experimentId = state.result.meta?.experiment_id || "quantguard";
    downloadBlob(blob, `${experimentId}_${view}.csv`, "text/csv;charset=utf-8");
    showToast(view === "raw" ? "完整原始行情 CSV 已导出" : "完整建模样本 CSV 已导出");
  } catch (error) {
    setDatasetStatus(`CSV 导出失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    label.textContent = originalLabel;
  }
}

function featureDisplayName(rawName) {
  return String(rawName || "")
    .replace(/^numeric__/, "")
    .replace(/^industry__/, "行业:")
    .replaceAll("_", " ");
}

function renderFeatures(result) {
  const catalog = result.feature_catalog || [];
  const missingRows = result.exploration?.feature_missing_top || [];
  const missingMap = new Map(missingRows.map((row) => [row.feature, row.missing_pct]));
  elements.featureCatalogBody.innerHTML = catalog.length
    ? catalog
        .map(
          (feature) => `
            <tr>
              <td><strong>${escapeHtml(feature.label)}</strong><br><small class="is-warning-text">${escapeHtml(feature.name)}</small></td>
              <td class="text-cell">${escapeHtml(feature.category)}</td>
              <td><code>${escapeHtml(feature.formula)}</code><br><small>${escapeHtml(feature.timing || "只使用信号日及之前")}</small></td>
              <td>${formatPct(missingMap.get(feature.name), 2)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="4" class="table-empty">等待实验</td></tr>`;

  elements.featureMissingBars.innerHTML = "";
  renderBarList(
    elements.featureMissingBars,
    missingRows.map((row) => ({
      label: row.label || row.feature,
      value: row.missing_pct,
      display: formatPct(row.missing_pct, 2),
      tone: Number(row.missing_pct) > 0.3 ? "is-warning" : "",
    })),
    { empty: "当前没有有效特征缺失数据" },
  );

  const correlationRows = result.exploration?.feature_correlations || [];
  elements.correlationBody.innerHTML = correlationRows.length
    ? correlationRows
        .slice(0, 10)
        .map(
          (row) => `
            <tr>
              <td class="text-cell">${escapeHtml(row.left)} ↔ ${escapeHtml(row.right)}</td>
              <td>${formatDecimal(row.correlation, 3)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="2" class="table-empty">暂无有效相关矩阵</td></tr>`;
  elements.correlationDate.textContent = result.exploration?.correlation_date || "--";
}

function renderSamples(result) {
  const training = result.training || {};
  const exploration = result.exploration || {};
  const context = result.report_context || {};
  const horizon = training.horizon_days || result.meta?.label_horizon || 20;
  elements.labelHorizonBadge.textContent = `${horizon} 日`;
  elements.labelFormula.textContent =
    context.label_definition || `future_excess = close[t+${horizon}] / close[t] - 1 - 当日股票池等权未来收益`;

  elements.splitDevelopmentText.textContent =
    training.development_start && training.development_end
      ? `${training.development_start} → ${training.development_end}`
      : "--";
  elements.splitHoldoutText.textContent =
    training.holdout_start && training.holdout_end
      ? `${training.holdout_start} → ${training.holdout_end}`
      : "--";
  elements.sampleRowsDevelopment.textContent = formatInteger(training.rows_development);
  elements.sampleRowsHoldout.textContent = formatInteger(training.rows_holdout);
  elements.foldCount.textContent = `${training.folds?.length || 0} 折`;
  elements.retrainProtocol.textContent =
    training.selected_model === "factor"
      ? "固定基线，不重训"
      : `每 ${training.retrain_step || 20} 个交易日`;

  const label = exploration.label_distribution || {};
  elements.labelDistribution.innerHTML = [
    ["样本数", formatInteger(label.samples)],
    ["均值", formatPct(label.mean, 3, true)],
    ["标准差", formatPct(label.std, 3)],
    ["5% 分位", formatPct(label.p05, 3, true)],
    ["中位数", formatPct(label.median, 3, true)],
    ["95% 分位", formatPct(label.p95, 3, true)],
    ["正超额占比", formatPct(label.positive_share, 1)],
    ["极端标签占比", formatPct(label.extreme_share, 2)],
  ]
    .map(([term, value]) => `<div><dt>${term}</dt><dd>${value}</dd></div>`)
    .join("");

  const groups = exploration.market_regime_groups || [];
  elements.marketGroupBody.innerHTML = groups.length
    ? groups
        .map(
          (group) => `
            <tr>
              <td class="text-cell">${escapeHtml(group.label)}</td>
              <td>${formatInteger(group.samples)}</td>
              <td class="${metricTone(group.mean_future_excess)}">${formatPct(group.mean_future_excess, 3, true)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="3" class="table-empty">暂无市场状态分组</td></tr>`;

  const folds = training.folds || [];
  elements.foldBody.innerHTML = folds.length
    ? folds
        .map(
          (fold, index) => `
            <tr>
              <td>${index + 1}</td>
              <td>${escapeHtml(fold.train_end)}</td>
              <td>${escapeHtml(fold.validation_start)} → ${escapeHtml(fold.validation_end)}</td>
              <td>${formatInteger(fold.train_rows)}</td>
              <td>${formatInteger(fold.validation_rows)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="5" class="table-empty">开发期不足以形成有效验证折</td></tr>`;

  const checks = training.leakage_checks || context.leakage_controls || [];
  elements.leakageList.innerHTML = checks.length
    ? checks
        .map(
          (check) => `
            <li><strong>${escapeHtml(check.name)}</strong> · ${escapeHtml(check.detail)}</li>
          `,
        )
        .join("")
    : "<li>等待训练完成后列出防泄漏检查。</li>";
}

function readableHyperparameters(parameters) {
  if (!parameters || typeof parameters !== "object") return [];
  return Object.entries(parameters).map(([key, value]) => [
    key
      .replaceAll("_", " ")
      .replace("momentum 20", "20 日动量")
      .replace("momentum 60", "60 日动量")
      .replace("low vol", "低波动")
      .replace("liquidity", "流动性"),
    typeof value === "number" ? formatDecimal(value, 4) : String(value),
  ]);
}

function renderTraining(result) {
  const training = result.training || {};
  const rows = result.model_comparison || training.model_comparison || [];
  elements.modelSelectedBadge.textContent = training.selected_label || "--";
  elements.modelSelectedBadge.className = "tiny-badge is-positive";
  elements.modelComparisonBody.innerHTML = rows.length
    ? rows
        .map((row) => {
          const validation = row.validation || {};
          const holdout = row.holdout || {};
          const status = row.status === "ok" ? "完成" : "失败";
          const conclusion = row.selected ? "入选" : row.status === "ok" ? "对照" : "未生成";
          return `
            <tr class="${row.selected ? "is-selected" : ""}">
              <td class="text-cell"><strong>${escapeHtml(row.label || MODEL_LABELS[row.model] || row.model)}</strong></td>
              <td class="${row.status === "ok" ? "is-positive-text" : "is-negative-text"}">${status}</td>
              <td>${formatDecimal(row.validation_ic ?? validation.rank_ic_mean, 3)}</td>
              <td>${formatDecimal(row.holdout_ic ?? holdout.rank_ic_mean, 3)}</td>
              <td>${formatDecimal(row.holdout_r2 ?? holdout.r2_oos, 3)}</td>
              <td>${formatPct(row.top_bucket_capture ?? holdout.top_bucket_capture, 1)}</td>
              <td class="text-cell ${row.selected ? "is-positive-text" : ""}">${conclusion}</td>
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="7" class="table-empty">暂无模型对照结果</td></tr>`;

  const selectedRow = rows.find((row) => row.selected);
  const parameters = training.selected_hyperparameters || selectedRow?.hyperparameters || {};
  const parameterRows = readableHyperparameters(parameters);
  elements.hyperparameterList.innerHTML = [
    ["选定模型", training.selected_label || "--"],
    ["请求模型", training.requested_model === "auto" ? "自动选择" : MODEL_LABELS[training.requested_model] || training.requested_model || "--"],
    ["选择规则", training.selection_rule || "--"],
    ["评估频率", training.evaluation_frequency || "--"],
    ["实际重训", `${formatInteger(training.fit_count)} 次`],
    ...parameterRows,
  ]
    .map(([term, value]) => `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");

  const importance = training.feature_importance || [];
  elements.importanceModel.textContent = training.selected_label || "--";
  renderBarList(
    elements.importanceBars,
    importance.map((row) => ({
      label: featureDisplayName(row.name),
      value: row.importance,
      display: formatDecimal(row.importance, 4),
      tone: "is-accent",
    })),
    { empty: "该模型为固定因子基线，不产生监督特征重要性。" },
  );

  const fits = training.rolling_fits || [];
  elements.fitCountBadge.textContent = `${formatInteger(training.fit_count)} 次`;
  elements.rollingFitsBody.innerHTML = fits.length
    ? fits
        .map(
          (fit) => `
            <tr>
              <td>${escapeHtml(fit.signal_date)}</td>
              <td>${escapeHtml(fit.train_start)}</td>
              <td>${escapeHtml(fit.train_end)}</td>
              <td>${escapeHtml(fit.label_cutoff)}</td>
              <td>${formatInteger(fit.train_rows)}</td>
              <td>${formatInteger(fit.prediction_rows)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="6" class="table-empty">因子基线使用固定打分，无监督重训记录。</td></tr>`;
}

function renderPerformanceTable(body, performance) {
  if (!body) return;
  const stages = [
    ["全样本", performance.full],
    ["开发期", performance.development],
    ["留出期", performance.holdout],
  ];
  body.innerHTML = stages
    .map(([label, row]) => {
      const data = row || {};
      return `
        <tr class="${label === "留出期" ? "is-selected" : ""}">
          <td class="text-cell">${label}</td>
          <td>${formatPct(data.gross_annual_return, 1, true)}</td>
          <td>${formatPct(data.annual_cost_drag, 2)}</td>
          <td>${formatPct(data.net_annual_return, 1, true)}</td>
          <td>${formatPct(data.benchmark_annual_return, 1, true)}</td>
          <td class="${metricTone(data.net_excess_annual_return)}">${formatPct(data.net_excess_annual_return, 1, true)}</td>
          <td>${formatDecimal(data.sharpe_ratio, 2)}</td>
          <td>${formatDecimal(data.annual_turnover, 2)}x</td>
        </tr>
      `;
    })
    .join("");
}

function renderBacktest(result) {
  renderPerformanceTable(elements.backtestPeriodBody, result.performance || {});
  renderNavChart(result);
  const costs = result.cost_breakdown || {};
  elements.costBody.innerHTML = Object.entries(COST_LABELS)
    .map(([key, label]) => {
      const row = costs[key] || {};
      return `
        <tr class="${key === "total" ? "is-selected" : ""}">
          <td class="text-cell">${escapeHtml(label)}</td>
          <td>${formatMoney(row.currency)}</td>
          <td>${formatPct(row.pct_of_capital, 4)}</td>
        </tr>
      `;
    })
    .join("");
  const totalCost = costs.total?.currency;
  setBadge(elements.costTotalBadge, `${formatMoney(totalCost)} · ${formatPct(costs.total?.pct_of_capital, 3)}`, "is-warning");

  const stress = result.capacity?.stress || result.risk?.stress || [];
  elements.stressBody.innerHTML = stress.length
    ? stress
        .map(
          (row) => `
            <tr class="${Number(row.utilization) > 0.9 ? "is-selected" : ""}">
              <td>${formatDecimal(row.multiple, 0)}x</td>
              <td>${formatMoney(row.capital)}</td>
              <td>${formatPct(row.invested_weight, 1)}</td>
              <td class="${Number(row.utilization) > 0.9 ? "is-negative-text" : "is-positive-text"}">${formatPct(row.utilization, 1)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="4" class="table-empty">暂无资金压力结果</td></tr>`;
  setBadge(
    elements.capacityBadge,
    `1x 占用 ${formatPct(result.capacity?.utilization, 1)}`,
    Number(result.capacity?.utilization) > 0.8 ? "is-warning" : "is-positive",
  );
}

function renderNavChart(result) {
  const rows = (result.nav || []).filter(
    (row) => isFiniteNumber(row.value) && isFiniteNumber(row.benchmark),
  );
  state.chartRows = rows;
  state.chartGeometry = null;
  if (rows.length < 2) {
    elements.navChart.innerHTML = "";
    elements.chartEmpty.hidden = false;
    elements.navTooltip.hidden = true;
    elements.chartStart.textContent = "--";
    elements.chartEnd.textContent = "--";
    elements.chartAriaText.textContent = "--";
    return;
  }
  elements.chartEmpty.hidden = true;
  const width = 900;
  const height = 300;
  const pad = { left: 58, right: 18, top: 18, bottom: 36 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const values = rows.flatMap((row) => [Number(row.value), Number(row.benchmark)]);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = Math.max(rawMax - rawMin, 0.02);
  const minValue = rawMin - span * 0.12;
  const maxValue = rawMax + span * 0.12;
  const xAt = (index) => pad.left + (index / Math.max(rows.length - 1, 1)) * innerWidth;
  const yAt = (value) => pad.top + ((maxValue - value) / (maxValue - minValue)) * innerHeight;
  const pointPath = (key) =>
    rows
      .map((row, index) => `${index ? "L" : "M"}${xAt(index).toFixed(2)},${yAt(Number(row[key])).toFixed(2)}`)
      .join(" ");
  const strategyPath = pointPath("value");
  const benchmarkPath = pointPath("benchmark");
  const areaPath = `${strategyPath} L${xAt(rows.length - 1).toFixed(2)},${height - pad.bottom} L${xAt(0).toFixed(2)},${height - pad.bottom} Z`;
  const holdoutStart = result.training?.holdout_start;
  let holdoutIndex = rows.findIndex((row) => String(row.date) >= String(holdoutStart));
  if (holdoutIndex < 0) holdoutIndex = Math.floor(rows.length * 0.6);
  const holdoutX = xAt(holdoutIndex);
  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const y = pad.top + ratio * innerHeight;
    const value = maxValue - ratio * (maxValue - minValue);
    return `
      <line class="chart-grid-line" x1="${pad.left}" y1="${y.toFixed(2)}" x2="${width - pad.right}" y2="${y.toFixed(2)}"></line>
      <text class="chart-axis-label" x="${pad.left - 8}" y="${(y + 3).toFixed(2)}" text-anchor="end">${value.toFixed(2)}</text>
    `;
  }).join("");
  const xIndexes = [0, Math.floor((rows.length - 1) / 2), rows.length - 1];
  const xLabels = xIndexes
    .map((index, position) => {
      const anchor = position === 0 ? "start" : position === 2 ? "end" : "middle";
      return `<text class="chart-axis-label" x="${xAt(index).toFixed(2)}" y="${height - 10}" text-anchor="${anchor}">${escapeHtml(rows[index].date)}</text>`;
    })
    .join("");
  elements.navChart.innerHTML = `
    <rect class="holdout-band" x="${holdoutX.toFixed(2)}" y="${pad.top}" width="${(width - pad.right - holdoutX).toFixed(2)}" height="${innerHeight.toFixed(2)}"></rect>
    ${grid}
    <path class="chart-area" d="${areaPath}"></path>
    <path class="chart-benchmark" d="${benchmarkPath}"></path>
    <path class="chart-line" d="${strategyPath}"></path>
    <line class="holdout-line" x1="${holdoutX.toFixed(2)}" y1="${pad.top}" x2="${holdoutX.toFixed(2)}" y2="${(height - pad.bottom).toFixed(2)}"></line>
    ${xLabels}
  `;
  state.chartGeometry = { pad, innerWidth, innerHeight, xAt, width, height };
  elements.navChart.setAttribute(
    "aria-label",
    `策略净值曲线，${rows[0].date} 至 ${rows.at(-1).date}，期末净值 ${Number(rows.at(-1).value).toFixed(4)}，基准 ${Number(rows.at(-1).benchmark).toFixed(4)}`,
  );
  elements.chartStart.textContent = `起点 ${rows[0].date}`;
  elements.chartEnd.textContent = `终点 ${rows.at(-1).date}`;
  elements.chartAriaText.textContent = `留出期起点约 ${rows[holdoutIndex]?.date || "--"}`;
}

function handleChartPointer(event) {
  const rows = state.chartRows;
  const geometry = state.chartGeometry;
  if (!rows.length || !geometry) return;
  const rect = elements.navChart.getBoundingClientRect();
  if (!rect.width) return;
  const relativeX = Math.max(0, Math.min(event.clientX - rect.left, rect.width));
  const chartX = (relativeX / rect.width) * geometry.width;
  const maxIndex = rows.length - 1;
  const index = Math.max(
    0,
    Math.min(maxIndex, Math.round(((chartX - geometry.pad.left) / geometry.innerWidth) * maxIndex)),
  );
  const row = rows[index];
  const left = Math.max(8, Math.min(relativeX + 12, rect.width - 144));
  const top = Math.max(8, Math.min(event.clientY - elements.navChartShell.getBoundingClientRect().top - 48, rect.height - 62));
  elements.navTooltip.style.left = `${left}px`;
  elements.navTooltip.style.top = `${top}px`;
  elements.tooltipDate.textContent = row.date;
  elements.tooltipValue.textContent = `净 ${Number(row.value).toFixed(4)} · 基准 ${Number(row.benchmark).toFixed(4)}`;
  elements.navTooltip.hidden = false;
}

function renderPortfolio(result) {
  const holdings = result.holdings || [];
  const capacity = result.capacity || {};
  const execution = result.execution || {};
  const exposure = result.industry_exposure || result.risk?.industry_exposure || [];
  const actions = result.risk?.actions || [];
  const meta = result.meta || {};
  const invested = holdings.reduce((total, row) => total + (Number(row.weight) || 0), 0);

  setMetric(elements.portfolioInvested, formatPct(invested, 1));
  setMetric(elements.portfolioCash, formatPct(capacity.cash_weight, 1), "is-warning");
  setMetric(elements.portfolioBuildCost, formatMoney(execution.build_cost), "is-warning");
  elements.portfolioBuildCostBps.textContent = `${formatDecimal(execution.build_cost_bps, 1)} bp`;
  setMetric(elements.portfolioMinimumCommission, formatMoney(execution.minimum_commission_cost), "is-warning");
  elements.portfolioExitDays.textContent = `${formatDecimal(execution.exit_days ?? meta.exit_days, 1)} 日`;
  elements.portfolioIndustryCap.textContent = formatPct(meta.industry_cap, 0);
  elements.syncTargetButton.disabled = !holdings.length;

  elements.portfolioHoldingsBody.innerHTML = holdings.length
    ? holdings
        .map(
          (row) => `
            <tr>
              <td>${row.rank}</td>
              <td><span class="stock-cell"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.symbol)}</small></span></td>
              <td class="text-cell">${escapeHtml(row.industry)}</td>
              <td>${formatDecimal(row.score, 3)}</td>
              <td>${formatPct(row.weight, 1)}</td>
              <td>${formatNumber(row.price, 2)}</td>
              <td>${formatInteger(row.shares)}</td>
              <td>${formatMoney(row.position_value)}</td>
              <td>${formatDecimal(row.days_to_exit, 1)} 日</td>
              <td>${formatDecimal(row.impact_bps, 1)} bp</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="10" class="table-empty">当前资金无法形成可交易持仓</td></tr>`;

  elements.riskActionCount.textContent = `${actions.length} 项`;
  elements.portfolioRiskActions.innerHTML = actions.length
    ? actions
        .map(
          (action) => `
            <article class="risk-action" data-severity="${escapeHtml(action.severity)}">
              <span class="risk-severity">${escapeHtml(action.severity === "critical" ? "硬性约束" : action.severity === "warning" ? "需要关注" : "结构提示")}</span>
              <div><h4>${escapeHtml(action.title)}</h4><p>${escapeHtml(action.action)}</p></div>
            </article>
          `,
        )
        .join("")
    : '<p class="empty-copy">当前未产生额外约束行动。</p>';

  const industryCap = Number(meta.industry_cap) || 0.22;
  renderBarList(
    elements.industryExposureBars,
    exposure.map((row) => ({
      label: row.industry,
      value: row.weight,
      display: formatPct(row.weight, 1),
      tone: Number(row.weight) > industryCap ? "is-negative" : "is-accent",
      title: `${row.positions || 0} 只持仓，上限 ${formatPct(industryCap, 0)}`,
    })),
    { empty: "当前无行业暴露" },
  );
  elements.capacityLimit.textContent = formatMoney(capacity.portfolio_capacity);
  elements.capacityHeadroom.textContent = formatMoney(capacity.headroom);
  elements.executionDaily.textContent = formatMoney(execution.daily_notional);
  elements.executionLargest.textContent = formatMoney(execution.largest_daily_order);
}

function renderArchive(result) {
  const meta = result.meta || {};
  const summary = result.summary || {};
  const training = result.training || {};
  const quality = result.data_quality || {};
  const context = result.report_context || {};
  elements.archiveExperimentId.textContent = meta.experiment_id || "--";
  elements.archiveModel.textContent = training.selected_label || "--";
  elements.archiveHoldoutIc.textContent = formatDecimal(summary.holdout_ic, 3);
  elements.archiveHoldoutIc.className = metricTone(summary.holdout_ic);
  elements.archiveExcess.textContent = formatPct(summary.holdout_net_excess, 1, true);
  elements.archiveExcess.className = metricTone(summary.holdout_net_excess);
  elements.archiveMode.textContent = meta.data_mode === "demo" ? "演示数据" : "实时行情";
  elements.archiveGeneratedAt.textContent = meta.generated_at || "--";
  elements.archiveQuestion.textContent =
    context.research_question || "检验机器学习模型能否预测未来横截面相对收益。";
  elements.archiveData.textContent =
    `${meta.source || "行情来源未知"}；${formatInteger(quality.loaded_symbols)} 只股票、${formatInteger(quality.trading_days)} 个交易日；有效标签样本 ${formatInteger(quality.labeled_rows)}。`;
  elements.archiveFeatures.textContent =
    `${(result.feature_catalog || []).length} 个特征；${context.label_definition || "未来横截面超额收益"}。`;
  elements.archiveSplit.textContent =
    `开发期 ${training.development_start || "--"} 至 ${training.development_end || "--"}；留出期 ${training.holdout_start || "--"} 至 ${training.holdout_end || "--"}；每 ${training.retrain_step || 20} 个交易日滚动重训。`;
  elements.archiveModels.textContent =
    `${training.selected_label || "--"} 入选；验证 IC ${formatDecimal(summary.validation_ic, 3)}，留出 IC ${formatDecimal(summary.holdout_ic, 3)}，留出净超额 ${formatPct(summary.holdout_net_excess, 1, true)}。`;
  elements.archiveConclusion.textContent =
    `全样本净年化 ${formatPct(summary.net_annual_return, 1, true)}，成本拖累 ${formatPct(summary.annual_cost_drag, 2)}，最大回撤 ${formatPct(summary.max_drawdown, 1)}；首要行动：${summary.primary_risk_action || "复核执行约束"}。`;

  const limitations = context.limitations || [
    "当前股票池按当前流动性筛选，存在幸存者偏差。",
    "标签窗口重叠会降低样本独立性。",
  ];
  elements.limitationsList.innerHTML = limitations
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  elements.disclaimerText.textContent = context.disclaimer || "仅用于课程研究和个人研究，不构成投资建议或收益承诺。";
  elements.exportMarkdownButton.disabled = false;
  elements.exportJsonButton.disabled = false;
}

function loadHistory() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_HISTORY) || "[]");
    state.history = Array.isArray(parsed)
      ? parsed
          .filter(
            (item) =>
              item &&
              typeof item.id === "string" &&
              item.params &&
              typeof item.params === "object" &&
              item.summary &&
              typeof item.summary === "object",
              item.dataMode !== "demo",
          )
          .slice(0, 6)
      : [];
  } catch (_error) {
    state.history = [];
    window.localStorage.removeItem(STORAGE_HISTORY);
  }
}

function saveExperiment(result, params) {
  const meta = result.meta || {};
  const summary = result.summary || {};
  const record = {
    id: meta.experiment_id || `QG-${Date.now()}`,
    createdAt: meta.generated_at || new Date().toISOString(),
    params: { ...params },
    summary: {
      model_label: summary.model_label || result.training?.selected_label || "--",
      holdout_ic: summary.holdout_ic,
      holdout_net_excess: summary.holdout_net_excess,
      net_annual_return: summary.net_annual_return,
      max_drawdown: summary.max_drawdown,
      labeled_rows: result.data_quality?.labeled_rows,
    },
    dataMode: meta.data_mode,
  };
  state.history = [record, ...state.history.filter((item) => item.id !== record.id)].slice(0, 6);
  try {
    window.localStorage.setItem(STORAGE_HISTORY, JSON.stringify(state.history));
  } catch (_error) {
    // Local storage can be unavailable; the current result remains usable.
  }
  renderHistory();
}

function renderHistory() {
  elements.historyCount.textContent = `${state.history.length} / 6`;
  elements.experimentHistory.innerHTML = state.history.length
    ? state.history
        .map(
          (item, index) => `
            <div class="history-item">
              <div>
                <strong>${escapeHtml(item.id)}</strong>
                <p>${escapeHtml(item.summary.model_label || "--")} · 留出 IC ${formatDecimal(item.summary.holdout_ic, 3)} · 净超额 ${formatPct(item.summary.holdout_net_excess, 1, true)} · ${item.dataMode === "demo" ? "演示" : "实时"}</p>
              </div>
              <button class="history-load" type="button" data-history-index="${index}">载入参数</button>
            </div>
          `,
        )
        .join("")
    : '<p class="empty-copy">每次运行会保存摘要与参数，可重新载入后复算。</p>';
}

function loadWatchlist() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_WATCHLIST) || "[]");
    state.watchlist = Array.isArray(parsed)
      ? parsed
          .filter((item) => /^\d{6}$/.test(String(item.symbol || "")))
          .map((item) => ({
            symbol: String(item.symbol),
            name: String(item.name || item.symbol),
            weight: Math.max(0, Number(item.weight) || 0),
          }))
          .slice(0, 20)
      : [];
  } catch (_error) {
    state.watchlist = [];
    window.localStorage.removeItem(STORAGE_WATCHLIST);
  }
}

function saveWatchlist() {
  try {
    window.localStorage.setItem(STORAGE_WATCHLIST, JSON.stringify(state.watchlist));
  } catch (_error) {
    // Keep the in-memory list when storage is disabled.
  }
}

function watchlistTotal() {
  return state.watchlist.reduce((total, item) => total + (Number(item.weight) || 0), 0);
}

function renderWatchlist() {
  const reportRows = new Map((state.riskResult?.holdings || []).map((row) => [row.symbol, row]));
  elements.watchlistStatus.textContent = `${state.watchlist.length} 只 · ${formatPct(watchlistTotal() / 100, 1)}`;
  elements.analyzeRiskButton.disabled =
    !state.watchlist.length ||
    watchlistTotal() > 100.001 ||
    state.watchlist.some((item) => Number(item.weight) <= 0);

  elements.watchlistBody.innerHTML = state.watchlist.length
    ? state.watchlist
        .map((item) => {
          const row = reportRows.get(item.symbol);
          return `
            <tr>
              <td><span class="stock-cell"><strong>${escapeHtml(row?.name || item.name)}</strong><small>${escapeHtml(item.symbol)}</small></span></td>
              <td class="text-cell">${escapeHtml(row?.industry || "待复核")}</td>
              <td>${formatNumber(row?.price, 2)}</td>
              <td class="${metricTone(row?.return_20d)}">${formatPct(row?.return_20d, 1, true)}</td>
              <td>${formatPct(row?.volatility_60d, 1)}</td>
              <td><input class="weight-input" type="number" min="0" max="100" step="0.1" value="${formatDecimal(item.weight, 1)}" data-symbol="${escapeHtml(item.symbol)}" aria-label="${escapeHtml(row?.name || item.name)} 的权重"></td>
              <td>${formatMoney(row?.minimum_lot_value)}</td>
              <td><button class="row-action" type="button" data-remove-symbol="${escapeHtml(item.symbol)}" title="移除 ${escapeHtml(row?.name || item.name)}" aria-label="移除 ${escapeHtml(row?.name || item.name)}"><svg class="icon" aria-hidden="true"><use href="#i-trash"></use></svg></button></td>
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="8" class="table-empty">添加股票后可直接编辑权重并复核风险。</td></tr>`;
}

function renderRiskReview() {
  const report = state.riskResult;
  if (!report) {
    elements.riskReviewResult.dataset.tone = "";
    elements.riskReviewResult.innerHTML = `
      <span class="section-index">INDEPENDENT RISK REVIEW</span>
      <h3>尚未执行组合风控</h3>
      <p>目标组合与研究回测相互独立；这里用于检查手工观察组合的集中度、行业暴露、回撤和相关性。</p>
    `;
    return;
  }
  const summary = report.summary || {};
  const metrics = report.metrics || {};
  const suggestions = report.suggestions || [];
  elements.riskReviewResult.dataset.tone = summary.level || "";
  elements.riskReviewResult.innerHTML = `
    <span class="section-index">INDEPENDENT RISK REVIEW</span>
    <h3>${escapeHtml(summary.headline || summary.label || "组合风险复核")}</h3>
    <p>${escapeHtml(report.meta?.source || "")} · ${escapeHtml(summary.data_as_of || "行情时间未知")} · ${escapeHtml(summary.label || "风险等级未知")}</p>
    <div class="risk-review-grid">
      <div><span>年化波动</span><strong>${formatPct(metrics.annual_volatility, 1)}</strong></div>
      <div><span>最大回撤</span><strong>${formatPct(metrics.max_drawdown, 1)}</strong></div>
      <div><span>单票最高</span><strong>${formatPct(metrics.max_single_weight, 1)}</strong></div>
      <div><span>行业最高</span><strong>${formatPct(metrics.max_sector_weight, 1)}</strong></div>
    </div>
    <div class="risk-review-suggestions">
      ${
        suggestions.length
          ? suggestions
              .slice(0, 5)
              .map((item) => `<p><strong>${escapeHtml(item.title)}</strong> · ${escapeHtml(item.action)}</p>`)
              .join("")
          : "<p>当前阈值内未产生额外建议，仍需复核成交与流动性。</p>"
      }
    </div>
  `;
}

async function analyzeWatchlist() {
  const total = watchlistTotal();
  if (!state.watchlist.length) {
    showNotice("请先向观察组合添加至少一只股票。");
    return;
  }
  if (total > 100.001) {
    showNotice(`观察组合总权重 ${formatDecimal(total, 1)}%，请降至 100% 以内。`);
    return;
  }
  if (state.watchlist.some((item) => Number(item.weight) <= 0)) {
    showNotice("每只股票权重必须大于 0。");
    return;
  }
  let payload;
  try {
    payload = readParameters();
  } catch (error) {
    showNotice(error.message);
    return;
  }
  elements.analyzeRiskButton.disabled = true;
  const originalLabel = elements.analyzeRiskButton.querySelector("span")?.textContent || "独立风控复核";
  const label = elements.analyzeRiskButton.querySelector("span");
  if (label) label.textContent = "复核中";
  try {
    const response = await fetch("/api/risk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data_mode: state.dataMode,
        capital: payload.capital,
        days: Math.max(30, Math.min(500, payload.days)),
        holdings: state.watchlist.map((item) => ({ symbol: item.symbol, weight: item.weight })),
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `风控请求失败 (${response.status})`);
    state.riskResult = result;
    renderWatchlist();
    renderRiskReview();
    showToast("观察组合风控复核完成");
  } catch (error) {
    showNotice(`观察组合风控未完成：${error.message}`);
  } finally {
    if (label) label.textContent = originalLabel;
    renderWatchlist();
  }
}

function addWatchlistItem(symbol, name) {
  const normalized = String(symbol || "").replace(/\D/g, "");
  if (!/^\d{6}$/.test(normalized)) {
    showNotice("股票代码必须是 6 位数字。");
    return;
  }
  if (state.watchlist.some((item) => item.symbol === normalized)) {
    showNotice(`${normalized} 已在观察组合中。`);
    return;
  }
  if (state.watchlist.length >= 20) {
    showNotice("观察组合最多 20 只股票。");
    return;
  }
  const remaining = Math.max(0, 100 - watchlistTotal());
  if (remaining < 0.1) {
    showNotice("总权重已接近 100%，请先调整已有权重。");
    return;
  }
  state.watchlist.push({
    symbol: normalized,
    name: name || normalized,
    weight: Math.min(10, Math.floor(remaining * 10) / 10),
  });
  state.riskResult = null;
  saveWatchlist();
  renderWatchlist();
  renderRiskReview();
  hideSuggestions();
  elements.stockSearch.value = "";
  showToast(`${name || normalized} 已加入观察组合`);
}

function hideSuggestions() {
  state.suggestions = [];
  state.activeSuggestion = -1;
  elements.searchSuggestions.hidden = true;
  elements.searchSuggestions.innerHTML = "";
}

function renderSuggestions(message = "") {
  if (message) {
    elements.searchSuggestions.innerHTML = `<div class="suggestion-message">${escapeHtml(message)}</div>`;
    elements.searchSuggestions.hidden = false;
    return;
  }
  if (!state.suggestions.length) {
    hideSuggestions();
    return;
  }
  elements.searchSuggestions.innerHTML = state.suggestions
    .map(
      (item, index) => `
        <button class="suggestion-item ${index === state.activeSuggestion ? "is-active" : ""}" type="button" role="option" aria-selected="${index === state.activeSuggestion}" data-index="${index}">
          <span class="suggestion-symbol">${escapeHtml(item.symbol)}</span>
          <span class="suggestion-name">${escapeHtml(item.name)}</span>
          <span class="suggestion-market">${escapeHtml(item.market || "A股")}</span>
        </button>
      `,
    )
    .join("");
  elements.searchSuggestions.hidden = false;
}

async function searchStocks(query) {
  if (state.searchController) state.searchController.abort();
  const normalized = query.trim();
  if (normalized.length < 2) {
    hideSuggestions();
    return;
  }
  const controller = new AbortController();
  state.searchController = controller;
  try {
    const response = await fetch(
      `/api/search?q=${encodeURIComponent(normalized)}&data_mode=${encodeURIComponent(state.dataMode)}`,
    );
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `搜索失败 (${response.status})`);
    if (state.searchController !== controller) return;
    state.suggestions = result.items || [];
    state.activeSuggestion = state.suggestions.length ? 0 : -1;
    renderSuggestions(state.suggestions.length ? "" : "没有找到匹配的 A 股");
  } catch (error) {
    if (error.name !== "AbortError") renderSuggestions(`搜索暂不可用：${error.message}`);
  }
}

function handleSearchInput() {
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(() => searchStocks(elements.stockSearch.value), 260);
}

function submitSearch() {
  const selected = state.suggestions[state.activeSuggestion];
  if (selected) {
    addWatchlistItem(selected.symbol, selected.name);
    return;
  }
  const query = elements.stockSearch.value.trim();
  if (/^\d{6}$/.test(query)) {
    addWatchlistItem(query, query);
    return;
  }
  if (state.suggestions.length) {
    addWatchlistItem(state.suggestions[0].symbol, state.suggestions[0].name);
    return;
  }
  showNotice("请输入 6 位股票代码，或从搜索结果中选择股票。");
}

function syncTargetToWatchlist() {
  const holdings = state.result?.holdings || [];
  if (!holdings.length) return;
  state.watchlist = holdings.map((row) => ({
    symbol: String(row.symbol),
    name: String(row.name || row.symbol),
    weight: Math.round(Number(row.weight || 0) * 1000) / 10,
  }));
  state.riskResult = null;
  saveWatchlist();
  renderWatchlist();
  renderRiskReview();
  showToast(`已同步 ${state.watchlist.length} 只目标持仓，可继续编辑后独立复核`);
}

function applyHistory(index) {
  const record = state.history[index];
  if (!record) return;
  applyParameters(record.params);
  showToast(`已载入 ${record.id} 的参数，正在复算`);
  setActiveTab("audit");
  document.getElementById("research")?.scrollIntoView({ behavior: "smooth", block: "start" });
  window.setTimeout(() => runResearch({ automatic: true }), 160);
}

function markdownCell(value) {
  return String(value ?? "")
    .replaceAll("|", "\\|")
    .replaceAll("\n", " ");
}

function buildMarkdown(result) {
  const meta = result.meta || {};
  const summary = result.summary || {};
  const quality = result.data_quality || {};
  const training = result.training || {};
  const context = result.report_context || {};
  const performance = result.performance || {};
  const models = result.model_comparison || [];
  const costs = result.cost_breakdown || {};
  const holdings = result.holdings || [];
  const limitations = context.limitations || [];
  const lines = [];
  lines.push(`# QuantGuard 量化研究报告 ${meta.experiment_id || ""}`);
  lines.push("");
  lines.push(`- 生成时间：${meta.generated_at || "--"}`);
  lines.push(`- 数据模式：${meta.data_mode === "demo" ? "演示数据" : "实时行情"}`);
  lines.push(`- 数据来源：${meta.source || "--"}`);
  lines.push(`- 行情截止：${meta.market_data_as_of || "--"}`);
  lines.push(`- 演示版本：${meta.demo_version || "不适用"}`);
  lines.push(`- 研究资金：${formatMoney(meta.capital)}`);
  lines.push("");
  lines.push("## 1. 研究问题与假设");
  lines.push("");
  lines.push(context.research_question || "检验机器学习模型能否预测未来横截面相对收益。");
  lines.push("");
  lines.push("## 2. 数据来源、清洗与质量");
  lines.push("");
  lines.push(`- 请求股票数：${formatInteger(quality.requested_symbols)}`);
  lines.push(`- 有效股票数：${formatInteger(quality.loaded_symbols)}`);
  lines.push(`- 交易日：${formatInteger(quality.trading_days)}（${quality.date_start || "--"} 至 ${quality.date_end || "--"}）`);
  lines.push(`- 有效标签样本：${formatInteger(quality.labeled_rows)}`);
  lines.push(`- 重复记录：${formatInteger(quality.duplicate_records)}`);
  lines.push(`- 非法价格：${formatInteger(quality.invalid_price_records)}`);
  lines.push(`- 前向填充单元：${formatInteger(quality.forward_filled_cells)}`);
  lines.push(`- 短历史排除：${formatInteger(quality.excluded_short_history_count)} 只`);
  lines.push("");
  lines.push("清洗规则：");
  (quality.cleaning_rules || []).forEach((rule) => lines.push(`- ${rule}`));
  lines.push("");
  lines.push("## 3. 特征工程与标签");
  lines.push("");
  lines.push(`标签：${context.label_definition || training.selection_rule || "未来横截面超额收益"}`);
  lines.push("");
  lines.push("| 特征 | 分组 | 公式 | 时点 |");
  lines.push("| --- | --- | --- | --- |");
  (result.feature_catalog || []).forEach((feature) => {
    lines.push(
      `| ${markdownCell(feature.label)} | ${markdownCell(feature.category)} | ${markdownCell(feature.formula)} | ${markdownCell(feature.timing)} |`,
    );
  });
  lines.push("");
  lines.push("## 4. 时间切分与防泄漏");
  lines.push("");
  lines.push(`- 开发期：${training.development_start || "--"} 至 ${training.development_end || "--"}`);
  lines.push(`- 留出期：${training.holdout_start || "--"} 至 ${training.holdout_end || "--"}`);
  lines.push(`- 滚动协议：${training.evaluation_frequency || "--"}`);
  (training.leakage_checks || []).forEach((check) => {
    lines.push(`- ${check.name}：${check.detail}`);
  });
  lines.push("");
  lines.push("## 5. 模型选择与样本外证据");
  lines.push("");
  lines.push(`- 选定模型：${training.selected_label || "--"}`);
  lines.push(`- 选择依据：${training.selection_reason || "--"}`);
  lines.push(`- 验证 Rank IC：${formatDecimal(summary.validation_ic, 4)}`);
  lines.push(`- 留出 Rank IC：${formatDecimal(summary.holdout_ic, 4)}`);
  lines.push(`- 留出 R²：${formatDecimal(summary.holdout_r2, 4)}`);
  lines.push("");
  lines.push("| 模型 | 状态 | 验证 IC | 留出 IC | 留出 R² | Top-20% 捕获 | 是否入选 |");
  lines.push("| --- | --- | --- | --- | --- | --- | --- |");
  models.forEach((row) => {
    const validation = row.validation || {};
    const holdout = row.holdout || {};
    lines.push(
      `| ${markdownCell(row.label)} | ${markdownCell(row.status)} | ${formatDecimal(row.validation_ic ?? validation.rank_ic_mean, 4)} | ${formatDecimal(row.holdout_ic ?? holdout.rank_ic_mean, 4)} | ${formatDecimal(row.holdout_r2 ?? holdout.r2_oos, 4)} | ${formatPct(row.top_bucket_capture ?? holdout.top_bucket_capture, 2)} | ${row.selected ? "是" : "否"} |`,
    );
  });
  lines.push("");
  lines.push("## 6. 分阶段绩效");
  lines.push("");
  lines.push("| 阶段 | 毛年化 | 成本拖累 | 净年化 | 基准 | 净超额 | 最大回撤 | 夏普 |");
  lines.push("| --- | --- | --- | --- | --- | --- | --- | --- |");
  [
    ["全样本", performance.full],
    ["开发期", performance.development],
    ["留出期", performance.holdout],
  ].forEach(([label, row]) => {
    const data = row || {};
    lines.push(
      `| ${label} | ${formatPct(data.gross_annual_return, 2)} | ${formatPct(data.annual_cost_drag, 2)} | ${formatPct(data.net_annual_return, 2)} | ${formatPct(data.benchmark_annual_return, 2)} | ${formatPct(data.net_excess_annual_return, 2)} | ${formatPct(data.max_drawdown, 2)} | ${formatDecimal(data.sharpe_ratio, 3)} |`,
    );
  });
  lines.push("");
  lines.push("## 7. 成本明细");
  lines.push("");
  lines.push("| 成本项 | 金额 | 占初始资金 |");
  lines.push("| --- | --- | --- |");
  Object.entries(COST_LABELS).forEach(([key, label]) => {
    lines.push(
      `| ${label} | ${formatMoney(costs[key]?.currency)} | ${formatPct(costs[key]?.pct_of_capital, 4)} |`,
    );
  });
  lines.push("");
  lines.push("## 8. 目标持仓与执行");
  lines.push("");
  lines.push("| 股票 | 行业 | 目标权重 | 股数 | 金额 | 退出天数 | 冲击成本 |");
  lines.push("| --- | --- | --- | --- | --- | --- | --- |");
  holdings.forEach((row) => {
    lines.push(
      `| ${markdownCell(row.name)} (${markdownCell(row.symbol)}) | ${markdownCell(row.industry)} | ${formatPct(row.weight, 2)} | ${formatInteger(row.shares)} | ${formatMoney(row.position_value)} | ${formatDecimal(row.days_to_exit, 2)} | ${formatDecimal(row.impact_bps, 2)} bp |`,
    );
  });
  lines.push("");
  lines.push("## 9. 风险行动");
  lines.push("");
  (result.risk?.actions || []).forEach((action) => {
    lines.push(`- [${action.severity}] ${action.title}：${action.action}`);
  });
  lines.push("");
  lines.push("## 10. 局限性与下一步验证");
  lines.push("");
  limitations.forEach((item) => lines.push(`- ${item}`));
  lines.push("");
  lines.push(`> ${context.disclaimer || "仅用于课程研究和个人研究，不构成投资建议或收益承诺。"}`);
  lines.push("");
  return lines.join("\n");
}

function downloadBlob(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportMarkdown() {
  if (!state.result) return;
  const id = state.result.meta?.experiment_id || "quantguard-report";
  downloadBlob(buildMarkdown(state.result), `${id}.md`, "text/markdown;charset=utf-8");
  showToast("Markdown 研究报告已导出");
}

function exportJson() {
  if (!state.result) return;
  const id = state.result.meta?.experiment_id || "quantguard-result";
  const payload = {
    application: "QuantGuard",
    exported_at: new Date().toISOString(),
    parameters: state.result.meta?.parameter_summary || {},
    result: state.result,
  };
  downloadBlob(JSON.stringify(payload, null, 2), `${id}.json`, "application/json;charset=utf-8");
  showToast("JSON 原始结果已导出");
}

function openTour() {
  state.tourStep = 0;
  elements.tourOverlay.hidden = false;
  renderTour();
  $("[data-tour-close]", elements.tourOverlay)?.focus();
}

function closeTour() {
  elements.tourOverlay.hidden = true;
}

function renderTour() {
  const step = TOUR_STEPS[state.tourStep];
  elements.tourStepLabel.textContent = `STEP ${state.tourStep + 1} / ${TOUR_STEPS.length}`;
  elements.tourTitle.textContent = step.title;
  elements.tourBody.textContent = step.body;
  elements.tourPoints.innerHTML = step.points
    .map((point, index) => `<div class="tour-point"><b>${String(index + 1).padStart(2, "0")}</b><span>${escapeHtml(point)}</span></div>`)
    .join("");
  elements.tourProgressBar.style.width = `${((state.tourStep + 1) / TOUR_STEPS.length) * 100}%`;
  elements.tourPreviousButton.disabled = state.tourStep === 0;
  elements.tourNextButton.querySelector("span").textContent =
    state.tourStep === TOUR_STEPS.length - 1 ? "完成" : "下一步";

  if (step.tab) setActiveTab(step.tab);
  const target = document.querySelector(step.target);
  target?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function nextTour() {
  if (state.tourStep >= TOUR_STEPS.length - 1) {
    closeTour();
    showToast("五步研究链路导览完成");
    return;
  }
  state.tourStep += 1;
  renderTour();
}

function previousTour() {
  if (state.tourStep <= 0) return;
  state.tourStep -= 1;
  renderTour();
}

function registerNavigation() {
  const navLinks = $$(".nav-link");
  navLinks.forEach((link) => {
    link.addEventListener("click", () => {
      navLinks.forEach((item) => item.classList.toggle("is-active", item === link));
    });
  });
  if ("IntersectionObserver" in window) {
    const sections = ["overview", "research", "portfolio", "archive"]
      .map((id) => document.getElementById(id))
      .filter(Boolean);
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
        if (!visible) return;
        navLinks.forEach((link) => {
          link.classList.toggle("is-active", link.getAttribute("href") === `#${visible.target.id}`);
        });
      },
      { rootMargin: "-15% 0px -58% 0px", threshold: [0.06, 0.2, 0.5] },
    );
    sections.forEach((section) => observer.observe(section));
  }
}

function registerEvents() {
  elements.researchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    runResearch();
  });

  elements.noticeClose.addEventListener("click", clearNotice);
  $$(".tab-button").forEach((button) => {
    button.addEventListener("click", () => setActiveTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const tabs = $$(".tab-button");
      const current = tabs.indexOf(button);
      const next = event.key === "ArrowRight" ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
      tabs[next].focus();
      setActiveTab(tabs[next].dataset.tab);
    });
  });
  $$("[data-dataset-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.datasetView = button.dataset.datasetView;
      state.datasetOffset = 0;
      updateDatasetViewButtons();
      loadDatasetPage();
    });
  });
  elements.datasetFilterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (
      elements.datasetDateFrom.value
      && elements.datasetDateTo.value
      && elements.datasetDateFrom.value > elements.datasetDateTo.value
    ) {
      setDatasetStatus("开始日期不能晚于结束日期。", "error");
      return;
    }
    state.datasetOffset = 0;
    loadDatasetPage();
  });
  elements.datasetFilterForm.addEventListener("reset", () => {
    window.setTimeout(() => {
      state.datasetOffset = 0;
      loadDatasetPage();
    }, 0);
  });
  elements.datasetPreviousPage.addEventListener("click", () => {
    state.datasetOffset = Math.max(0, state.datasetOffset - state.datasetLimit);
    loadDatasetPage();
  });
  elements.datasetNextPage.addEventListener("click", () => {
    state.datasetOffset += state.datasetLimit;
    loadDatasetPage();
  });
  elements.datasetRawDownload.addEventListener("click", () => downloadDatasetCsv("raw"));
  elements.datasetModelDownload.addEventListener("click", () => downloadDatasetCsv("model"));

  elements.navChartShell.addEventListener("pointermove", handleChartPointer);
  elements.navChartShell.addEventListener("pointerleave", () => {
    elements.navTooltip.hidden = true;
  });

  elements.syncTargetButton.addEventListener("click", syncTargetToWatchlist);
  elements.stockSearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitSearch();
  });
  elements.stockSearch.addEventListener("input", handleSearchInput);
  elements.stockSearch.addEventListener("keydown", (event) => {
    if (!state.suggestions.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      state.activeSuggestion = (state.activeSuggestion + 1) % state.suggestions.length;
      renderSuggestions();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      state.activeSuggestion = (state.activeSuggestion - 1 + state.suggestions.length) % state.suggestions.length;
      renderSuggestions();
    } else if (event.key === "Escape") {
      hideSuggestions();
    }
  });
  elements.searchSuggestions.addEventListener("click", (event) => {
    const item = event.target.closest(".suggestion-item");
    if (!item) return;
    const suggestion = state.suggestions[Number(item.dataset.index)];
    if (suggestion) addWatchlistItem(suggestion.symbol, suggestion.name);
  });
  document.addEventListener("click", (event) => {
    if (!elements.stockSearchForm.contains(event.target)) hideSuggestions();
  });
  elements.watchlistBody.addEventListener("change", (event) => {
    if (!event.target.classList.contains("weight-input")) return;
    const symbol = event.target.dataset.symbol;
    const item = state.watchlist.find((row) => row.symbol === symbol);
    if (!item) return;
    const value = Number(event.target.value);
    if (!Number.isFinite(value) || value <= 0 || value > 100) {
      showNotice("权重必须是大于 0 且不超过 100 的数字。");
      renderWatchlist();
      return;
    }
    item.weight = value;
    state.riskResult = null;
    saveWatchlist();
    renderWatchlist();
    renderRiskReview();
  });
  elements.watchlistBody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-symbol]");
    if (!button) return;
    state.watchlist = state.watchlist.filter((item) => item.symbol !== button.dataset.removeSymbol);
    state.riskResult = null;
    saveWatchlist();
    renderWatchlist();
    renderRiskReview();
  });
  elements.clearWatchlistButton.addEventListener("click", () => {
    state.watchlist = [];
    state.riskResult = null;
    saveWatchlist();
    renderWatchlist();
    renderRiskReview();
  });
  elements.analyzeRiskButton.addEventListener("click", analyzeWatchlist);

  elements.exportMarkdownButton.addEventListener("click", exportMarkdown);
  elements.exportJsonButton.addEventListener("click", exportJson);
  elements.openTourButton.addEventListener("click", openTour);
  elements.tourOpenButton.addEventListener("click", openTour);
  elements.tourPreviousButton.addEventListener("click", previousTour);
  elements.tourNextButton.addEventListener("click", nextTour);
  $$("[data-tour-close]", elements.tourOverlay).forEach((element) => {
    element.addEventListener("click", closeTour);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.tourOverlay.hidden) closeTour();
  });
  elements.experimentHistory.addEventListener("click", (event) => {
    const button = event.target.closest("[data-history-index]");
    if (!button) return;
    applyHistory(Number(button.dataset.historyIndex));
  });
}

function initialize() {
  state.dataMode = "live";
  loadHistory();
  loadWatchlist();
  updateModeUI();
  renderHistory();
  renderWatchlist();
  renderRiskReview();
  setActiveTab("audit");
  registerEvents();
  registerNavigation();
  loadLiveMarketSnapshot();
  window.setTimeout(() => runResearch({ automatic: true }), 300);
}

initialize();
