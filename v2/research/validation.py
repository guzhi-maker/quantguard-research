"""验证与防过拟合审计：时间折、DSR、PBO、单调性检查。

设计口径见 docs/quant-research-v2-workflow.md 阶段 8 与第 6 节。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class Fold:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "start": str(pd.Timestamp(self.start).date()),
            "end": str(pd.Timestamp(self.end).date()),
        }


def walk_forward_folds(
    dates,
    n_folds: int = 3,
    train_fraction: float = 0.45,
    embargo: int = 5,
) -> list[Fold]:
    """滚动时间折：前段训练、后段按块切分验证。"""
    index = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in dates))
    if len(index) < 8:
        return []
    split = max(2, int(len(index) * train_fraction))
    remainder = index[split:]
    if len(remainder) < n_folds:
        return []
    blocks = np.array_split(np.arange(len(remainder)), n_folds)
    folds: list[Fold] = []
    for position, block in enumerate(blocks):
        start = remainder[int(block[0])]
        end = remainder[int(block[-1])]
        folds.append(Fold(start=start, end=end, label=f"fold{position + 1}"))
    return folds


def train_cutoff(dates, fold_start: pd.Timestamp, embargo: int = 5) -> pd.Timestamp | None:
    """训练集最后一日的标签结束时间必须早于 fold_start。"""
    index = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in dates))
    position = index.searchsorted(pd.Timestamp(fold_start)) - 1 - int(embargo)
    if position < 0:
        return None
    return index[position]


def effective_sample(dates, horizon: int) -> int:
    """不重叠标签的有效样本数。"""
    unique = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in dates))
    if len(unique) == 0:
        return 0
    return int(math.floor(len(unique) / max(1, horizon)))


def deflated_sharpe(
    sharpe: float,
    observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    trials: int = 1,
) -> dict:
    """Deflated Sharpe Ratio：按真实尝试次数惩罚夏普。

    trials 必须是实际尝试过的配置数量（含调参试废的那些）。
    """
    if not math.isfinite(sharpe) or observations < 3 or trials < 1:
        return {"dsr": None, "sr0": None, "prob": None}
    gamma = 0.5772156649
    e = math.e
    variance = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    variance = max(variance, 1e-9)
    sr0 = math.sqrt(variance / (observations - 1)) * (
        (1.0 - gamma) * stats.norm.ppf(1.0 - 1.0 / trials)
        + gamma * stats.norm.ppf(1.0 - 1.0 / (trials * e))
    )
    dsr = stats.norm.cdf((sharpe - sr0) * math.sqrt(observations - 1) / math.sqrt(variance))
    return {
        "dsr": round(float(dsr), 4),
        "sr0": round(float(sr0), 4),
        "prob": round(float(dsr), 4),
        "trials": int(trials),
        "observations": int(observations),
    }


def pbo(performance: pd.DataFrame, n_blocks: int = 8, samples: int = 400, seed: int = 20261104) -> dict:
    """回测过拟合概率（CSCV 近似）。

    performance: index=时间, columns=配置, 值=逐期收益。
    """
    data = performance.dropna(how="all")
    if data.shape[1] < 2 or len(data) < n_blocks * 2:
        return {"pbo": None, "samples": 0, "note": "样本或配置不足"}
    blocks = np.array_split(np.arange(len(data)), n_blocks)
    half = n_blocks // 2
    combinations = list(itertools.combinations(range(n_blocks), half))
    rng = np.random.default_rng(seed)
    if len(combinations) > samples:
        picks = rng.choice(len(combinations), size=samples, replace=False)
        combinations = [combinations[index] for index in picks]
    values = data.to_numpy(dtype=float)
    logits = []
    for combo in combinations:
        inside = np.concatenate([blocks[index] for index in combo])
        outside = np.concatenate([blocks[index] for index in range(n_blocks) if index not in combo])
        if len(inside) == 0 or len(outside) == 0:
            continue
        inside_mean = np.nanmean(values[inside], axis=0)
        outside_mean = np.nanmean(values[outside], axis=0)
        best = int(np.nanargmax(inside_mean))
        rank = stats.rankdata(outside_mean, method="average")[best]
        relative = rank / (len(outside_mean) + 1.0)
        relative = min(max(relative, 1e-6), 1 - 1e-6)
        logits.append(math.log(relative / (1.0 - relative)))
    if not logits:
        return {"pbo": None, "samples": 0}
    share = float(np.mean(np.asarray(logits) <= 0.0))
    return {"pbo": round(share, 4), "samples": len(logits)}


def monotonic(values: list[float], increasing: bool = True, tolerance: float = 1e-9) -> dict:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(clean) < 2:
        return {"monotonic": None, "values": clean}
    pairs = list(zip(clean, clean[1:]))
    ok = all(
        (later >= earlier - tolerance) if increasing else (later <= earlier + tolerance)
        for earlier, later in pairs
    )
    return {"monotonic": bool(ok), "values": [round(value, 6) for value in clean]}


def summarize_returns(returns: pd.Series, periods_per_year: int = 252) -> dict:
    """净值序列的核心指标（含下行风险）。"""
    series = returns.dropna()
    if len(series) < 3:
        return {}
    nav = (1.0 + series).cumprod()
    annual = float(nav.iloc[-1] ** (periods_per_year / len(series)) - 1.0)
    volatility = float(series.std(ddof=1) * math.sqrt(periods_per_year))
    downside = series[series < 0]
    downside_vol = float(downside.std(ddof=1) * math.sqrt(periods_per_year)) if len(downside) > 1 else 0.0
    drawdown = float((nav / nav.cummax() - 1.0).min())
    sharpe = annual / volatility if volatility > 0 else float("nan")
    sortino = annual / downside_vol if downside_vol > 0 else float("nan")
    return {
        "annual_return": round(annual, 6),
        "annual_volatility": round(volatility, 6),
        "sharpe": round(sharpe, 4) if math.isfinite(sharpe) else None,
        "sortino": round(sortino, 4) if math.isfinite(sortino) else None,
        "max_drawdown": round(drawdown, 6),
        "observations": int(len(series)),
    }
