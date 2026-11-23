"""标签生成与防泄漏纪律。

标签 = 未来 N 日行业中性超额收益；每行记录"标签结束日"，
训练集只允许使用 label_end_date <= signal_date - embargo 的样本。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_EMBARGO = 5


def forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """未来 N 日收益：close[t+N] / close[t] - 1。"""
    if horizon <= 0:
        raise ValueError("horizon 必须为正整数")
    return close.shift(-horizon) / close - 1.0


def industry_median(
    frame: pd.DataFrame,
    industry: pd.Series,
    min_group: int = 8,
) -> pd.DataFrame:
    """逐日取行业中位数；行业成员太少时退回全市场中位数。

    行业只有 4-5 只股票时，中位数等于 2-3 只的平均，噪声极大，
    会把标签本身变成噪音；因此小组行业统一用市场基准。
    """
    aligned = industry.reindex(frame.columns).fillna("未分类")
    grouped = frame.T.groupby(aligned).transform("median").T
    grouped = grouped.reindex(index=frame.index, columns=frame.columns)
    sizes = aligned.value_counts()
    small = [column for column in frame.columns if sizes.get(aligned.get(column), 0) < min_group]
    if small:
        market = frame.median(axis=1)
        for column in small:
            grouped[column] = market
    return grouped


def market_median(frame: pd.DataFrame) -> pd.DataFrame:
    """全市场截面中位数，返回与 frame 同形。"""
    market = frame.median(axis=1)
    values = np.repeat(market.to_numpy()[:, None], frame.shape[1], axis=1)
    return pd.DataFrame(values, index=frame.index, columns=frame.columns)


def excess_returns(
    close: pd.DataFrame,
    industry: pd.Series,
    horizon: int,
    label_baseline: str = "industry",
    min_group: int = 8,
) -> pd.DataFrame:
    """超额标签：未来收益减去基准（行业中位数或全市场中位数）。"""
    forward = forward_returns(close, horizon)
    if label_baseline == "market":
        baseline = market_median(forward)
    else:
        baseline = industry_median(forward, industry, min_group=min_group)
    excess = forward - baseline
    return excess.where(forward.notna() & baseline.notna())


def label_end_dates(dates: pd.DatetimeIndex, horizon: int) -> pd.Series:
    """每个信号日对应的标签结束日。"""
    index = pd.DatetimeIndex(dates)
    future = pd.Series(index=index, dtype="datetime64[ns]")
    positions = {date: position for position, date in enumerate(index)}
    for position, date in enumerate(index):
        target = position + horizon
        if target < len(index):
            future.loc[date] = index[target]
    return future


def training_mask(
    dates: pd.DatetimeIndex,
    signal_date: pd.Timestamp,
    embargo: int = DEFAULT_EMBARGO,
) -> pd.Series:
    """信号日可用的训练日期：标签结束日必须早于 signal_date - embargo。"""
    index = pd.DatetimeIndex(dates)
    signal = pd.Timestamp(signal_date)
    cutoff_position = index.searchsorted(signal) - 1 - int(embargo)
    allowed = pd.Series(False, index=index)
    if cutoff_position >= 0:
        allowed.iloc[: cutoff_position + 1] = True
    return allowed


def transform_labels(
    labels: pd.DataFrame,
    mode: str = "none",
    clip: float = 0.01,
) -> pd.DataFrame:
    """标签变换：缩尾或截面分位化。

    - none：原始行业中性超额；
    - winsor：逐日按 1%/99% 缩尾，抑制极端值；
    - rank：逐日转为 [-0.5, 0.5] 的截面分位，直接优化排序。
    """
    if mode in (None, "none"):
        return labels
    if mode == "winsor":
        lower = labels.quantile(clip, axis=1)
        upper = labels.quantile(1.0 - clip, axis=1)
        clipped = labels.clip(lower=lower, upper=upper, axis=0)
        return clipped
    if mode == "rank":
        ranks = labels.rank(axis=1, pct=True) - 0.5
        # 保留排序信息，同时按截面离散度还原量纲，避免优化器把 0.5 当成 50% 收益
        dispersion = labels.std(axis=1)
        return ranks.mul(dispersion, axis=0) * 2.0
    raise ValueError(f"未知标签变换: {mode}")


def build_label_panel(
    close: pd.DataFrame,
    industry: pd.Series,
    horizon: int,
    transform: str = "none",
    label_baseline: str = "industry",
) -> tuple[pd.DataFrame, pd.Series]:
    """返回 (超额收益矩阵, 每个日期对应的标签结束日)。"""
    labels = excess_returns(close, industry, horizon, label_baseline=label_baseline)
    labels = transform_labels(labels, transform)
    ends = label_end_dates(close.index, horizon)
    return labels, ends


def to_long(
    labels: pd.DataFrame,
    feature_frames: dict[str, pd.DataFrame],
    end_dates: pd.Series,
    feature_names: list[str] | None = None,
) -> pd.DataFrame:
    """把标签与特征拼成长表：date, symbol, 各特征, label, label_end_date。"""
    names = feature_names or list(feature_frames)
    stacked = labels.stack(future_stack=True).rename("label")
    frame = stacked.to_frame()
    for name in names:
        frame[name] = feature_frames[name].stack(future_stack=True)
    frame = frame.reset_index()
    frame.columns = ["date", "symbol", "label", *names]
    frame["label_end_date"] = frame["date"].map(end_dates)
    frame = frame.dropna(subset=["label", "label_end_date"])
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)
