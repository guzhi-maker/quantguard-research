"""滚动打分面板：定期重训并只对训练截止日之后的日期出预测。

与 pipeline 里的三折验证互补：
- 三折验证用于报告 IC / 覆盖率；
- 本模块产出连续的日频分数面板，供组合层事后回测使用。

防泄漏纪律：每次重训只用 label_end_date <= 信号日 - embargo 的样本。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research import primary as primary_mod
from research import validation as validation_mod


def rolling_scores(
    dataset: pd.DataFrame,
    feature_names: list[str],
    refit_every: int = 20,
    embargo: int = 5,
    seeds: int = 1,
    min_train: int = primary_mod.MIN_TRAIN_ROWS,
    max_refits: int | None = None,
    train_window: int | None = None,
    predict_period: str | None = None,
) -> pd.DataFrame:
    """返回 date, symbol, P10, P50, P90 的日频面板。"""
    data = dataset.dropna(subset=feature_names + ["label"]).copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values(["date", "symbol"])
    if data.empty:
        return pd.DataFrame(columns=["date", "symbol", "P10", "P50", "P90"])

    dates = np.sort(data["date"].unique())
    groups = {pd.Timestamp(key): frame for key, frame in data.groupby("date")}
    predict_dates: set[pd.Timestamp] | None = None
    if predict_period:
        index = pd.DatetimeIndex(pd.to_datetime(dates))
        if predict_period == "M":
            picked = pd.Series(index, index=index).groupby(index.to_period("M")).last()
        elif predict_period == "W":
            picked = pd.Series(index, index=index).groupby(index.to_period("W-FRI")).last()
        else:
            picked = pd.Series(index, index=index)
        predict_dates = {pd.Timestamp(value) for value in picked.values}

    frames: list[pd.DataFrame] = []
    model = None
    refits = 0

    for position, date in enumerate(dates):
        if model is None or position % max(1, int(refit_every)) == 0:
            if max_refits is not None and refits >= max_refits:
                break
            cutoff = validation_mod.train_cutoff(dates, date, embargo)
            if cutoff is None:
                continue
            train = data[data["label_end_date"] <= cutoff]
            if train_window:
                date_index = pd.DatetimeIndex(pd.to_datetime(dates))
                cutoff_pos = int(date_index.searchsorted(pd.Timestamp(cutoff)))
                start_pos = max(0, cutoff_pos - int(train_window))
                window_start = date_index[start_pos]
                train = train[train["date"] >= window_start]
            if len(train) < min_train:
                continue
            try:
                model = primary_mod.fit_quantiles(train, feature_names, seeds=seeds)
            except ValueError:
                continue
            refits += 1
        if model is None:
            continue
        if predict_dates is not None and pd.Timestamp(date) not in predict_dates:
            continue
        rows = groups.get(pd.Timestamp(date))
        if rows is None or rows.empty:
            continue
        predictions = primary_mod.predict_quantiles(model, rows)
        predictions["refit"] = refits
        frames.append(predictions)

    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "P10", "P50", "P90"])
    panel = pd.concat(frames, ignore_index=True)
    return panel.sort_values(["date", "symbol"]).reset_index(drop=True)


def _cutoff(dates, signal_date, embargo: int) -> pd.Timestamp | None:
    """保留旧接口，内部直接复用验证模块的截止日逻辑。"""
    return validation_mod.train_cutoff(dates, signal_date, embargo)


def score_coverage(panel: pd.DataFrame) -> dict:
    if panel.empty:
        return {"dates": 0, "rows": 0}
    return {
        "dates": int(panel["date"].nunique()),
        "rows": int(len(panel)),
        "start": str(pd.Timestamp(panel["date"].min()).date()),
        "end": str(pd.Timestamp(panel["date"].max()).date()),
        "refits": int(panel["refit"].max()) if "refit" in panel else None,
    }
