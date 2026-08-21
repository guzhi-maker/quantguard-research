"""向量化选股回测引擎。

实现要点：
- 信号在调仓日收盘生成，权重从下一交易日开始生效，天然满足 T+1。
- 交易成本（佣金/印花税/过户费/滑点）在调仓日按换手率扣减。
- 输出净值曲线、权重、换手率、交易记录，供绩效与可视化工具消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

import numpy as np
import pandas as pd

from config.settings import CostConfig

FREQ_ALIASES = {
    "D": "D",
    "daily": "D",
    "日": "D",
    "W": "W",
    "weekly": "W",
    "周": "W",
    "M": "ME",
    "ME": "ME",
    "month": "ME",
    "monthly": "ME",
    "月": "ME",
}


@dataclass
class BacktestResult:
    nav: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    trades: pd.DataFrame
    rebalance_dates: list
    params: dict

    def to_dict(self) -> dict:
        return {
            "nav": self.nav,
            "returns": self.returns,
            "weights": self.weights,
            "turnover": self.turnover,
            "trades": self.trades,
            "rebalance_dates": self.rebalance_dates,
            "params": self.params,
        }


def _normalize_freq(freq: str) -> str:
    if freq not in FREQ_ALIASES:
        raise ValueError(f"不支持的调仓频率 {freq}，可选: {sorted(set(FREQ_ALIASES))}")
    return FREQ_ALIASES[freq]


def _rebalance_dates(index: pd.DatetimeIndex, freq: str) -> list[pd.Timestamp]:
    if freq == "D":
        return list(index)
    if freq == "W":
        period = index.to_period("W-SUN")
    else:  # ME
        period = index.to_period("M")
    grouped = pd.Series(index, index=index).groupby(period).last()
    return list(grouped.values)


def _target_weights(
    factor_row: pd.Series,
    price_row: pd.Series,
    market_cap_row: Optional[pd.Series],
    top_n: int,
    weight_scheme: str,
    max_single_weight: Optional[float],
    universe_row: Optional[pd.Series],
) -> pd.Series:
    """在单个调仓日生成目标权重。"""
    valid = factor_row.dropna()
    valid = valid[valid.index.isin(price_row.dropna().index)]
    if universe_row is not None:
        valid = valid[valid.index.isin(universe_row[universe_row].index)]
    if valid.empty:
        return factor_row * 0.0
    selected = valid.sort_values(ascending=False).head(top_n).index

    if weight_scheme == "equal":
        weights = pd.Series(1.0 / len(selected), index=selected)
    elif weight_scheme == "market_cap":
        if market_cap_row is None:
            raise ValueError("weight_scheme=market_cap 需要提供 market_cap_matrix")
        cap = market_cap_row.reindex(selected).dropna()
        if cap.empty or cap.sum() <= 0:
            return factor_row * 0.0
        weights = cap / cap.sum()
    else:
        raise ValueError("weight_scheme 仅支持 equal / market_cap")

    if max_single_weight is not None:
        weights = weights.clip(upper=max_single_weight)
        if weights.sum() > 0:
            weights = weights / weights.sum()
    return weights


def run_backtest(
    factor_matrix: pd.DataFrame,
    price_matrix: pd.DataFrame,
    benchmark_close: Optional[pd.Series] = None,
    top_n: int = 20,
    rebalance_freq: str = "ME",
    weight_scheme: str = "equal",
    market_cap_matrix: Optional[pd.DataFrame] = None,
    universe: Optional[pd.DataFrame] = None,
    costs: Optional[CostConfig] = None,
    max_single_weight: Optional[float] = None,
    initial_capital: float = 1_000_000.0,
) -> BacktestResult:
    """执行选股回测。

    factor_matrix / price_matrix: date x symbol，且索引与列对齐。
    benchmark_close: 与 price_matrix 同索引的基准收盘价序列。
    """
    if factor_matrix.empty or price_matrix.empty:
        raise ValueError("factor_matrix / price_matrix 不能为空")

    costs = costs or CostConfig()
    freq = _normalize_freq(rebalance_freq)
    dates = price_matrix.index.intersection(factor_matrix.index)
    if len(dates) < 2:
        raise ValueError("价格与因子矩阵共同交易日不足 2 天")
    prices = price_matrix.reindex(dates).sort_index()
    factors = factor_matrix.reindex(dates).sort_index()
    cols = prices.columns.intersection(factors.columns)
    prices = prices[cols].sort_index(axis=1)
    factors = factors[cols].sort_index(axis=1)

    if market_cap_matrix is not None:
        cap = market_cap_matrix.reindex(index=dates, columns=cols).sort_index(axis=1)
    else:
        cap = None
    if universe is not None:
        universe = universe.reindex(index=dates, columns=cols).fillna(False).astype(bool)
    else:
        universe = None

    rebalance_dates = _rebalance_dates(dates, freq)
    rebalance_dates = [d for d in rebalance_dates if d in dates]
    if not rebalance_dates:
        raise ValueError("没有可用的调仓日")

    weights = pd.DataFrame(0.0, index=dates, columns=cols)
    weights = pd.DataFrame(np.nan, index=dates, columns=cols)
    turnover = pd.Series(0.0, index=dates)
    trades_rows = []

    last_weights = pd.Series(0.0, index=cols)
    for t in rebalance_dates:
        target = _target_weights(
            factors.loc[t],
            prices.loc[t],
            cap.loc[t] if cap is not None else None,
            top_n=top_n,
            weight_scheme=weight_scheme,
            max_single_weight=max_single_weight,
            universe_row=universe.loc[t] if universe is not None else None,
        )
        new = target.reindex(cols).fillna(0.0)
        old = last_weights.reindex(cols).fillna(0.0)
        turn = float((new - old).abs().sum())
        turnover.loc[t] = turn
        weights.loc[t] = new
        last_weights = new

        diff = new - old
        for symbol, delta in diff[diff.abs() > 1e-12].items():
            trades_rows.append(
                {
                    "date": t,
                    "symbol": symbol,
                    "side": "buy" if delta > 0 else "sell",
                    "weight_delta": round(float(delta), 8),
                    "target_weight": round(float(new[symbol]), 8),
                }
            )

    # 权重在下一交易日生效：避免用 t 日收盘信号计算 t 日自身收益（未来函数）
    returns = prices.pct_change()
    # 调仓日之间的持仓保持不变
    weights = weights.ffill()
    weights = weights.ffill().fillna(0.0)
    port_return = (weights.shift(1).fillna(0.0) * returns).sum(axis=1, min_count=1)
    port_return = port_return.fillna(0.0).clip(lower=-0.5, upper=0.5)

    cost_rate = costs.round_trip_rate(sell_share=0.5)
    cost_adjust = -turnover * cost_rate
    daily_return = port_return + cost_adjust
    nav = (1.0 + daily_return).cumprod()
    nav.iloc[0] = 1.0

    trades = pd.DataFrame(trades_rows)
    params = {
        "top_n": top_n,
        "rebalance_freq": freq,
        "weight_scheme": weight_scheme,
        "cost_model": {
            "commission_rate": costs.commission_rate,
            "min_commission": costs.min_commission,
            "stamp_tax_rate": costs.stamp_tax_rate,
            "transfer_fee_rate": costs.transfer_fee_rate,
            "slippage_rate": costs.slippage_rate,
        },
        "initial_capital": initial_capital,
        "benchmark_close": benchmark_close is not None,
    }
    return BacktestResult(
        nav=nav,
        returns=daily_return,
        weights=weights,
        turnover=turnover,
        trades=trades,
        rebalance_dates=rebalance_dates,
        params=params,
    )
