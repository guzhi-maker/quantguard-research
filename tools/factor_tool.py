"""因子计算工具。

核心约束：
- 因子严格按“时点可得”计算，财务类因子只在公告日之后生效。
- 输出统一为 date x symbol 的因子矩阵。
- 标准化统一采用截面 Z-Score，并先做 3σ 去极值。
"""

from __future__ import annotations

from typing import Dict, Optional, Union

import numpy as np
import pandas as pd

FACTOR_REGISTRY: Dict[str, dict] = {
    "momentum_20": {"name": "20日动量", "type": "technical", "params": {"window": 20}},
    "momentum_60": {"name": "60日动量", "type": "technical", "params": {"window": 60}},
    "momentum_120": {"name": "120日动量", "type": "technical", "params": {"window": 120}},
    "reversal_5": {"name": "5日反转", "type": "technical", "params": {"window": 5}},
    "volatility_20": {"name": "20日波动率", "type": "technical", "params": {"window": 20}},
    "volatility_60": {"name": "60日波动率", "type": "technical", "params": {"window": 60}},
    "turnover": {"name": "换手率", "type": "technical", "params": {}},
    "volume_ratio_20": {"name": "20日量比", "type": "technical", "params": {"window": 20}},
    "amount_ratio_20": {"name": "20日成交额比", "type": "technical", "params": {"window": 20}},
    "pe": {"name": "市盈率PE", "type": "value", "params": {}},
    "pb": {"name": "市净率PB", "type": "value", "params": {}},
    "roe": {"name": "净资产收益率ROE", "type": "quality", "params": {}},
}


def list_factors() -> pd.DataFrame:
    return pd.DataFrame(FACTOR_REGISTRY).T.reset_index().rename(columns={"index": "factor_id"})


def _close_panel(input_df: pd.DataFrame) -> pd.DataFrame:
    """从标准行情面板或 close 矩阵统一提取 date x symbol 收盘价矩阵。"""
    if isinstance(input_df.index, pd.MultiIndex):
        return input_df["close"].unstack("symbol")
    if "close" in input_df.columns:
        return input_df.pivot(index="date", columns="symbol", values="close")
    return input_df.copy()


def _field_panel(input_df: pd.DataFrame, field: str) -> pd.DataFrame:
    """从标准行情面板或宽表统一提取 date x symbol 字段矩阵。"""
    if isinstance(input_df.index, pd.MultiIndex) and field in input_df.columns:
        return input_df[field].unstack("symbol")
    if field in input_df.columns:
        return input_df.pivot(index="date", columns="symbol", values=field)
    raise ValueError(f"数据缺少字段 {field}")


def momentum(close: pd.Series, window: int = 20) -> pd.Series:
    """N 日动量 = close[t] / close[t-N] - 1。"""
    if window <= 0:
        raise ValueError("window 必须为正整数")
    return close / close.shift(window) - 1.0


def volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """N 日年化波动率 = 对数收益率滚动标准差 * sqrt(252)。"""
    if window <= 0:
        raise ValueError("window 必须为正整数")
    log_return = np.log(close / close.shift(1))
    return log_return.rolling(window, min_periods=max(5, window // 2)).std(ddof=0) * np.sqrt(252)


def cross_sectional_zscore(factor: pd.Series, winsorize: float = 3.0) -> pd.Series:
    """截面 Z-Score：先 3σ 去极值，再标准化。"""
    if winsorize <= 0:
        raise ValueError("winsorize 必须为正数")
    values = factor.dropna()
    if values.empty:
        return factor.copy()
    mean = values.mean()
    std = values.std(ddof=0)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=factor.index)
    lower, upper = mean - winsorize * std, mean + winsorize * std
    clipped = values.clip(lower, upper)
    standardized = (clipped - mean) / std
    return standardized.reindex(factor.index)


def standardize_factor_matrix(
    matrix: pd.DataFrame, winsorize: float = 3.0
) -> pd.DataFrame:
    """对 date x symbol 因子矩阵逐截面做去极值 + Z-Score。"""
    return matrix.apply(lambda row: cross_sectional_zscore(row, winsorize), axis=1)


def point_in_time_financial_factor(
    financials: pd.DataFrame,
    asof_dates: Union[pd.DatetimeIndex, pd.Series],
    field: str,
) -> pd.Series:
    """财务因子时点对齐：仅使用 ann_date <= asof 的最新公告值。"""
    if field not in financials.columns:
        raise ValueError(f"financials 缺少字段 {field}")
    fin = financials[["symbol", "ann_date", field]].dropna(subset=[field]).copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"])
    fin = fin.sort_values(["symbol", "ann_date"])
    dates = pd.DatetimeIndex(asof_dates).normalize()
    frames = []
    for symbol in fin["symbol"].unique():
        symbol_fin = fin[fin["symbol"] == symbol].drop(columns=["symbol"])
        symbol_dates = pd.DataFrame({"date": dates, "symbol": symbol})
        merged = pd.merge_asof(
            symbol_dates.sort_values("date"),
            symbol_fin.sort_values("ann_date"),
            left_on="date",
            right_on="ann_date",
            direction="backward",
            allow_exact_matches=True,
        )
        frames.append(merged)
    result = pd.concat(frames, ignore_index=True).set_index(["date", "symbol"])[field]
    return result


def compute_factor_matrix(
    panel: pd.DataFrame,
    factor: str = "momentum_20",
    params: Optional[dict] = None,
    winsorize: float = 3.0,
    financials: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """计算单因子矩阵，返回 date x symbol 的标准化因子值。"""
    if factor not in FACTOR_REGISTRY:
        raise ValueError(
            f"未知因子 {factor}，可选: {sorted(FACTOR_REGISTRY)}"
        )
    meta = FACTOR_REGISTRY[factor]
    config = {**meta["params"], **(params or {})}

    close = _close_panel(panel)
    if factor.startswith("momentum"):
        raw = close.apply(lambda col: momentum(col, int(config["window"])))
    elif factor.startswith("volatility"):
        raw = close.apply(lambda col: volatility(col, int(config["window"])))
    elif factor == "turnover":
        raw = _field_panel(panel, "turnover")
    elif factor == "reversal_5":
        raw = -close.apply(lambda col: momentum(col, int(config["window"])))
    elif factor.startswith("volume_ratio"):
        volume = _field_panel(panel, "volume")
        window = int(config["window"])
        raw = volume / volume.rolling(window, min_periods=max(5, window // 2)).mean() - 1.0
    elif factor.startswith("amount_ratio"):
        amount = _field_panel(panel, "amount")
        window = int(config["window"])
        raw = amount / amount.rolling(window, min_periods=max(5, window // 2)).mean() - 1.0
    elif factor in {"pe", "pb", "roe"}:
        if financials is None:
            raise ValueError(f"{factor} 因子需要提供 financials（含 ann_date）")
        values = point_in_time_financial_factor(financials, close.index, factor)
        raw = values.unstack("symbol").reindex(index=close.index, columns=close.columns)
        if factor in {"pe", "pb"}:
            # 估值类因子先取对数，缓解右偏
            raw = np.log(raw.replace(0, np.nan))
    else:  # pragma: no cover - registry 已限定
        raise ValueError(f"未实现的因子 {factor}")

    if winsorize is not None:
        return standardize_factor_matrix(raw, winsorize=winsorize)
    return raw
