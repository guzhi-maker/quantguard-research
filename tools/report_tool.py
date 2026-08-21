"""Markdown 研报生成：指标、图表、参数、风险归因与阶段 2 迭代对比。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from tools import visual_tool
from tools.backtest_tool import BacktestResult
from tools.performance_tool import metrics_table


def _fmt(value, digits: int = 2, is_pct: bool = True) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if is_pct:
        return f"{value * 100:.{digits}f}%"
    return f"{value:.{digits}f}"


def _md_path(path) -> str:
    return str(Path(path)).replace("\\", "/")




def _json_scalar(value):
    if isinstance(value, (pd.Series, pd.DataFrame)) or hasattr(value, "to_dict"):
        return None
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _df_table(df: pd.DataFrame, digits: int = 3) -> str:
    if df is None or df.empty:
        return "无数据"
    lines = ["| " + " | ".join(str(c) for c in df.columns) + " |"]
    lines.append("|" + "|".join(["---"] * len(df.columns)) + "|")
    for _, row in df.iterrows():
        cells = []
        for value in row:
            if isinstance(value, float) and value == value:
                cells.append(f"{value:.{digits}f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_markdown_report(
    result: BacktestResult,
    metrics: dict,
    strategy_name: str,
    factor_name: str,
    benchmark_name: str = "沪深300",
    universe_desc: str = "沪深300 成分股",
    output_dir: str | Path = "reports/output/mvp_demo",
    risk_summary: Optional[str] = None,
    sector_exposure: Optional[pd.DataFrame] = None,
    style_exposure: Optional[pd.DataFrame] = None,
) -> str:
    """生成标准化研报并保存图表与 Markdown 文件，返回报告路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    equity_path = visual_tool.plot_equity_curve(
        result.nav,
        benchmark_nav=metrics.get("benchmark_nav"),
        save_path=out / "equity_curve.png",
        title=f"{strategy_name} 净值曲线",
        benchmark_name=benchmark_name,
    )
    dd_path = visual_tool.plot_drawdown(result.nav, save_path=out / "drawdown.png")
    monthly_path = visual_tool.plot_monthly_returns_heatmap(
        result.returns, save_path=out / "monthly_returns.png"
    )

    rows = []
    for _, row in metrics_table(metrics).iterrows():
        name = row["指标"]
        is_pct = name in {
            "年化收益率", "年化波动率", "最大回撤", "累计收益率",
            "年化换手率", "基准年化收益率", "基准最大回撤", "年化超额收益", "超额波动率",
        }
        rows.append(f"| {name} | {_fmt(row['数值'], is_pct=is_pct)} |")
    metrics_md = "\n".join(rows)

    params = result.params
    cost = params["cost_model"]
    start_date = pd.Timestamp(result.nav.index[0]).date()
    end_date = pd.Timestamp(result.nav.index[-1]).date()
    last_rebalance = (
        pd.Timestamp(result.rebalance_dates[-1]).date() if result.rebalance_dates else "-"
    )
    report = f"""# {strategy_name} 回测研报

## 1. 摘要

- **策略逻辑**: {factor_name} 因子选股，每期持有前 {params['top_n']} 只，{params['weight_scheme']} 加权。
- **调仓频率**: {params['rebalance_freq']}（月末）  |  **股票池**: {universe_desc}
- **回测区间**: {start_date} 至 {end_date}
- **核心结论**: 年化收益 {_fmt(metrics.get('annual_return'))}，夏普 {_fmt(metrics.get('sharpe_ratio'), is_pct=False)}，最大回撤 {_fmt(metrics.get('max_drawdown'))}。

## 2. 策略设计

- 因子：{factor_name}，截面 3σ 去极值 + Z-Score 标准化后取排名靠前标的。
- 选股规则：剔除 ST 及缺失值标的，每期等权持有前 {params['top_n']} 名。
- 调仓规则：{params['rebalance_freq']} 收盘生成信号，下一交易日生效（T+1 约束）。
- 仓位规则：{params['weight_scheme']}，单票上限 {params.get('max_single_weight', '-')}。

## 3. 回测设置

| 项目 | 设置 |
| --- | --- |
| 回测区间 | {start_date} 至 {end_date} |
| 股票池 | {universe_desc} |
| 基准指数 | {benchmark_name} |
| 佣金 | {_fmt(cost['commission_rate'])}（单笔最低 {cost['min_commission']} 元） |
| 印花税 | {_fmt(cost['stamp_tax_rate'])}，仅卖出 |
| 过户费 | {_fmt(cost['transfer_fee_rate'], digits=4)}，双向 |
| 滑点 | {_fmt(cost['slippage_rate'])}，双向 |

## 4. 绩效分析

| 指标 | 数值 |
| --- | --- |
{metrics_md}

![净值曲线]({_md_path(equity_path)})

![回撤曲线]({_md_path(dd_path)})

![月度收益热力图]({_md_path(monthly_path)})

## 5. 风险归因

**风控意见**：{risk_summary or "风控校验通过"}

### 5.1 行业暴露

{_df_table(sector_exposure) if sector_exposure is not None else "无行业映射数据"}

### 5.2 风格暴露

{_df_table(style_exposure) if style_exposure is not None else "无因子暴露数据"}

## 6. 风险提示

- 本报告由量化工具自动生成，展示的是历史模拟结果，不代表未来收益。
- 已包含佣金、印花税、过户费与滑点假设，但未覆盖涨跌停无法成交、停牌等极端情形。
- 策略有效性的最终判断需结合样本外表现与参数敏感性分析。

## 7. 附录：调仓记录摘要

```text
调仓次数: {len(result.trades)} 笔
最近一次调仓: {last_rebalance}
```
"""
    report_path = out / "report.md"
    report_path.write_text(report, encoding="utf-8")
    with (out / "metrics.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {k: _json_scalar(v) for k, v in metrics.items()},
            fh,
            ensure_ascii=False,
            indent=2,
        )
    return str(report_path)


def build_iteration_report(
    base: dict,
    variants: list[dict],
    best: dict,
    diagnostics: list,
    output_dir: str | Path = "reports/output/stage2",
    strategy_name: str = "策略迭代优化",
    heatmap_table: Optional[pd.DataFrame] = None,
    heatmap_path: Optional[str] = None,
) -> str:
    """生成“初始回测 -> 问题诊断 -> 自动优化 -> 对比报告”的 Markdown 文档。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    lines = [f"# {strategy_name}", ""]
    lines.append("## 1. 初始策略诊断")
    lines.append("")
    if diagnostics:
        for diag in diagnostics:
            lines.append(
                f"- **[{diag.dimension} / {diag.severity}]** {diag.issue}："
                f"{diag.suggestion}"
            )
    else:
        lines.append("- 未发现显著问题。")
    lines.append("")

    lines.append("## 2. 参数敏感性分析")
    lines.append("")
    if heatmap_path:
        lines.append(f"![参数敏感性]({_md_path(heatmap_path)})")
    else:
        lines.append("无参数敏感性数据。")
    lines.append("")

    lines.append("## 3. 方案对比")
    lines.append("")
    lines.append("| 方案 | 因子 | 参数 | top_n | 调仓 | 年化收益 | 夏普 | 最大回撤 | 年化换手 | 风控 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in [base, *variants]:
        m = item["metrics"]
        params = item.get("params", {})
        risk_summary = item.get("risk_report", None)
        risk_text = risk_summary.summary() if hasattr(risk_summary, "summary") else str(risk_summary)
        lines.append(
            f"| {item['name']} | {item.get('factor', '-')} | {params} | "
            f"{item.get('top_n', '-')} | {item.get('rebalance_freq', '-')} | "
            f"{_fmt(m.get('annual_return'))} | {_fmt(m.get('sharpe_ratio'), is_pct=False)} | "
            f"{_fmt(m.get('max_drawdown'))} | {_fmt(m.get('annual_turnover'))} | {risk_text} |"
        )
    lines.append("")

    lines.append("## 4. 最优方案")
    lines.append("")
    best_m = best["metrics"]
    lines.append(
        f"- **方案**: {best.get('name')}\n"
        f"- **因子/参数**: {best.get('factor')} {best.get('params')}\n"
        f"- **年化收益**: {_fmt(best_m.get('annual_return'))}\n"
        f"- **夏普**: {_fmt(best_m.get('sharpe_ratio'), is_pct=False)}\n"
        f"- **最大回撤**: {_fmt(best_m.get('max_drawdown'))}\n"
        f"- **年化换手**: {_fmt(best_m.get('annual_turnover'))}\n"
    )
    if best.get("report_path"):
        lines.append(f"- **最优方案研报**: {_md_path(best['report_path'])}")
    lines.append("")

    report_path = out / "iteration_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)
