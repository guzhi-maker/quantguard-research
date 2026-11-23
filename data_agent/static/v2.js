/* v2 组合研究页逻辑：只调用 /api/v2/*，不依赖 app.js。 */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);

  const CHECK_LABELS = {
    ic_ok: "信号 IC",
    coverage_ok: "区间覆盖率",
    calibration_ok: "校准",
    cvar_monotonic: "CVaR 单调",
    reject_rate_ok: "拒单率",
    evidence_size_ok: "证据包大小",
    leakage_free: "无数据泄漏",
    meta_auc_ok: "元模型 AUC",
  };

  const STATUS_TEXT = {
    queued: "排队中",
    running: "分析中",
    done: "已完成",
    failed: "失败",
  };

  const state = { jobId: null, timer: null, result: null, running: false };

  function fmtInt(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toLocaleString("zh-CN");
  }

  function fmtPct(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return (Number(value) * 100).toFixed(digits === undefined ? 2 : digits) + "%";
  }

  function esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function riskLabel(value) {
    const points = [[1, "保守"], [3.25, "稳健"], [5.5, "平衡"], [7.75, "进取"], [10, "激进"]];
    let best = points[0];
    for (const point of points) {
      if (Math.abs(point[0] - value) < Math.abs(best[0] - value)) best = point;
    }
    return best[1];
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || "请求失败：" + response.status);
    }
    return data;
  }

  function showError(message) {
    const box = $("#v2Error");
    box.textContent = message;
    box.hidden = false;
  }

  function hideError() {
    $("#v2Error").hidden = true;
  }

  function setRunning(running) {
    state.running = running;
    $("#v2Run").disabled = running;
    $("#v2Run").textContent = running ? "分析中…" : "开始分析";
    $("#v2Dot").classList.toggle("busy", running);
    $("#v2Status").textContent = running ? "任务运行中" : "就绪";
  }

  async function loadSnapshots() {
    const select = $("#v2Snapshot");
    const hint = $("#v2SnapshotHint");
    try {
      const data = await api("/api/v2/snapshots");
      select.innerHTML = "";
      if (!data.items.length) {
        select.innerHTML = '<option value="">没有可用快照，请先在数据台采集</option>';
        hint.textContent = "需要先采集至少一份快照";
        return;
      }
      for (const snap of data.items) {
        const option = document.createElement("option");
        option.value = snap.run_id;
        option.textContent =
          snap.run_id +
          " · " +
          snap.symbols +
          " 只 · " +
          fmtInt(snap.rows) +
          " 行 · " +
          snap.date_min +
          " ~ " +
          snap.date_max;
        select.appendChild(option);
      }
      select.value = data.items[0].run_id;
      hint.textContent = "共 " + data.items.length + " 份快照，默认选最新";
    } catch (error) {
      hint.textContent = "快照读取失败：" + error.message;
    }
  }

  function renderStatus(job) {
    const pill = $("#v2Pill");
    pill.textContent = STATUS_TEXT[job.status] || job.status;
    pill.className = "v2-pill " + (job.status === "failed" ? "failed" : job.status === "done" ? "done" : "");
    $("#v2Stage").textContent = job.stage || "等待开始";
    const elapsed = job.elapsed_seconds;
    $("#v2Elapsed").textContent =
      elapsed === null || elapsed === undefined ? "" : "已用 " + Math.round(elapsed) + " 秒";
  }

  async function submit(event) {
    event.preventDefault();
    if (state.running) return;
    hideError();
    $("#v2Result").hidden = true;
    $("#v2JobBar").hidden = false;

    const payload = {
      run_id: $("#v2Snapshot").value,
      risk: Number($("#v2Risk").value),
      horizon: Number($("#v2Horizon").value),
      capital: Number($("#v2Capital").value),
      seeds: Number($("#v2Seeds").value),
      positions: Number($("#v2Positions").value),
      holdings: $("#v2Holdings").value.trim(),
      with_report: $("#v2Report").checked,
    };

    try {
      setRunning(true);
      const job = await api("/api/v2/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.jobId = job.job_id;
      $("#v2Pill").textContent = "排队中";
      $("#v2Stage").textContent = "等待调度";
      poll();
    } catch (error) {
      showError(error.message);
      setRunning(false);
    }
  }

  function poll() {
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      try {
        const job = await api("/api/v2/jobs/" + encodeURIComponent(state.jobId));
        renderStatus(job);
        if (job.status === "done") {
          state.result = job.result;
          render(job.result);
          setRunning(false);
          return;
        }
        if (job.status === "failed") {
          showError(job.error || "分析失败");
          setRunning(false);
          return;
        }
        poll();
      } catch (error) {
        showError(error.message);
        setRunning(false);
      }
    }, 2500);
  }

  function card(label, value, sub) {
    return (
      '<div class="v2-card"><span class="label">' +
      esc(label) +
      '</span><span class="value">' +
      esc(value) +
      "</span>" +
      (sub ? '<span class="sub">' + esc(sub) + "</span>" : "") +
      "</div>"
    );
  }

  function renderOverview(payload) {
    const panel = payload.panel || {};
    const model = payload.model || {};
    const metrics = payload.metrics || {};
    const rankIc = metrics.rank_ic || {};
    const top = metrics.top_bucket || {};
    const calibration = (metrics.calibration && metrics.calibration.holdout_coverage) || {};

    const items = [
      card("数据快照", panel.run_id || "—", panel.symbols + " 只 · " + fmtInt(panel.rows) + " 行"),
      card("数据区间", (panel.start || "—") + " ~ " + panel.end, "行业 " + (panel.industries || "—") + " 个"),
      card("模型", model.version || "—", "训练 " + fmtInt(model.train_rows) + " 行"),
      card("Rank IC", Number(rankIc.ic_mean || 0).toFixed(4), "IR " + Number(rankIc.ic_ir || 0).toFixed(2) + " · 胜率 " + fmtPct(rankIc.ic_win_rate, 1)),
      card("Top 组超额", fmtPct(top.top_excess), (top.dates || 0) + " 个交易日"),
      card("校准后覆盖", fmtPct(calibration.p10_breach, 1) + " / " + fmtPct(calibration.p90_breach, 1), "名义 10% / 10%"),
      card("有效独立样本", fmtInt(metrics.effective_sample), metrics.validation_mode === "holdout" ? "单次留出" : "滚动前推"),
    ];
    $("#v2Overview").innerHTML = items.join("");
  }

  function renderChecks(payload) {
    const acceptance = payload.acceptance || {};
    const chips = Object.keys(CHECK_LABELS).map((key) => {
      const value = acceptance[key];
      let cls = "skip";
      let mark = "—";
      if (value === true) {
        cls = "ok";
        mark = "✓";
      } else if (value === false) {
        cls = "bad";
        mark = "✗";
      }
      return '<span class="v2-check-item ' + cls + '">' + mark + " " + esc(CHECK_LABELS[key]) + "</span>";
    });
    $("#v2Checks").innerHTML = chips.join("");
    const warnings = payload.warnings || [];
    $("#v2Warnings").textContent = warnings.length ? "提示：" + warnings.join("；") : "没有额外提示。";
  }

  function renderLadder(payload) {
    const rows = (payload.ladder || [])
      .map((row) => {
        return (
          "<tr><td class=\"strong\">" +
          esc(row.label) +
          "</td><td>" +
          fmtPct(row.cvar95, 1) +
          "</td><td>" +
          fmtPct(row.expected_return, 2) +
          "</td><td>" +
          fmtPct(row.volatility, 1) +
          "</td><td>" +
          fmtPct(row.gross_exposure, 1) +
          "</td><td>" +
          fmtInt(row.positions) +
          "</td><td>" +
          fmtPct(row.cash_weight, 1) +
          "</td><td>" +
          fmtPct(row.cost_estimate, 2) +
          "</td></tr>"
        );
      })
      .join("");
    $("#v2Ladder").innerHTML =
      "<thead><tr><th>档位</th><th>CVaR</th><th>预期/月</th><th>波动</th><th>仓位</th><th>持仓</th><th>现金</th><th>成本</th></tr></thead><tbody>" +
      rows +
      "</tbody>";
  }

  function renderSelected(payload) {
    const selected = payload.selected || {};
    const summary = selected.summary || {};
    const names = payload.names || {};
    $("#v2SelectedTitle").textContent = "推荐组合 · " + (selected.label || "");

    $("#v2SelectedSummary").innerHTML = [
      card("持仓数量", fmtInt(summary.positions), "现金 " + fmtPct(selected.cash_weight, 1)),
      card("预期收益/月", fmtPct(summary.expected_return), "扣费后"),
      card("CVaR", fmtPct(summary.cvar95, 1), "最差 5% 月份"),
      card("Top3 权重", fmtPct(summary.top3_weight, 1), "HHI " + Number(summary.hhi || 0).toFixed(3)),
      card("换手成本", fmtPct(summary.cost_estimate, 2), "本次建仓"),
    ].join("");

    const rows = (selected.rows || []).slice();
    rows.sort((a, b) => a.rank - b.rank);
    const body = rows
      .map((row) => {
        return (
          "<tr><td>" +
          row.rank +
          "</td><td>" +
          esc(row.symbol) +
          "</td><td>" +
          esc(row.name || "—") +
          "</td><td>" +
          esc(row.industry || "—") +
          "</td><td>" +
          fmtPct(row.weight, 2) +
          "</td><td>" +
          fmtPct(row.expected, 2) +
          "</td></tr>"
        );
      })
      .join("");
    $("#v2Selected").innerHTML =
      "<thead><tr><th>#</th><th>代码</th><th>名称</th><th>行业</th><th>建议权重</th><th>预期/20日</th></tr></thead><tbody>" +
      body +
      "</tbody>";

    const lots = selected.lots || {};
    const lotBody = rows
      .map((row) => {
        const lot = lots[row.symbol];
        const lotText = lot === undefined ? "—" : lot === 0 ? "买不起一手" : lot + " 手";
        return (
          "<tr><td>" +
          row.rank +
          "</td><td>" +
          esc(row.symbol) +
          "</td><td>" +
          fmtPct(row.weight, 2) +
          "</td><td>" +
          esc(lotText) +
          "</td></tr>"
        );
      })
      .join("");
    $("#v2SelectedLots").innerHTML =
      "<thead><tr><th>#</th><th>代码</th><th>建议权重</th><th>整手参考</th></tr></thead><tbody>" +
      lotBody +
      "</tbody>";
  }

  function renderPartial(payload) {
    const section = $("#v2PartialSection");
    const rows = ((payload.selected || {}).rows || []).slice();
    if (rows.length < 6) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    rows.sort((a, b) => b.weight - a.weight);
    const gross = rows.reduce((sum, row) => sum + row.weight, 0);
    const targets = [5, 8, 10].filter((n) => n < rows.length);
    targets.push(rows.length);

    const body = targets
      .map((n) => {
        const top = rows.slice(0, n);
        const rawSum = top.reduce((sum, row) => sum + row.weight, 0);
        const scale = rawSum > 0 ? gross / rawSum : 0;
        const expected = top.reduce((sum, row) => sum + row.weight * scale * row.expected, 0);
        const topWeight = (top[0].weight || 0) * scale;
        const label = n === rows.length ? "全部 " + n + " 只" : "前 " + n + " 只";
        return (
          "<tr><td>" +
          label +
          "</td><td>" +
          fmtPct(gross, 1) +
          "</td><td>" +
          fmtPct(1 - gross, 1) +
          "</td><td>" +
          fmtPct(topWeight, 1) +
          "</td><td>" +
          fmtPct(expected, 2) +
          "</td><td>" +
          (n === rows.length ? "原方案" : "×" + scale.toFixed(2)) +
          "</td></tr>"
        );
      })
      .join("");
    $("#v2Partial").innerHTML =
      "<thead><tr><th>取法</th><th>买入占比</th><th>现金</th><th>单票最高</th><th>预期/20日</th><th>权重放大</th></tr></thead><tbody>" +
      body +
      "</tbody>";
  }

  function renderDiagnosis(payload) {
    const diagnosis = payload.diagnosis || {};
    $("#v2DiagHeadline").textContent = diagnosis.headline || "";
    const items = (diagnosis.items || [])
      .map((item) => {
        const status = item.status === "red" ? "red" : item.status === "yellow" ? "yellow" : "green";
        return (
          '<div class="v2-dim ' +
          status +
          '"><h3>' +
          esc(item.label) +
          '<span class="status ' +
          status +
          '">' +
          esc(item.status_label) +
          "</span></h3><p>" +
          esc(item.evidence) +
          "</p><p>动作：" +
          esc(item.action) +
          "</p></div>"
        );
      })
      .join("");
    $("#v2Diagnosis").innerHTML =
      items || '<p class="v2-hint">空仓模式：未提供持仓，只给出目标组合与建仓计划。</p>';
  }

  function renderAdvice(payload) {
    const advice = payload.advice || {};
    const actions = advice.actions || [];
    $("#v2AdviceMeta").textContent =
      "预算 " +
      (advice.budget === undefined ? "—" : advice.budget) +
      " 条 · 安全类 " +
      (advice.safety_count || 0) +
      " 条（不占预算）· 优化类 " +
      (advice.optimization_count || 0) +
      " 条 · 折叠 " +
      (advice.folded_count || 0) +
      " 条";
    if (!actions.length) {
      $("#v2Advice").innerHTML =
        '<tbody><tr><td colspan="7">当前没有需要调整的持仓。</td></tr></tbody>';
      return;
    }
    const body = actions
      .map((item) => {
        return (
          "<tr><td>" +
          esc(item.symbol) +
          "</td><td>" +
          esc(item.name || "—") +
          "</td><td>" +
          esc(item.action) +
          "</td><td>" +
          fmtPct(item.delta, 2) +
          "</td><td>" +
          esc(item.kind) +
          "</td><td>" +
          fmtPct(item.improvement, 2) +
          " / " +
          fmtPct(item.cost, 2) +
          "</td><td>" +
          esc(item.reason || "") +
          "</td></tr>"
        );
      })
      .join("");
    $("#v2Advice").innerHTML =
      "<thead><tr><th>代码</th><th>名称</th><th>动作</th><th>变动</th><th>类型</th><th>改善/成本</th><th>原因</th></tr></thead><tbody>" +
      body +
      "</tbody>";
  }

  function renderEvidence(payload) {
    const size = payload.evidence_size || {};
    const report = payload.report;
    $("#v2Evidence").innerHTML = [
      card("证据包", fmtInt(size.tokens) + " token", "上限 " + fmtInt(size.limit)),
      card("快照截止", payload.as_of || "—", "生成时间 " + new Date().toLocaleString("zh-CN")),
      card("警告", String((payload.warnings || []).length), "见上方验收区"),
    ].join("");

    const box = $("#v2ReportText");
    if (!report) {
      box.textContent = "未生成 AI 简报（勾选后需要 DeepSeek 密钥；无密钥会回退模板）。";
    } else if (report.text) {
      box.textContent = report.text;
    } else if (report.error) {
      box.textContent = "AI 简报生成失败：" + report.error;
    } else {
      box.textContent = "AI 简报已完成。";
    }
  }

  function render(payload) {
    if (!payload) return;
    state.result = payload;
    renderOverview(payload);
    renderChecks(payload);
    renderLadder(payload);
    renderSelected(payload);
    renderPartial(payload);
    renderDiagnosis(payload);
    renderAdvice(payload);
    renderEvidence(payload);
    $("#v2Result").hidden = false;
  }

  function download() {
    if (!state.result) return;
    const blob = new Blob([JSON.stringify(state.result, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "v2-result-" + (state.result.run_id || "run") + ".json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function bind() {
    $("#v2Form").addEventListener("submit", submit);
    $("#v2Risk").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      $("#v2RiskValue").textContent = value.toFixed(1);
      $("#v2RiskLabel").textContent = riskLabel(value);
    });
    $("#v2Download").addEventListener("click", download);
  }

  bind();
  loadSnapshots();
})();
