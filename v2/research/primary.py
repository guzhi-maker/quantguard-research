"""分位数主模型：输出 P10 / P50 / P90 三条预测线。

优先使用 LightGBM 的分位数目标；未安装时退回 sklearn 的
HistGradientBoostingRegressor(loss="quantile")，接口保持一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SEED = 20261104
QUANTILES = (0.1, 0.5, 0.9)
MIN_TRAIN_ROWS = 200

try:  # pragma: no cover - 依赖存在时走这里
    import lightgbm as lgb

    LIGHTGBM_AVAILABLE = True
except Exception:  # noqa: BLE001
    lgb = None
    LIGHTGBM_AVAILABLE = False

from sklearn.ensemble import HistGradientBoostingRegressor


@dataclass
class QuantileModel:
    models: dict[float, list] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    quantiles: tuple[float, ...] = QUANTILES
    backend: str = "sklearn"
    train_rows: int = 0
    train_end: str | None = None
    seeds: int = 1

    @property
    def available(self) -> bool:
        return bool(self.models)

    def first_estimator(self):
        for estimators in self.models.values():
            if estimators:
                return estimators[0]
        return None


def _fit_one(train: pd.DataFrame, feature_names: list[str], quantile: float, seed: int) -> object:
    if LIGHTGBM_AVAILABLE:
        model = lgb.LGBMRegressor(
            objective="quantile",
            alpha=quantile,
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=max(30, MIN_TRAIN_ROWS // 4),
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=seed,
            verbose=-1,
        )
        model.fit(train[feature_names], train["label"])
        return model
    model = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=quantile,
        max_iter=200,
        learning_rate=0.06,
        max_leaf_nodes=15,
        min_samples_leaf=max(20, MIN_TRAIN_ROWS // 10),
        l2_regularization=1.0,
        random_state=seed,
    )
    model.fit(train[feature_names], train["label"])
    return model


def fit_quantiles(
    train: pd.DataFrame,
    feature_names: list[str],
    quantiles: tuple[float, ...] = QUANTILES,
    seeds: int = 1,
) -> QuantileModel:
    """在训练集上拟合每个分位点；多种子时对预测取平均以降低方差。"""
    data = train.dropna(subset=["label", *feature_names])
    if len(data) < MIN_TRAIN_ROWS:
        raise ValueError(f"训练样本不足：{len(data)} < {MIN_TRAIN_ROWS}")
    seed_count = max(1, int(seeds))
    models: dict[float, list] = {}
    for quantile in quantiles:
        models[quantile] = [
            _fit_one(data, feature_names, quantile, SEED + 17 * index)
            for index in range(seed_count)
        ]
    return QuantileModel(
        models=models,
        feature_names=list(feature_names),
        quantiles=tuple(quantiles),
        backend="lightgbm" if LIGHTGBM_AVAILABLE else "sklearn",
        train_rows=len(data),
        train_end=str(pd.Timestamp(data["date"].max()).date()),
        seeds=seed_count,
    )


def predict_quantiles(model: QuantileModel, rows: pd.DataFrame) -> pd.DataFrame:
    """预测三条线，并强制 P10 <= P50 <= P90。"""
    if not model.available:
        raise ValueError("模型尚未训练")
    if rows.empty:
        return pd.DataFrame(columns=["date", "symbol", "P10", "P50", "P90"])
    features = rows[model.feature_names]
    output = pd.DataFrame({"date": rows["date"].to_numpy(), "symbol": rows["symbol"].to_numpy()})
    for quantile, estimators in model.models.items():
        predictions = np.vstack([estimator.predict(features) for estimator in estimators])
        output[f"P{int(round(quantile * 100))}"] = predictions.mean(axis=0)
    columns = [column for column in ("P10", "P50", "P90") if column in output.columns]
    ordered = np.sort(output[columns].to_numpy(), axis=1)
    for position, column in enumerate(columns):
        output[column] = ordered[:, position]
    return output


def conformal_offsets(predictions: pd.DataFrame, lower_q: float = 0.10, upper_q: float = 0.90) -> dict:
    """分裂保形校准：用样本外残差修正区间边界，使覆盖率回到名义水平。"""
    frame = predictions.dropna(subset=["P10", "P90", "label"])
    if frame.empty:
        return {"lower": 0.0, "upper": 0.0, "samples": 0}
    lower = float(np.quantile((frame["label"] - frame["P10"]).to_numpy(dtype=float), lower_q))
    upper = float(np.quantile((frame["label"] - frame["P90"]).to_numpy(dtype=float), upper_q))
    return {"lower": lower, "upper": upper, "samples": int(len(frame))}


def apply_offsets(predictions: pd.DataFrame, offsets: dict) -> pd.DataFrame:
    """把保形偏移应用到新预测，并重新保证分位顺序。"""
    if not offsets or not offsets.get("samples"):
        return predictions
    output = predictions.copy()
    output["P10"] = output["P10"] + float(offsets.get("lower", 0.0))
    output["P90"] = output["P90"] + float(offsets.get("upper", 0.0))
    ordered = np.sort(output[["P10", "P50", "P90"]].to_numpy(), axis=1)
    for position, column in enumerate(("P10", "P50", "P90")):
        output[column] = ordered[:, position]
    return output


def cqr_offsets(
    predictions: pd.DataFrame,
    lower_alpha: float = 0.10,
    upper_alpha: float = 0.10,
) -> dict:
    """保形分位回归（CQR）：用区间宽度归一化的残差修正上下界。

    相比加性偏移，CQR 对宽区间少改、窄区间多改，能在保持覆盖率的同时
    避免把区间整体撑得过宽。
    """
    frame = predictions.dropna(subset=["P10", "P90", "label"])
    if frame.empty:
        return {"low": 0.0, "high": 0.0, "samples": 0}
    width = (frame["P90"] - frame["P10"]).clip(lower=1e-6).to_numpy(dtype=float)
    lower_score = (frame["P10"].to_numpy(dtype=float) - frame["label"].to_numpy(dtype=float)) / width
    upper_score = (frame["label"].to_numpy(dtype=float) - frame["P90"].to_numpy(dtype=float)) / width
    return {
        "low": float(np.quantile(lower_score, 1.0 - lower_alpha)),
        "high": float(np.quantile(upper_score, 1.0 - upper_alpha)),
        "samples": int(len(frame)),
    }


def apply_cqr(predictions: pd.DataFrame, offsets: dict) -> pd.DataFrame:
    """按 CQR 偏移缩放区间；不改变中位数预测。"""
    if not offsets or not offsets.get("samples"):
        return predictions
    output = predictions.copy()
    width = (output["P90"] - output["P10"]).clip(lower=1e-6)
    output["P10"] = output["P10"] - float(offsets["low"]) * width
    output["P90"] = output["P90"] + float(offsets["high"]) * width
    ordered = np.sort(output[["P10", "P50", "P90"]].to_numpy(), axis=1)
    for position, column in enumerate(("P10", "P50", "P90")):
        output[column] = ordered[:, position]
    return output


def adaptive_offsets(
    predictions: pd.DataFrame,
    alpha_low: float = 0.10,
    alpha_high: float = 0.10,
    gamma: float = 0.25,
    burn_in: float = 0.4,
) -> dict:
    """自适应保形（ACI）：按时间顺序在线更新上下界偏移。

    静态校准假设"历史与未来同分布"，遇到行情切换会失效。这里对每个交易日：
    先用当前偏移出区间，观察实际跌破率，再按 (实际 - 目标) 调整偏移。
    只使用当天及之前的信息，因此可以逐日因果评估覆盖率。
    """
    frame = predictions.dropna(subset=["P10", "P90", "label"]).sort_values("date")
    if frame.empty:
        return {"low": 0.0, "high": 0.0, "samples": 0, "increments": 0}

    dates = np.sort(frame["date"].unique())
    burn_index = max(1, int(len(dates) * burn_in))
    burn_dates = set(dates[:burn_index])
    start = cqr_offsets(frame[frame["date"].isin(burn_dates)])

    low = float(start.get("low", 0.0))
    high = float(start.get("high", 0.0))
    increments = 0
    for date in dates[burn_index:]:
        block = frame[frame["date"] == date]
        width = (block["P90"] - block["P10"]).clip(lower=1e-6)
        lower_bound = block["P10"] - low * width
        upper_bound = block["P90"] + high * width
        breach_low = float((block["label"] < lower_bound).mean())
        breach_high = float((block["label"] > upper_bound).mean())
        low += gamma * (breach_low - alpha_low)
        high += gamma * (breach_high - alpha_high)
        low = float(np.clip(low, -1.0, 3.0))
        high = float(np.clip(high, -1.0, 3.0))
        increments += 1

    return {
        "low": round(low, 6),
        "high": round(high, 6),
        "samples": int(len(frame)),
        "increments": increments,
        "burn_in": burn_in,
        "gamma": gamma,
    }


def causal_coverage(
    predictions: pd.DataFrame,
    alpha_low: float = 0.10,
    alpha_high: float = 0.10,
    gamma: float = 0.25,
    burn_in: float = 0.4,
) -> dict:
    """用自适应偏移做逐日因果覆盖率评估，只统计 burn-in 之后的日期。"""
    frame = predictions.dropna(subset=["P10", "P90", "label"]).sort_values("date")
    if frame.empty:
        return {"p10_breach": None, "p90_breach": None, "samples": 0}
    dates = np.sort(frame["date"].unique())
    burn_index = max(1, int(len(dates) * burn_in))
    burn_dates = set(dates[:burn_index])
    start = cqr_offsets(frame[frame["date"].isin(burn_dates)])
    low = float(start.get("low", 0.0))
    high = float(start.get("high", 0.0))

    breaches_low, breaches_high, widths = [], [], []
    for date in dates[burn_index:]:
        block = frame[frame["date"] == date]
        width = (block["P90"] - block["P10"]).clip(lower=1e-6)
        lower_bound = block["P10"] - low * width
        upper_bound = block["P90"] + high * width
        breach_low = float((block["label"] < lower_bound).mean())
        breach_high = float((block["label"] > upper_bound).mean())
        breaches_low.append(breach_low)
        breaches_high.append(breach_high)
        widths.append(float(width.mean()))
        low += gamma * (breach_low - alpha_low)
        high += gamma * (breach_high - alpha_high)
        low = float(np.clip(low, -1.0, 3.0))
        high = float(np.clip(high, -1.0, 3.0))

    if not breaches_low:
        return {"p10_breach": None, "p90_breach": None, "samples": 0}
    return {
        "p10_breach": round(float(np.mean(breaches_low)), 4),
        "p90_breach": round(float(np.mean(breaches_high)), 4),
        "width_mean": round(float(np.mean(widths)), 6),
        "samples": int(len(breaches_low)),
        "mode": "adaptive_causal",
    }


def nonoverlap_ic(predictions: pd.DataFrame, horizon: int, min_obs: int = 5) -> dict:
    """非重叠采样后的 Rank IC：每 horizon 个交易日取一个截面，衡量独立信息。"""
    frame = predictions.dropna(subset=["P50", "label"])
    if frame.empty:
        return {"ic_mean": None, "samples": 0}
    dates = sorted(pd.Timestamp(value) for value in frame["date"].unique())
    sampled = dates[:: max(1, int(horizon))]
    subset = frame[frame["date"].isin(sampled)]
    values = []
    for _, group in subset.groupby("date", sort=True):
        if len(group) < min_obs:
            continue
        if group["P50"].nunique() < 3 or group["label"].nunique() < 3:
            continue
        value = group["P50"].corr(group["label"], method="spearman")
        if value is not None and math.isfinite(value):
            values.append(float(value))
    if not values:
        return {"ic_mean": None, "samples": 0}
    return {
        "ic_mean": round(float(np.mean(values)), 4),
        "ic_std": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        "samples": len(values),
        "dates": len(sampled),
    }


def coverage_report(predictions: pd.DataFrame, actual: pd.Series) -> dict:
    """分位数覆盖率与交叉检查。"""
    frame = predictions.copy()
    frame["actual"] = actual.reindex(frame.index).to_numpy() if actual.index.equals(frame.index) else actual.to_numpy()
    frame = frame.dropna(subset=["P10", "P50", "P90", "actual"])
    if frame.empty:
        return {"p10_breach": None, "p90_breach": None, "cross_rate": None, "width_mean": None, "samples": 0}
    below = float((frame["actual"] < frame["P10"]).mean())
    above = float((frame["actual"] > frame["P90"]).mean())
    cross = float(((frame["P10"] > frame["P50"]) | (frame["P50"] > frame["P90"])).mean())
    width = float((frame["P90"] - frame["P10"]).mean())
    return {
        "p10_breach": round(below, 4),
        "p90_breach": round(above, 4),
        "cross_rate": round(cross, 4),
        "width_mean": round(width, 6),
        "samples": int(len(frame)),
    }


def rank_ic(predictions: pd.DataFrame, actual: pd.Series, min_obs: int = 5) -> dict:
    """预测与真实值的逐日 Rank IC。"""
    frame = predictions.copy()
    frame["actual"] = actual.to_numpy() if len(actual) == len(frame) else actual.reindex(frame.index).to_numpy()
    frame = frame.dropna(subset=["P50", "actual"])
    if frame.empty:
        return {"ic_mean": None, "ic_std": None, "ic_ir": None, "ic_win_rate": None, "samples": 0}
    values = []
    for _, group in frame.groupby("date", sort=True):
        if len(group) < min_obs:
            continue
        if group["P50"].nunique() < 3 or group["actual"].nunique() < 3:
            continue
        value = group["P50"].corr(group["actual"], method="spearman")
        if value is not None and math.isfinite(value):
            values.append(float(value))
    if not values:
        return {"ic_mean": None, "ic_std": None, "ic_ir": None, "ic_win_rate": None, "samples": 0}
    array = np.asarray(values)
    mean = float(array.mean())
    std = float(array.std(ddof=1)) if len(array) > 1 else float("nan")
    return {
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4) if math.isfinite(std) else None,
        "ic_ir": round(float(mean / std * math.sqrt(12.0)), 3) if math.isfinite(std) and std > 0 else None,
        "ic_win_rate": round(float((array > 0).mean()), 4),
        "samples": len(values),
    }


def top_series(predictions: pd.DataFrame, share: float = 0.2) -> pd.Series:
    """逐日 Top 分组相对全市场均值的超额收益序列，用于跨配置比较与 PBO。"""
    frame = predictions.dropna(subset=["P50", "label"])
    values: dict[pd.Timestamp, float] = {}
    for date, group in frame.groupby("date", sort=True):
        if len(group) < 5:
            continue
        count = max(1, int(math.ceil(len(group) * share)))
        top = group.nlargest(count, "P50")["label"]
        values[pd.Timestamp(date)] = float(top.mean() - group["label"].mean())
    return pd.Series(values, dtype=float).sort_index()


def top_bucket(predictions: pd.DataFrame, actual: pd.Series, share: float = 0.2) -> dict:
    """Top 分组超额与命中率：检验排序的决策价值。"""
    frame = predictions.copy()
    frame["actual"] = actual.to_numpy() if len(actual) == len(frame) else actual.reindex(frame.index).to_numpy()
    frame = frame.dropna(subset=["P50", "actual"])
    excess, hit = [], []
    for _, group in frame.groupby("date", sort=True):
        if len(group) < 5:
            continue
        count = max(1, int(math.ceil(len(group) * share)))
        top = group.nlargest(count, "P50")["actual"]
        mean = float(group["actual"].mean())
        excess.append(float(top.mean() - mean))
        hit.append(float((top > mean).mean()))
    if not excess:
        return {"top_excess": None, "top_capture": None, "dates": 0}
    return {
        "top_excess": round(float(np.mean(excess)), 6),
        "top_capture": round(float(np.mean(hit)), 4),
        "dates": len(excess),
    }
