"""批量因子检验：IC、IC_IR、胜率、半衰期、分层单调性与样本内外对比。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from tools import factor_tool, visual_tool


@dataclass
class FactorResearchResult:
    summary: pd.DataFrame
    ic_series: dict[str, pd.Series] = field(default_factory=dict)
    quantile_returns: dict[str, pd.DataFrame] = field(default_factory=dict)
    report_path: Optional[str] = None


def forward_returns(price_matrix: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """未来 N 日收益率：close[t+N] / close[t] - 1。"""
    if horizon <= 0:
        raise ValueError("horizon 必须为正整数")
    return price_matrix.shift(-horizon) / price_matrix - 1.0


def daily_rank_ic(factor_matrix: pd.DataFrame, forward_ret: pd.DataFrame) -> pd.Series:
    """逐日计算截面 Spearman 秩相关，即 Rank IC。"""
    index = factor_matrix.index.intersection(forward_ret.index)
    return factor_matrix.loc[index].corrwith(forward_ret.loc[index], axis=1, method="spearman")


def _ic_stats(ic_series: pd.Series) -> dict:
    ic = ic_series.dropna()
    if ic.empty:
        return {
            "ic_mean": np.nan,
            "ic_ir": np.nan,
            "ic_win_rate": np.nan,
            "half_life_days": np.nan,
        }
    mean = float(ic.mean())
    std = float(ic.std(ddof=1))
    ic_ir = float(mean / std * np.sqrt(252)) if std > 0 else np.nan
    win_rate = float((ic > 0).mean())
    rho = ic.autocorr(lag=1)
    half_life = float(-np.log(2.0) / np.log(rho)) if 0 < rho < 1 else np.nan
    return {
        "ic_mean": mean,
        "ic_ir": ic_ir,
        "ic_win_rate": win_rate,
        "half_life_days": half_life,
    }


def _assign_quantile(row: pd.Series, n_quantiles: int) -> pd.Series:
    valid = row.dropna()
    if valid.empty or valid.nunique() < n_quantiles:
        return pd.Series(np.nan, index=row.index)
    return pd.qcut(valid.rank(method="first"), q=n_quantiles, labels=False, duplicates="drop")


def quantile_return_table(
    factor_matrix: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """按因子分 5 组，计算每组未来收益的等权日频均值并返回。"""
    index = factor_matrix.index.intersection(forward_ret.index)
    factor = factor_matrix.loc[index]
    ret = forward_ret.loc[index]
    cols = factor.columns.intersection(ret.columns)
    factor, ret = factor[cols], ret[cols]

    group_table = factor.apply(lambda row: _assign_quantile(row, n_quantiles), axis=1)
    rows = []
    for date, row in ret.iterrows():
        q = group_table.loc[date]
        valid = pd.DataFrame({"ret": row, "q": q}).dropna()
        if valid.empty:
            continue
        for q_label, grp in valid.groupby("q")["ret"]:
            rows.append({"date": date, "quantile": int(q_label), "ret": float(grp.mean())})
    table = pd.DataFrame(rows).pivot(index="date", columns="quantile", values="ret")
    return table.sort_index(axis=1)


def _split_ic_stats(
    factor_matrix: pd.DataFrame,
    forward_ret: pd.DataFrame,
    sample_ratio: float,
) -> tuple[float, float]:
    ic = daily_rank_ic(factor_matrix, forward_ret).dropna()
    if len(ic) < 4:
        return np.nan, np.nan
    cutoff = max(2, int(len(ic) * sample_ratio))
    in_stats = _ic_stats(ic.iloc[:cutoff])
    out_stats = _ic_stats(ic.iloc[cutoff:])
    return in_stats["ic_mean"], out_stats["ic_mean"]


def evaluate_factors(
    panel: pd.DataFrame,
    price_matrix: pd.DataFrame,
    factor_ids: Optional[list[str]] = None,
    horizons: tuple[int, ...] = (1, 5, 20),
    n_quantiles: int = 5,
    sample_ratio: float = 0.7,
    financials: Optional[pd.DataFrame] = None,
) -> FactorResearchResult:
    """批量检验因子并返回汇总表、IC 序列与分层收益。"""
    if factor_ids is None:
        factor_ids = factor_tool.list_factors()["factor_id"].tolist()
    primary_horizon = max(horizons)
    meta = factor_tool.list_factors().set_index("factor_id")

    summary_rows = []
    ic_series: dict[str, pd.Series] = {}
    quantile_tables: dict[str, pd.DataFrame] = {}

    for factor_id in factor_ids:
        if factor_id not in meta.index:
            continue
        row_meta = meta.loc[factor_id]
        try:
            matrix = factor_tool.compute_factor_matrix(
                panel,
                factor=factor_id,
                winsorize=None,
                financials=financials,
            )
        except ValueError as exc:
            summary_rows.append(
                {
                    "factor_id": factor_id,
                    "factor_name": row_meta["name"],
                    "factor_type": row_meta["type"],
                    "skipped_reason": str(exc),
                }
            )
            continue
        matrix = matrix.reindex(index=price_matrix.index, columns=price_matrix.columns)
        if matrix.notna().sum().sum() < max(10, len(matrix) * 2):
            summary_rows.append(
                {
                    "factor_id": factor_id,
                    "factor_name": row_meta["name"],
                    "factor_type": row_meta["type"],
                    "skipped_reason": "有效样本不足",
                }
            )
            continue

        row = {
            "factor_id": factor_id,
            "factor_name": row_meta["name"],
            "factor_type": row_meta["type"],
        }
        for horizon in horizons:
            ret = forward_returns(price_matrix, horizon)
            ic = daily_rank_ic(matrix, ret)
            stats = _ic_stats(ic)
            key = f"{factor_id}_h{horizon}"
            ic_series[key] = ic
            row[f"ic_mean_h{horizon}"] = stats["ic_mean"]
            if horizon == primary_horizon:
                row.update(
                    {
                        "ic_mean": stats["ic_mean"],
                        "ic_ir": stats["ic_ir"],
                        "ic_win_rate": stats["ic_win_rate"],
                        "half_life_days": stats["half_life_days"],
                    }
                )
        ret_primary = forward_returns(price_matrix, primary_horizon)
        in_ic, out_ic = _split_ic_stats(matrix, ret_primary, sample_ratio)
        q_table = quantile_return_table(matrix, ret_primary, n_quantiles=n_quantiles)
        quantile_tables[factor_id] = q_table
        if not q_table.empty and len(q_table.columns) >= 2:
            # 使用未来收益均值而非重叠窗口累计，避免分层收益虚高
            mean_ret = q_table.mean()
            row["quantile_spread"] = float(mean_ret.iloc[-1] - mean_ret.iloc[0])
            row["monotonicity"] = float(
                pd.Series(mean_ret.values).corr(
                    pd.Series(mean_ret.index.astype(float)), method="spearman"
                )
            )
        row["in_sample_ic"] = in_ic
        row["out_sample_ic"] = out_ic
        row["skipped_reason"] = ""
        summary_rows.append(row)

    summary = pd.DataFrame(summary_rows)
    if not summary.empty and "ic_mean" in summary.columns:
        summary = summary.sort_values("ic_mean", ascending=False, na_position="last").reset_index(drop=True)
    return FactorResearchResult(
        summary=summary,
        ic_series=ic_series,
        quantile_returns=quantile_tables,
    )


def build_factor_research_report(
    result: FactorResearchResult,
    output_dir: str | Path = "reports/output/factor_research",
) -> str:
    """生成因子有效性分析 Markdown 报告并保存图表。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary = result.summary.copy()
    display_cols = [
        "factor_id",
        "factor_name",
        "factor_type",
        "ic_mean",
        "ic_ir",
        "ic_win_rate",
        "half_life_days",
        "in_sample_ic",
        "out_sample_ic",
        "monotonicity",
        "quantile_spread",
        "skipped_reason",
    ]
    summary = summary[[c for c in display_cols if c in summary.columns]]

    ic_path = visual_tool.plot_ic_series(result.ic_series, save_path=out / "ic_series.png")
    quant_path = visual_tool.plot_quantile_returns(
        result.quantile_returns, save_path=out / "quantile_returns.png"
    )

    def _fmt(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    lines = ["# 因子有效性批量检验报告", "", "## 1. 汇总排名", ""]
    lines.append("| " + " | ".join(summary.columns) + " |")
    lines.append("|" + "|".join(["---"] * len(summary.columns)) + "|")
    for _, row in summary.iterrows():
        lines.append("| " + " | ".join(_fmt(v) for v in row.values) + " |")
    lines += ["", "## 2. IC 曲线", "", f"![IC 曲线]({Path(ic_path).as_posix()})", ""]
    lines += ["## 3. 分层收益曲线", "", f"![分层收益]({Path(quant_path).as_posix()})", ""]
    lines += [
        "",
        "## 4. 说明",
        "",
        "- 主检验窗口为最大 horizon；IC_IR 按日频 IC 年化。",
        "- 样本内/样本外按时间 7:3 切分，样本外失效的因子应标记为高风险。",
        "- 半衰期由 IC 一阶自相关估算，半衰期越短说明因子衰减越快。",
        "",
    ]
    report_path = out / "factor_research.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)
