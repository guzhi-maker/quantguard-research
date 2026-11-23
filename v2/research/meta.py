"""元模型：判断主模型本次预测是否可信，输出仓位乘数。

关键纪律：元模型的训练输入必须是主模型的**样本外预测**，
否则模型会学到"主模型在训练集上有多准"，样本外完全失效。

设计口径见 docs/quant-research-v2-workflow.md 阶段 4。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

SEED = 20261104
MIN_META_ROWS = 300
META_FEATURES = ("P50_rank", "P10", "P90", "spread")


@dataclass
class MetaModel:
    model: object | None = None
    feature_names: list[str] = field(default_factory=lambda: list(META_FEATURES))
    train_rows: int = 0
    status: str = "ok"
    diagnostics: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.model is not None


def build_meta_dataset(predictions: pd.DataFrame, labeled: pd.DataFrame) -> pd.DataFrame:
    """把样本外预测与真实结果拼成元模型数据集。

    predictions 必须只包含某折之外的日期（样本外），label 为真实超额收益。
    """
    required = {"date", "symbol", "P10", "P50", "P90"}
    if not required.issubset(predictions.columns):
        raise ValueError(f"预测缺少字段: {required - set(predictions.columns)}")
    frame = predictions.merge(
        labeled[["date", "symbol", "label"]], on=["date", "symbol"], how="inner"
    ).dropna(subset=["P10", "P50", "P90", "label"])
    if frame.empty:
        return frame
    rank = frame.groupby("date")["P50"].rank(pct=True, method="average")
    frame = frame.assign(
        P50_rank=rank.to_numpy(),
        spread=(frame["P90"] - frame["P10"]).to_numpy(),
        up=(frame["label"] > 0).astype(int).to_numpy(),
    )
    return frame


def fit_meta(frame: pd.DataFrame, calibration_share: float = 0.2) -> MetaModel:
    """时间切分拟合与概率校准：前段训练、后段校准。"""
    data = frame.dropna(subset=list(META_FEATURES) + ["up"])
    if len(data) < MIN_META_ROWS:
        return MetaModel(status="insufficient", diagnostics={"rows": int(len(data))})
    if data["up"].nunique() < 2:
        return MetaModel(status="single_class", diagnostics={"rows": int(len(data))})

    dates = np.sort(data["date"].unique())
    split = max(1, int(len(dates) * (1.0 - calibration_share)))
    fit_dates = set(dates[:split])
    calib_dates = set(dates[split:])
    fit_rows = data[data["date"].isin(fit_dates)]
    calib_rows = data[data["date"].isin(calib_dates)]
    if len(fit_rows) < MIN_META_ROWS or len(calib_rows) < 50:
        return MetaModel(status="insufficient", diagnostics={"rows": int(len(data))})

    base = RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        min_samples_leaf=max(20, len(fit_rows) // 400),
        class_weight="balanced",
        random_state=SEED,
        n_jobs=1,
    )
    base.fit(fit_rows[list(META_FEATURES)], fit_rows["up"])
    try:
        calibrated = CalibratedClassifierCV(base, method="isotonic", cv="prefit")
        calibrated.fit(calib_rows[list(META_FEATURES)], calib_rows["up"])
        model = calibrated
    except Exception:  # noqa: BLE001 - 校准失败时退回原始概率
        model = base

    try:
        auc = float(roc_auc_score(calib_rows["up"], model.predict_proba(calib_rows[list(META_FEATURES)])[:, 1]))
    except ValueError:
        auc = float("nan")

    return MetaModel(
        model=model,
        train_rows=len(fit_rows),
        status="ok",
        diagnostics={
            "rows": int(len(data)),
            "fit_rows": int(len(fit_rows)),
            "calibration_rows": int(len(calib_rows)),
            "calibration_auc": round(auc, 4) if math.isfinite(auc) else None,
        },
    )


def meta_probability(model: MetaModel, rows: pd.DataFrame) -> pd.Series:
    if not model.available:
        return pd.Series(0.5, index=rows.index)
    frame = rows.dropna(subset=list(META_FEATURES))
    if frame.empty:
        return pd.Series(0.5, index=rows.index)
    prob = model.model.predict_proba(frame[list(META_FEATURES)])[:, 1]
    return pd.Series(prob, index=frame.index).reindex(rows.index).fillna(0.5)


def multiplier(probability: pd.Series, preference) -> pd.Series:
    """可信度 -> 仓位乘数。

    保守档允许归零（相当于软性排除）；激进档保底 0.6，避免组合被清空。
    """
    values = probability.clip(0.0, 1.0)
    tier = preference.risk["tier_index"] if "tier_index" in preference.risk else 2
    if tier <= 1:
        return values.apply(lambda value: 0.0 if value < 0.45 else min(1.0, max(0.15, (value - 0.45) / 0.30)))
    if tier == 2:
        return values.apply(lambda value: 0.0 if value < 0.38 else min(1.0, max(0.25, (value - 0.38) / 0.45)))
    if tier == 3:
        return values.clip(lower=0.35, upper=1.0)
    return values.clip(lower=0.60, upper=1.0)


def apply_multiplier(expected: pd.Series, probability: pd.Series, preference) -> pd.Series:
    """把乘数作用在预期收益上：既降权也降预期。"""
    factors = multiplier(probability.reindex(expected.index).fillna(0.5), preference)
    return expected * factors


def evaluate_meta(probability: pd.Series, actual_up: pd.Series) -> dict:
    frame = pd.DataFrame({"p": probability, "up": actual_up}).dropna()
    if frame.empty or frame["up"].nunique() < 2:
        return {"auc": None, "precision": None, "coverage": None, "samples": 0}
    try:
        auc = float(roc_auc_score(frame["up"], frame["p"]))
    except ValueError:
        auc = float("nan")
    threshold = 0.5
    predicted = frame["p"] >= threshold
    precision = float((frame.loc[predicted, "up"]).mean()) if predicted.any() else None
    return {
        "auc": round(auc, 4) if math.isfinite(auc) else None,
        "precision": round(precision, 4) if precision is not None else None,
        "coverage": round(float(predicted.mean()), 4),
        "samples": int(len(frame)),
    }
