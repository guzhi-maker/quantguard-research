// API and rendered-data contract checks for the local QuantGuard service.
// Usage: node tools/check_api.mjs
const BASE = "http://127.0.0.1:8789";

async function json(path) {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

const overview = await json("/api/overview");
const runs = await json("/api/runs?limit=5");
const audits = await json("/api/audits?limit=5");
const latestRun = runs.items?.[0];
const latestAudit = audits.items?.[0];

if (!latestRun || latestRun.run_id !== "RAW-20261110-124549-780A2D") {
  throw new Error("Latest thousand-stock snapshot is not first in /api/runs");
}
if (latestRun.summary?.symbols < 1000) {
  throw new Error("Latest snapshot has fewer than 1000 successful stocks");
}
if ("symbols" in (latestRun.request || {})) {
  throw new Error("/api/runs still exposes the full symbol list");
}
for (const run of overview.recent_runs || []) {
  if ("trace" in run) throw new Error("/api/overview exposes trace");
  if ("symbols" in (run.request || {})) {
    throw new Error("/api/overview exposes the full symbol list");
  }
}
if (!latestAudit) throw new Error("No saved stock research report");
const assessments = latestAudit.assessments || [];
if (!assessments.length) throw new Error("Latest report has no assessments");
for (const stock of assessments) {
  if (stock.indicator_details?.length !== 14) {
    throw new Error(`${stock.symbol} does not have 14 indicator details`);
  }
  if (stock.research_steps?.length !== 6) {
    throw new Error(`${stock.symbol} does not have 6 research steps`);
  }
}

const html = await (await fetch(`${BASE}/`)).text();
for (const id of [
  "dataPoolForm",
  "datasetSelect",
  "reportTitle",
  "researchChain",
  "indicatorTable",
  "researchDesign",
]) {
  if (!html.includes(`id="${id}"`)) throw new Error(`Missing UI element ${id}`);
}

console.log(
  JSON.stringify(
    {
      latest_run: latestRun.run_id,
      symbols: latestRun.summary.symbols,
      daily_rows: latestRun.summary.daily_rows,
      failures: latestRun.summary.failures,
      latest_audit: latestAudit.audit_id,
      reports: assessments.length,
      indicators_per_stock: assessments.map((item) => item.indicator_details.length),
      steps_per_stock: assessments.map((item) => item.research_steps.length),
      overview_bytes: JSON.stringify(overview).length,
      runs_bytes: JSON.stringify(runs).length,
      checks: "passed",
    },
    null,
    2,
  ),
);
