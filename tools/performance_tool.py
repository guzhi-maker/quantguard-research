"""绩效统计工具：核心指标、基准对比、换手率。"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _annualize(nav: pd.Series, periods_per_year: int = 252) -> float:
    nav = nav.dropna()
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return np.nan
    n = len(nav) - 1
    if n <= 0:
        return np.nan
    return float((nav.iloc[-1] / nav.iloc[0]) ** (periods_per_year / n) - 1.0)


def _max_drawdown(nav: pd.Series) -> float:
    nav = nav.dropna()
    if nav.empty:
        return np.nan
    return float((nav / nav.cummax() - 1.0).min())


def compute_metrics(
    nav: pd.Series,
    benchmark_nav: Optional[pd.Series] = None,
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
    annual_turnover: Optional[float] = None,
) -> dict:
    """计算策略核心绩效指标。

    返回 dict，包含年化收益/波动、夏普、最大回撤、卡玛、信息比率、
    累计收益、年化换手率，以及基准的同口径指标与超额收益。
    """
    nav = nav.dropna().astype(float)
    if len(nav) < 2:
        raise ValueError("净值序列至少需要 2 个观测值")

    returns = nav.pct_change().dropna()
    n = len(returns)
    ann_return = _annualize(nav, periods_per_year)
    ann_vol = float(returns.std(ddof=1) * np.sqrt(periods_per_year)) if len(returns) > 1 else np.nan
    sharpe = (ann_return - risk_free_rate) / ann_vol if ann_vol and ann_vol > 0 else np.nan
    max_dd = _max_drawdown(nav)
    calmar = ann_return / abs(max_dd) if max_dd and max_dd != 0 else np.nan
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)

    metrics = {
        "annual_return": ann_return,
        "annual_volatility": ann_vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
        "cumulative_return": total_return,
        "annual_turnover": float(annual_turnover) if annual_turnover is not None else np.nan,
        "periods_per_year": periods_per_year,
    }

    if benchmark_nav is not None:
        bench = benchmark_nav.dropna().astype(float).reindex(nav.index).dropna()
        if len(bench) >= 2:
            bench_returns = bench.pct_change().dropna()
            bench_ann_return = _annualize(bench, periods_per_year)
            bench_max_dd = _max_drawdown(bench)
            metrics["benchmark_annual_return"] = bench_ann_return
            metrics["benchmark_max_drawdown"] = bench_max_dd
            metrics["excess_annual_return"] = ann_return - bench_ann_return

            aligned = pd.concat([returns, bench_returns], axis=1, keys=["strategy", "benchmark"]).dropna()
            if len(aligned) > 1:
                excess_daily = aligned["strategy"] - aligned["benchmark"]
                tracking_error = float(excess_daily.std(ddof=1) * np.sqrt(periods_per_year))
                metrics["info_ratio"] = (
                    float(excess_daily.mean() * periods_per_year) / tracking_error
                    if tracking_error > 0
                    else np.nan
                )
                metrics["excess_volatility"] = tracking_error
    return metrics


def compute_annual_turnover(turnover: pd.Series, dates: pd.DatetimeIndex) -> float:
    """按调仓记录估算年化换手率。"""
    turnover = turnover.dropna()
    if turnover.empty:
        return 0.0
    years = max((dates.max() - dates.min()).days / 365.25, 1 / 365.25)
    return float(turnover.sum() / years)


def metrics_table(metrics: dict) -> pd.DataFrame:
    """将指标 dict 转为易读 DataFrame。"""
    names = {
        "annual_return": "年化收益率",
        "annual_volatility": "年化波动率",
        "sharpe_ratio": "夏普比率",
        "max_drawdown": "最大回撤",
        "calmar_ratio": "卡玛比率",
        "cumulative_return": "累计收益率",
        "annual_turnover": "年化换手率",
        "benchmark_annual_return": "基准年化收益率",
        "benchmark_max_drawdown": "基准最大回撤",
        "excess_annual_return": "年化超额收益",
        "info_ratio": "信息比率",
        "excess_volatility": "超额波动率",
    }
    rows = [
        {"指标": names.get(key, key), "数值": round(value, 6) if value == value else None}
        for key, value in metrics.items()
        if key in names
    ]
    return pd.DataFrame(rows)
