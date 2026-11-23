"""组合层事后回测：把打分面板变成真实净值。

流程：按期限档的调仓频率取调仓日 -> 在调仓日用当时的可成交性与价格构建组合
-> 持有期内按买入持有漂移 -> 下一个调仓日扣交易成本并换仓。

关键纪律：调仓日只用当日及以前的信息；分数面板本身已按滚动重训生成。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.settings import CostConfig
from research import panel as panel_mod
from research import portfolio as portfolio_mod
from research import preferences as preferences_mod

SHRINK = 0.35
EXPECTED_CLIP = 0.20
RISK_FREE = 0.02


@dataclass
class BacktestResult:
    label: str
    nav: pd.Series
    benchmark_nav: pd.Series
    rebalances: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "metrics": self.metrics,
            "rebalances": self.rebalances,
        }


def _rebalance_dates(dates: pd.DatetimeIndex, frequency: str) -> list[pd.Timestamp]:
    """按 W / ME / 2ME / QE 取每个区间最后一个可用打分日。"""
    if len(dates) == 0:
        return []
    series = pd.Series(pd.DatetimeIndex(dates), index=pd.DatetimeIndex(dates))
    rules = {
        "W": lambda index: index.to_period("W-FRI"),
        "2W": lambda index: index.to_period("W-FRI"),
        "ME": lambda index: index.to_period("M"),
        "2ME": lambda index: index.to_period("M"),
        "QE": lambda index: index.to_period("Q"),
    }
    rule = rules.get(frequency, rules["ME"])
    grouped = series.groupby(rule(series.index)).last()
    picks = [pd.Timestamp(value) for value in grouped.values]
    if frequency == "2W":
        picks = picks[::2]
    elif frequency == "2ME":
        picks = picks[::2]
    return picks


def _expected_from_scores(scores: pd.DataFrame, days: int, tradable: pd.Series | None) -> pd.Series:
    """把 P50 转成月化、收缩、截断后的预期收益。"""
    frame = scores.dropna(subset=["P50"]).set_index("symbol")
    scale = 20.0 / max(1, int(days))
    raw = frame["P50"] * scale
    mean = float(raw.mean()) if len(raw) else 0.0
    expected = mean + SHRINK * (raw - mean)
    expected = expected.clip(-EXPECTED_CLIP, EXPECTED_CLIP)
    if tradable is not None:
        allowed = tradable.reindex(expected.index).fillna(False)
        expected = expected[allowed]
    return expected


def _apply_buffer(
    target: pd.Series,
    previous: pd.Series,
    scores: pd.Series,
    held_since: dict[str, pd.Timestamp],
    date: pd.Timestamp,
    days: int,
    entry_pct: float = 0.20,
    exit_pct: float = 0.50,
    min_hold_days: int | None = None,
) -> pd.Series:
    """换手缓冲：只在排名足够好时买入，跌出更宽的区间才卖出。

    这是把"信号变化"和"实际交易"解耦的标准做法，可以显著降低无谓换手。
    """
    if scores.empty:
        return target
    rank = scores.rank(pct=True, method="average")
    min_hold = int(min_hold_days if min_hold_days is not None else max(5, days // 2))

    keep = []
    for symbol, weight in previous[previous > 0].items():
        age = (date - held_since.get(str(symbol), date)).days if symbol in held_since else 0
        score_rank = float(rank.get(symbol, 1.0))
        if age < min_hold or score_rank >= exit_pct:
            keep.append(symbol)
    buys = [symbol for symbol in target.index if float(rank.get(symbol, 0.0)) >= entry_pct]

    universe = list(dict.fromkeys([*keep, *buys]))
    if not universe:
        return previous
    combined = target.reindex(universe)
    fallback = previous.reindex(universe)
    combined = combined.where(combined.notna(), fallback).fillna(0.0)
    total = float(combined.sum())
    if total <= 0:
        return previous
    return (combined / total) * float(target.sum())


def _drift_weights(weights: pd.Series, returns: pd.Series) -> pd.Series:
    """持有期内按价格漂移，不隐含每日再平衡。"""
    aligned = returns.reindex(weights.index).fillna(0.0)
    gross = weights * (1.0 + aligned)
    total = float(gross.sum()) + max(0.0, 1.0 - float(weights.sum()))
    if total <= 0:
        return weights
    return gross / total


def run_portfolio_backtest(
    scores: pd.DataFrame,
    close: pd.DataFrame,
    preference,
    tradable: pd.DataFrame | None = None,
    industries: pd.Series | None = None,
    mode: str = "optimize",
    capital: float = 1_000_000.0,
    costs: CostConfig | None = None,
    use_buffer: bool = True,
    entry_pct: float = 0.20,
    exit_pct: float = 0.50,
) -> BacktestResult:
    """在给定打分面板上做一次组合层回测。"""
    costs = costs or CostConfig()
    days = int(preference.horizon["days"])
    frequency = str(preference.horizon["rebalance"])

    panel_dates = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in scores["date"].unique()))
    trade_dates = _rebalance_dates(panel_dates, frequency)
    if not trade_dates:
        return BacktestResult(label=preference.risk["label"], nav=pd.Series(dtype=float), benchmark_nav=pd.Series(dtype=float))

    returns = close.pct_change(fill_method=None).fillna(0.0)
    timeline = close.index[(close.index >= trade_dates[0])]
    nav = [1.0]
    benchmark_nav = [1.0]
    weights = pd.Series(dtype=float)
    held_since: dict[str, pd.Timestamp] = {}
    records: list[dict] = []

    score_by_date = {pd.Timestamp(date): frame for date, frame in scores.groupby("date")}
    equal_benchmark = returns.mean(axis=1)
    trade_set = set(trade_dates)

    def _rebalance(date: pd.Timestamp, current: pd.Series) -> pd.Series:
        """在收盘构建目标持仓；成本计入当日净值，新权重从下一交易日起生效。"""
        frame = score_by_date.get(date)
        if frame is None or frame.empty:
            return current
        mask = None
        if tradable is not None and date in tradable.index:
            mask = tradable.loc[date]
        expected = _expected_from_scores(frame, days, mask)
        if expected.empty:
            return current
        prices = close.loc[date] if date in close.index else pd.Series(dtype=float)
        history_scenarios = _scenarios(returns, date)
        portfolio = portfolio_mod.build_portfolio(
            expected,
            history_scenarios,
            preference,
            mode=mode,
            tradable=mask,
            prices=prices,
            capital=capital,
            industries=industries,
            costs=costs,
            previous=current.reindex(close.columns).fillna(0.0),
            baseline=history_scenarios.mean(axis=1),
        )
        target = portfolio.weights.reindex(close.columns).fillna(0.0)
        if use_buffer:
            target = _apply_buffer(
                target,
                current.reindex(close.columns).fillna(0.0),
                frame.set_index("symbol")["P50"],
                held_since,
                date,
                days,
                entry_pct=entry_pct,
                exit_pct=exit_pct,
            )
        turnover = float((target - current.reindex(close.columns).fillna(0.0)).abs().sum())
        cost = turnover * costs.round_trip_rate()
        nav[-1] = nav[-1] * (1.0 - cost)
        for symbol in target[target > 0].index:
            held_since.setdefault(str(symbol), date)
        for symbol in list(held_since):
            if symbol not in target.index or float(target.get(symbol, 0.0)) <= 0:
                held_since.pop(symbol, None)
        records.append(
            {
                "date": str(pd.Timestamp(date).date()),
                "positions": int((target > 0).sum()),
                "gross_exposure": round(float(target.sum()), 4),
                "turnover": round(turnover, 4),
                "cost": round(cost, 6),
                "mode": mode,
            }
        )
        return target

    for index, date in enumerate(timeline):
        if index > 0:
            # 当日收益由"上一交易日收盘确定的权重"产生
            period_return = float(
                (weights * returns.loc[date].reindex(weights.index).fillna(0.0)).sum()
            )
            nav.append(nav[-1] * (1.0 + period_return))
            benchmark_nav.append(
                benchmark_nav[-1] * (1.0 + float(equal_benchmark.get(date, 0.0)))
            )
            weights = _drift_weights(weights, returns.loc[date])
        if date in trade_set:
            weights = _rebalance(pd.Timestamp(date), weights)

    nav_series = pd.Series(nav, index=timeline)
    benchmark_series = pd.Series(benchmark_nav, index=timeline)
    metrics = _metrics(nav_series, benchmark_series, records)
    return BacktestResult(
        label=preference.risk["label"],
        nav=nav_series,
        benchmark_nav=benchmark_series,
        rebalances=records,
        metrics=metrics,
    )


def _scenarios(returns: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """使用调仓日之前的历史月收益作为情景库，严禁使用未来数据。"""
    history = returns[returns.index < date]
    if history.empty:
        return pd.DataFrame(columns=returns.columns)
    return portfolio_mod.monthly_scenarios(history, window=400)


def _metrics(nav: pd.Series, benchmark: pd.Series, records: list[dict]) -> dict:
    if len(nav) < 3:
        return {"observations": int(len(nav))}
    returns = nav.pct_change().dropna()
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
    annual = float(nav.iloc[-1] ** (1.0 / years) - 1.0)
    volatility = float(returns.std(ddof=1) * math.sqrt(252))
    sharpe = (annual - RISK_FREE) / volatility if volatility > 0 else float("nan")
    drawdown = float((nav / nav.cummax() - 1.0).min())
    bench_annual = float(benchmark.iloc[-1] ** (1.0 / years) - 1.0) if len(benchmark) > 1 else float("nan")
    excess = annual - bench_annual
    turnover = sum(record["turnover"] for record in records)
    cost_total = sum(record["cost"] for record in records)
    return {
        "observations": int(len(nav)),
        "years": round(years, 2),
        "annual_return": round(annual, 6),
        "annual_volatility": round(volatility, 6),
        "sharpe": round(sharpe, 4) if math.isfinite(sharpe) else None,
        "max_drawdown": round(drawdown, 6),
        "benchmark_annual_return": round(bench_annual, 6) if math.isfinite(bench_annual) else None,
        "excess_annual_return": round(excess, 6) if math.isfinite(bench_annual) else None,
        "annual_turnover": round(turnover / years, 4),
        "cost_drag_annual": round(cost_total / years, 6),
        "rebalances": len(records),
        "avg_positions": round(float(np.mean([record["positions"] for record in records])), 2)
        if records
        else None,
    }


def run_ladder_backtest(
    scores: pd.DataFrame,
    close: pd.DataFrame,
    risk_levels: list[float],
    horizon: int,
    tradable: pd.DataFrame | None = None,
    industries: pd.Series | None = None,
    modes: tuple[str, ...] = ("optimize",),
    use_buffer: bool = True,
) -> dict:
    """对多档风险与多种组合模式做组合层回测对比。"""
    rows = []
    curves = {}
    for mode in modes:
        for risk in risk_levels:
            preference = preferences_mod.resolve(risk, horizon)
            result = run_portfolio_backtest(
                scores,
                close,
                preference,
                tradable=tradable,
                industries=industries,
                mode=mode,
                use_buffer=use_buffer,
            )
            row = {
                "mode": mode,
                "risk": risk,
                "tier": preference.risk["label"],
                "buffer": use_buffer,
                **result.metrics,
            }
            rows.append(row)
            curves[f"{mode}_R{risk:g}"] = result.nav
    table = pd.DataFrame(rows)
    return {"table": table, "curves": curves}


def buy_and_hold_benchmark(close: pd.DataFrame) -> pd.Series:
    returns = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    return (1.0 + returns).cumprod()
