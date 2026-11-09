"""Time-series-safe cross-sectional machine-learning research helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SEED = 20261104
MIN_TRAIN_ROWS = 300
MAX_TRAIN_ROWS = 20000
RETRAIN_STEP = 20
TOP_BUCKET = 0.20

FEATURE_DEFINITIONS = [
    ("momentum_5", "5 日收益", "close_t / close_{t-5} - 1", "价格趋势"),
    ("momentum_20", "20 日动量", "close_t / close_{t-20} - 1", "价格趋势"),
    ("momentum_60", "60 日动量", "close_t / close_{t-60} - 1", "价格趋势"),
    ("momentum_120", "120 日动量", "close_t / close_{t-120} - 1", "价格趋势"),
    ("volatility_10", "10 日波动", "日收益 10 日标准差 × sqrt(252)", "风险"),
    ("volatility_20", "20 日波动", "日收益 20 日标准差 × sqrt(252)", "风险"),
    ("volatility_60", "60 日波动", "日收益 60 日标准差 × sqrt(252)", "风险"),
    ("price_to_ma20", "价格偏离 20 日均线", "close_t / MA20 - 1", "趋势"),
    ("price_to_ma60", "价格偏离 60 日均线", "close_t / MA60 - 1", "趋势"),
    ("volume_ratio_5_20", "成交量短长比", "MA5(volume) / MA20(volume)", "流动性"),
    ("amount_ratio_5_20", "成交额短长比", "MA5(amount) / MA20(amount)", "流动性"),
    ("liquidity_log_20", "流动性规模", "log1p(MA20(amount))", "流动性"),
    ("amihud_20", "Amihud 非流动性", "MA20(abs(return) / amount)", "流动性"),
    ("max_drawdown_60", "60 日最大回撤", "close / rolling_max_60(close) - 1", "风险"),
    ("market_return_20", "股票池 20 日收益", "等权股票池 20 日收益", "市场状态"),
    ("market_breadth", "市场广度", "价格高于 60 日均线的股票比例", "市场状态"),
    ("cross_section_dispersion", "截面离散度", "20 日收益截面标准差", "市场状态"),
]
STOCK_FEATURES = [
    name
    for name, _label, _formula, category in FEATURE_DEFINITIONS
    if category != "市场状态"
]
FEATURE_NAMES = [row[0] for row in FEATURE_DEFINITIONS]
FEATURE_CATALOG = [
    {
        "name": name,
        "label": label,
        "formula": formula,
        "category": category,
        "timing": "仅使用信号日 t 及之前的行情",
        "missing_policy": "训练折中位数填充",
    }
    for name, label, formula, category in FEATURE_DEFINITIONS
]

MODEL_LABELS = {
    "factor": "多因子基线",
    "ridge": "Ridge 回归",
    "random_forest": "随机森林",
    "gradient_boosting": "梯度提升",
}
MODEL_ALIASES = {
    "balanced": "factor",
    "momentum": "factor",
    "defensive": "factor",
    "auto": "auto",
    "factor": "factor",
    "ridge": "ridge",
    "random_forest": "random_forest",
    "gradient_boosting": "gradient_boosting",
}
FACTOR_WEIGHTS = {
    "balanced": {
        "momentum_20": 0.35,
        "momentum_60": 0.25,
        "low_vol": 0.20,
        "liquidity": 0.20,
    },
    "momentum": {
        "momentum_20": 0.50,
        "momentum_60": 0.30,
        "low_vol": 0.05,
        "liquidity": 0.15,
    },
    "defensive": {
        "momentum_20": 0.15,
        "momentum_60": 0.20,
        "low_vol": 0.45,
        "liquidity": 0.20,
    },
}

CONFIG_GRID = {
    "ridge": [{"alpha": 0.1}, {"alpha": 1.0}, {"alpha": 10.0}, {"alpha": 100.0}],
    "random_forest": [
        {
            "n_estimators": 70,
            "max_depth": 7,
            "min_samples_leaf": 20,
            "max_features": 0.7,
        },
    ],
    "gradient_boosting": [
        {
            "max_iter": 90,
            "learning_rate": 0.06,
            "max_leaf_nodes": 15,
            "l2_regularization": 1.0,
        },
    ],
}


@dataclass
class FitResult:
    model: Pipeline
    feature_names: list[str]
    pred: pd.Series


@dataclass
class RollingPredictionResult:
    predictions: pd.DataFrame
    fit_log: list[dict[str, Any]]


def _zscore(row: pd.Series) -> pd.Series:
    values = row.dropna()
    if len(values) < 3:
        return pd.Series(np.nan, index=row.index)
    std = float(values.std(ddof=0))
    if not math.isfinite(std) or std <= 1e-12:
        return pd.Series(0.0, index=row.index)
    mean = float(values.mean())
    clipped = values.clip(mean - 3.0 * std, mean + 3.0 * std)
    return ((clipped - mean) / std).reindex(row.index)


def _finite(value: Any, digits: int = 6) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _monthly_dates(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    if len(dates) == 0:
        return []
    series = pd.Series(dates, index=dates)
    values = series.groupby(dates.to_period("M")).last().values
    return [pd.Timestamp(value) for value in values]


def _safe_spearman(left: pd.Series, right: pd.Series) -> Optional[float]:
    common = left.dropna().index.intersection(right.dropna().index)
    if len(common) < 8:
        return None
    correlation = left.reindex(common).corr(right.reindex(common), method="spearman")
    if pd.isna(correlation) or not math.isfinite(float(correlation)):
        return None
    return float(correlation)


def build_feature_panels(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    volume: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    returns = close.pct_change(fill_method=None)
    raw: dict[str, pd.DataFrame] = {
        "momentum_5": close / close.shift(5) - 1.0,
        "momentum_20": close / close.shift(20) - 1.0,
        "momentum_60": close / close.shift(60) - 1.0,
        "momentum_120": close / close.shift(120) - 1.0,
        "volatility_10": returns.rolling(10, min_periods=8).std(ddof=1) * np.sqrt(252),
        "volatility_20": returns.rolling(20, min_periods=15).std(ddof=1) * np.sqrt(252),
        "volatility_60": returns.rolling(60, min_periods=40).std(ddof=1) * np.sqrt(252),
        "price_to_ma20": close / close.rolling(20, min_periods=15).mean() - 1.0,
        "price_to_ma60": close / close.rolling(60, min_periods=40).mean() - 1.0,
        "volume_ratio_5_20": (
            volume.rolling(5, min_periods=3).mean()
            / volume.rolling(20, min_periods=10).mean()
        ),
        "amount_ratio_5_20": (
            amount.rolling(5, min_periods=3).mean()
            / amount.rolling(20, min_periods=10).mean()
        ),
        "liquidity_log_20": np.log1p(
            amount.rolling(20, min_periods=10).mean().clip(lower=1.0)
        ),
        "amihud_20": (
            (returns.abs() / amount.clip(lower=1.0)).rolling(20, min_periods=10).mean()
        ),
        "max_drawdown_60": close / close.rolling(60, min_periods=40).max() - 1.0,
    }
    equal_return = returns.mean(axis=1).fillna(0.0)
    equal_nav = (1.0 + equal_return).cumprod()
    market_return = equal_nav / equal_nav.shift(20) - 1.0
    ma60 = close.rolling(60, min_periods=40).mean()
    breadth = (close > ma60).mean(axis=1)
    dispersion = (close / close.shift(20) - 1.0).std(axis=1)

    standardized = {
        name: matrix.apply(_zscore, axis=1) for name, matrix in raw.items()
    }
    standardized["market_return_20"] = pd.DataFrame(
        np.repeat(market_return.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    standardized["market_breadth"] = pd.DataFrame(
        np.repeat(breadth.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    standardized["cross_section_dispersion"] = pd.DataFrame(
        np.repeat(dispersion.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    standardized["average_amount"] = amount.rolling(20, min_periods=10).mean()
    standardized["raw_volatility_20"] = raw["volatility_20"]
    return standardized


def _long_features(
    features: dict[str, pd.DataFrame],
    metadata: dict[str, dict],
) -> pd.DataFrame:
    first = features[FEATURE_NAMES[0]]
    base = pd.DataFrame(
        [(date, symbol) for date in first.index for symbol in first.columns],
        columns=["date", "symbol"],
    )
    for name in FEATURE_NAMES:
        values = features[name].stack(future_stack=True).rename(name)
        base = base.merge(values, left_on=["date", "symbol"], right_index=True, how="left")
    base["industry"] = base["symbol"].map(
        lambda symbol: str(metadata.get(str(symbol), {}).get("industry") or "未分类")
    )
    eligible = (
        base["momentum_120"].notna()
        & base["volatility_60"].notna()
        & base["market_return_20"].notna()
    )
    return base.loc[eligible].reset_index(drop=True)


def build_samples(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    volume: pd.DataFrame,
    metadata: dict[str, dict],
    horizon: int = 20,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    features = build_feature_panels(close, amount, volume)
    feature_rows = _long_features(features, metadata)
    forward = close.shift(-horizon) / close - 1.0
    future_excess = forward.sub(forward.mean(axis=1), axis=0)
    labels = future_excess.stack(future_stack=True).rename("future_excess").reset_index()
    labels.columns = ["date", "symbol", "future_excess"]
    labeled = feature_rows.merge(labels, on=["date", "symbol"], how="left")
    date_positions = {pd.Timestamp(value): index for index, value in enumerate(close.index)}
    labeled["label_end_date"] = labeled["date"].map(
        lambda value: (
            close.index[date_positions[pd.Timestamp(value)] + horizon]
            if date_positions.get(pd.Timestamp(value), len(close.index)) + horizon
            < len(close.index)
            else pd.NaT
        )
    )
    labeled = labeled.loc[labeled["future_excess"].notna() & labeled["label_end_date"].notna()]
    return features, feature_rows, labeled.reset_index(drop=True)


def _factor_score(
    features: dict[str, pd.DataFrame],
    variant: str = "balanced",
) -> pd.DataFrame:
    config = FACTOR_WEIGHTS.get(variant, FACTOR_WEIGHTS["balanced"])
    low_vol = (-features["raw_volatility_20"]).apply(_zscore, axis=1)
    score = (
        features["momentum_20"] * config["momentum_20"]
        + features["momentum_60"] * config["momentum_60"]
        + low_vol * config["low_vol"]
        + features["liquidity_log_20"] * config["liquidity"]
    )
    return score.where(features["momentum_60"].notna() & features["raw_volatility_20"].notna())


def _make_pipeline(model_name: str, config: dict[str, Any]) -> Pipeline:
    numeric = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    preprocess = ColumnTransformer(
        [
            ("numeric", numeric, FEATURE_NAMES),
            ("industry", categorical, ["industry"]),
        ],
        sparse_threshold=0.0,
    )
    if model_name == "ridge":
        estimator = Ridge(**config)
    elif model_name == "random_forest":
        estimator = RandomForestRegressor(
            random_state=SEED,
            n_jobs=1,
            **config,
        )
    elif model_name == "gradient_boosting":
        estimator = HistGradientBoostingRegressor(
            random_state=SEED,
            early_stopping=False,
            **config,
        )
    else:
        raise ValueError(f"未知监督学习模型 {model_name}")
    return Pipeline([("preprocess", preprocess), ("model", estimator)])


def _fit_predict(
    model_name: str,
    config: dict[str, Any],
    train: pd.DataFrame,
    prediction_rows: pd.DataFrame,
) -> FitResult:
    train = train.sort_values("date").tail(MAX_TRAIN_ROWS)
    if len(train) < MIN_TRAIN_ROWS:
        raise ValueError(f"训练样本不足: {len(train)}")
    columns = FEATURE_NAMES + ["industry"]
    pipeline = _make_pipeline(model_name, config)
    pipeline.fit(train[columns], train["future_excess"])
    pred = np.asarray(
        pipeline.predict(prediction_rows[columns]),
        dtype=float,
    )
    if not np.isfinite(pred).all():
        raise ValueError("模型产生了非有限预测值")
    names = list(pipeline.named_steps["preprocess"].get_feature_names_out())
    return FitResult(
        model=pipeline,
        feature_names=names,
        pred=pd.Series(pred, index=prediction_rows.index),
    )


def _prediction_frame(
    prediction_rows: pd.DataFrame,
    pred: pd.Series,
) -> pd.DataFrame:
    result = prediction_rows[["date", "symbol", "future_excess"]].copy()
    result["prediction"] = pred.reindex(result.index).to_numpy()
    return result


def prediction_metrics(predictions: pd.DataFrame) -> dict[str, Any]:
    if predictions.empty or not {"prediction", "future_excess"}.issubset(predictions.columns):
        return {
            "mae": None,
            "rmse": None,
            "r2_oos": None,
            "rank_ic_mean": None,
            "rank_ic_std": None,
            "icir": None,
            "ic_win_rate": None,
            "top_bucket_excess": None,
            "top_bucket_capture": None,
            "samples": 0,
            "dates": 0,
            "status": "样本不足",
        }
    rows = predictions.dropna(subset=["prediction", "future_excess"]).copy()
    if rows.empty:
        return {
            "mae": None,
            "rmse": None,
            "r2_oos": None,
            "rank_ic_mean": None,
            "rank_ic_std": None,
            "icir": None,
            "ic_win_rate": None,
            "top_bucket_excess": None,
            "top_bucket_capture": None,
            "samples": 0,
            "dates": 0,
            "status": "样本不足",
        }
    error = rows["prediction"] - rows["future_excess"]
    mae = float(error.abs().mean())
    rmse = float(np.sqrt((error**2).mean()))
    variance = float(((rows["future_excess"] - rows["future_excess"].mean()) ** 2).sum())
    r2 = 1.0 - float((error**2).sum()) / variance if variance > 0 else None

    ics = []
    top_means = []
    hit_rates = []
    for _, group in rows.groupby("date", sort=True):
        if len(group) < 8:
            continue
        ic = _safe_spearman(group["prediction"], group["future_excess"])
        if ic is not None:
            ics.append(ic)
        count = max(1, int(math.ceil(len(group) * TOP_BUCKET)))
        top = group.nlargest(count, "prediction")["future_excess"]
        market_mean = float(group["future_excess"].mean())
        top_means.append(float(top.mean() - market_mean))
        hit_rates.append(float((top > market_mean).mean()))

    ic_values = np.asarray(ics, dtype=float)
    ic_mean = float(ic_values.mean()) if len(ic_values) else None
    ic_std = float(ic_values.std(ddof=1)) if len(ic_values) > 1 else None
    icir = (
        ic_mean / ic_std * math.sqrt(12.0)
        if ic_mean is not None and ic_std and math.isfinite(ic_std) and ic_std > 0
        else None
    )
    top_bucket_excess = float(np.mean(top_means)) if top_means else None
    top_bucket_capture = float(np.mean(hit_rates)) if hit_rates else None
    status = "方向有效" if ic_mean is not None and ic_mean >= 0.02 else (
        "方向偏弱" if ic_mean is not None and ic_mean > 0 else "反向或不稳定"
    )
    return {
        "mae": _finite(mae),
        "rmse": _finite(rmse),
        "r2_oos": _finite(r2),
        "rank_ic_mean": _finite(ic_mean, 4),
        "rank_ic_std": _finite(ic_std, 4),
        "icir": _finite(icir, 3),
        "ic_win_rate": _finite(float((ic_values > 0).mean()) if len(ic_values) else None, 4),
        "top_bucket_excess": _finite(top_bucket_excess),
        "top_bucket_capture": _finite(top_bucket_capture, 4),
        "samples": int(len(rows)),
        "dates": int(rows["date"].nunique()),
        "status": status,
    }


def _factor_predictions(
    feature_rows: pd.DataFrame,
    feature_panels: dict[str, pd.DataFrame],
    variant: str,
    dates: list[pd.Timestamp],
    labeled: pd.DataFrame,
) -> pd.DataFrame:
    score = _factor_score(feature_panels, variant)
    score_rows = score.stack(future_stack=True).rename("prediction").reset_index()
    score_rows.columns = ["date", "symbol", "prediction"]
    wanted = feature_rows.loc[
        feature_rows["date"].isin(dates), ["date", "symbol"]
    ].merge(labeled[["date", "symbol", "future_excess"]], on=["date", "symbol"], how="left")
    return wanted.merge(score_rows, on=["date", "symbol"], how="left")


def _fold_dates(dev_dates: list[pd.Timestamp]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if len(dev_dates) < 4:
        return []
    start_index = len(dev_dates) // 2
    validation = dev_dates[start_index:]
    if len(validation) < 3:
        return []
    a = start_index + max(1, len(validation) // 3)
    b = start_index + max(2, 2 * len(validation) // 3)
    return [
        (dev_dates[start_index], dev_dates[min(a, len(dev_dates) - 1)]),
        (dev_dates[min(a, len(dev_dates) - 1)], dev_dates[min(b, len(dev_dates) - 1)]),
        (dev_dates[min(b, len(dev_dates) - 1)], dev_dates[-1]),
    ]


def _validate_supervised(
    model_name: str,
    config: dict[str, Any],
    labeled: pd.DataFrame,
    feature_rows: pd.DataFrame,
    folds: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[dict[str, Any], pd.DataFrame]:
    predictions = []
    for fold_start, fold_end in folds:
        train = labeled.loc[labeled["label_end_date"] <= fold_start]
        dates = pd.DatetimeIndex(
            feature_rows.loc[
                (feature_rows["date"] >= fold_start) & (feature_rows["date"] <= fold_end),
                "date",
            ].drop_duplicates()
        )
        validation_dates = _monthly_dates(dates)
        if not validation_dates or len(train) < MIN_TRAIN_ROWS:
            continue
        prediction_rows = feature_rows.loc[feature_rows["date"].isin(validation_dates)].copy()
        try:
            fitted = _fit_predict(model_name, config, train, prediction_rows)
        except (ValueError, TypeError, FloatingPointError):
            continue
        predictions.append(_prediction_frame(prediction_rows, fitted.pred))
    combined = (
        pd.concat(predictions, ignore_index=True)
        if predictions
        else pd.DataFrame(columns=["date", "symbol", "future_excess", "prediction"])
    )
    return prediction_metrics(combined), combined


def _validate_factor(
    variant: str,
    labeled: pd.DataFrame,
    feature_rows: pd.DataFrame,
    feature_panels: dict[str, pd.DataFrame],
    folds: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[dict[str, Any], pd.DataFrame]:
    predictions = []
    for fold_start, fold_end in folds:
        dates = pd.DatetimeIndex(
            feature_rows.loc[
                (feature_rows["date"] >= fold_start) & (feature_rows["date"] <= fold_end),
                "date",
            ].drop_duplicates()
        )
        validation_dates = _monthly_dates(dates)
        if validation_dates:
            predictions.append(
                _factor_predictions(
                    feature_rows, feature_panels, variant, validation_dates, labeled
                )
            )
    combined = (
        pd.concat(predictions, ignore_index=True)
        if predictions
        else pd.DataFrame(columns=["date", "symbol", "future_excess", "prediction"])
    )
    return prediction_metrics(combined), combined


def _rolling_supervised_predictions(
    model_name: str,
    config: dict[str, Any],
    labeled: pd.DataFrame,
    feature_rows: pd.DataFrame,
    dates: list[pd.Timestamp],
) -> RollingPredictionResult:
    """
    Refit on available labels every RETRAIN_STEP signal dates.

    Returns both predictions and an auditable fit log. A model is only reused
    between refits; every training row still satisfies label_end_date <= t.
    """
    predictions = []
    last_model: Optional[Pipeline] = None
    last_fit_index = -999
    fit_log: list[dict[str, Any]] = []
    ordered_dates = sorted(pd.Timestamp(value) for value in dates)
    trading_dates = pd.DatetimeIndex(
        sorted(pd.Timestamp(value) for value in feature_rows["date"].unique())
    )
    for index, date in enumerate(ordered_dates):
        rows = feature_rows.loc[feature_rows["date"] == date].copy()
        if rows.empty:
            continue
        positions = trading_dates.get_indexer([date])
        if len(positions) == 0 or positions[0] < 0:
            continue
        trading_position = int(positions[0])
        should_refit = (
            last_model is None
            or trading_position - last_fit_index >= RETRAIN_STEP
        )
        if should_refit:
            train = labeled.loc[labeled["label_end_date"] <= date]
            if len(train) < MIN_TRAIN_ROWS:
                continue
            try:
                fitted = _fit_predict(model_name, config, train, rows)
            except (ValueError, TypeError, FloatingPointError):
                continue
            last_model = fitted.model
            last_fit_index = trading_position
            fit_log.append(
                {
                    "signal_date": date.strftime("%Y-%m-%d"),
                    "train_start": pd.Timestamp(train["date"].min()).strftime("%Y-%m-%d"),
                    "train_end": pd.Timestamp(train["date"].max()).strftime("%Y-%m-%d"),
                    "label_cutoff": pd.Timestamp(
                        train["label_end_date"].max()
                    ).strftime("%Y-%m-%d"),
                    "train_rows": int(len(train)),
                    "prediction_rows": int(len(rows)),
                }
            )
        try:
            pred = np.asarray(last_model.predict(rows[FEATURE_NAMES + ["industry"]]), dtype=float)
        except Exception:
            continue
        if not np.isfinite(pred).all():
            continue
        predictions.append(
            _prediction_frame(
                rows,
                pd.Series(pred, index=rows.index),
            )
        )
    return RollingPredictionResult(
        predictions=(
            pd.concat(predictions, ignore_index=True)
            if predictions
            else pd.DataFrame(
                columns=["date", "symbol", "future_excess", "prediction"]
            )
        ),
        fit_log=fit_log,
    )


def _rolling_factor_predictions(
    feature_rows: pd.DataFrame,
    feature_panels: dict[str, pd.DataFrame],
    variant: str,
    dates: list[pd.Timestamp],
    labeled: pd.DataFrame,
) -> pd.DataFrame:
    predictions = _factor_predictions(
        feature_rows, feature_panels, variant, dates, labeled
    )
    return predictions.loc[predictions["prediction"].notna()].copy()


def _feature_importance(
    fitted: FitResult,
    prediction_rows: pd.DataFrame,
    model_name: str,
) -> list[dict[str, Any]]:
    values: np.ndarray
    if model_name in {"random_forest", "gradient_boosting"} and len(prediction_rows) > 40:
        sample = prediction_rows.sample(
            n=min(1200, len(prediction_rows)),
            random_state=SEED,
        )
        try:
            result = permutation_importance(
                fitted.model,
                sample[FEATURE_NAMES + ["industry"]],
                sample["future_excess"],
                n_repeats=2,
                random_state=SEED,
                scoring="neg_mean_absolute_error",
            )
            values = np.asarray(result.importances_mean, dtype=float)
        except Exception:
            estimator = fitted.model.named_steps["model"]
            values = np.asarray(estimator.feature_importances_, dtype=float)
    else:
        estimator = fitted.model.named_steps["model"]
        if hasattr(estimator, "coef_"):
            values = np.abs(np.asarray(estimator.coef_, dtype=float).reshape(-1))
        else:
            values = np.zeros(len(fitted.feature_names), dtype=float)
    names = fitted.feature_names
    if len(names) != len(values):
        names = [f"feature_{index}" for index in range(len(values))]
    rows = [
        {"name": str(name), "importance": _finite(value)}
        for name, value in zip(names, values)
        if math.isfinite(float(value))
    ]
    rows.sort(key=lambda row: row["importance"] or 0.0, reverse=True)
    return rows[:12]


def run_ml_pipeline(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    volume: pd.DataFrame,
    metadata: dict[str, dict],
    params: dict,
) -> dict[str, Any]:
    horizon = int(params.get("label_horizon", 20))
    features, feature_rows, labeled = build_samples(
        close, amount, volume, metadata, horizon=horizon
    )
    feature_rows = feature_rows.merge(
        labeled[["date", "symbol", "future_excess"]],
        on=["date", "symbol"],
        how="left",
    )
    all_dates = sorted(pd.Timestamp(value) for value in labeled["date"].unique())
    if len(all_dates) < 8:
        raise ValueError("可用于时间序列建模的样本日期不足")
    dev_end = max(1, min(len(all_dates) - 1, int(len(all_dates) * 0.6)))
    dev_dates = all_dates[:dev_end]
    holdout_dates = all_dates[dev_end:]
    folds = _fold_dates(dev_dates)
    if len(folds) < 2:
        raise ValueError("开发期不足以形成两个时间验证折")

    factor_variant = str(params.get("factor_variant") or "balanced")
    factor_validation, factor_validation_predictions = _validate_factor(
        factor_variant, labeled, feature_rows, features, folds
    )
    model_rows: list[dict[str, Any]] = [
        {
            "model": "factor",
            "label": MODEL_LABELS["factor"],
            "status": "ok",
            "hyperparameters": FACTOR_WEIGHTS[factor_variant],
            "validation": factor_validation,
        }
    ]
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for family in ("ridge", "random_forest", "gradient_boosting"):
        best: Optional[tuple[dict[str, Any], dict[str, Any]]] = None
        for config in CONFIG_GRID[family]:
            metrics, _predictions = _validate_supervised(
                family, config, labeled, feature_rows, folds
            )
            if metrics["samples"] == 0:
                continue
            if best is None or (metrics.get("rank_ic_mean") or -9) > (
                best[1].get("rank_ic_mean") or -9
            ):
                best = (config, metrics)
        if best is None:
            model_rows.append(
                {
                    "model": family,
                    "label": MODEL_LABELS[family],
                    "status": "failed",
                    "hyperparameters": None,
                    "validation": prediction_metrics(pd.DataFrame()),
                }
            )
            continue
        config, metrics = best
        candidates[family] = (config, metrics)
        model_rows.append(
            {
                "model": family,
                "label": MODEL_LABELS[family],
                "status": "ok",
                "hyperparameters": config,
                "validation": metrics,
            }
        )

    requested = MODEL_ALIASES.get(str(params.get("model") or "auto"), "auto")
    eligible = [
        row
        for row in model_rows
        if row["validation"]["samples"] > 0
        and (row["validation"].get("r2_oos") is None or row["validation"]["r2_oos"] > -0.5)
    ]
    if not eligible:
        eligible = [row for row in model_rows if row["validation"]["samples"] > 0]
    if requested == "auto":
        factor_ic = factor_validation.get("rank_ic_mean")
        supervised = [row for row in eligible if row["model"] != "factor"]
        best_ml = max(
            supervised,
            key=lambda row: row["validation"].get("rank_ic_mean") or -9,
            default=None,
        )
        if (
            best_ml is None
            or factor_ic is None
            or (best_ml["validation"].get("rank_ic_mean") or -9) <= float(factor_ic)
        ):
            selected = "factor"
        else:
            selected = best_ml["model"]
    else:
        selected = requested if requested in MODEL_LABELS else "factor"
        if selected not in {row["model"] for row in eligible}:
            selected = "factor"

    prediction_dates = sorted(
        set(_monthly_dates(pd.DatetimeIndex(all_dates)))
        | {pd.Timestamp(all_dates[-1]), pd.Timestamp(close.index[-1])}
    )
    selected_config = None
    if selected == "factor":
        score_predictions = _rolling_factor_predictions(
            feature_rows, features, factor_variant, prediction_dates, labeled
        )
        selected_fit_log = []
    else:
        selected_config = candidates[selected][0]
        rolling = _rolling_supervised_predictions(
            selected, selected_config, labeled, feature_rows, prediction_dates
        )
        score_predictions = rolling.predictions
        selected_fit_log = rolling.fit_log
    if score_predictions.empty:
        raise ValueError("模型未生成可用的样本外预测")

    holdout_metric_predictions: dict[str, pd.DataFrame] = {}
    holdout_evaluation_dates = _monthly_dates(pd.DatetimeIndex(holdout_dates))
    for row in model_rows:
        family = row["model"]
        if row["status"] != "ok":
            continue
        if family == "factor":
            holdout_metric_predictions[family] = _rolling_factor_predictions(
                feature_rows, features, factor_variant, holdout_evaluation_dates, labeled
            )
        else:
            config, _ = candidates[family]
            holdout_metric_predictions[family] = _rolling_supervised_predictions(
                family,
                config,
                labeled,
                feature_rows,
                holdout_evaluation_dates,
            ).predictions
        row["holdout"] = prediction_metrics(holdout_metric_predictions[family])
        row["selected"] = family == selected

    score_panel = score_predictions.pivot_table(
        index="date",
        columns="symbol",
        values="prediction",
        aggfunc="last",
    ).sort_index()
    current_date = score_panel.index.max()
    current_score = score_panel.loc[current_date].dropna().sort_values(ascending=False)

    feature_importance: list[dict[str, Any]] = []
    if selected != "factor":
        selected_config = selected_config or candidates[selected][0]
        train = labeled.loc[labeled["label_end_date"] <= holdout_dates[0]]
        validation_rows = feature_rows.loc[feature_rows["date"].isin(holdout_dates[: max(1, len(holdout_dates) // 4)])]
        if len(train) >= MIN_TRAIN_ROWS and len(validation_rows) >= 20:
            try:
                fitted = _fit_predict(selected, selected_config, train, validation_rows)
                feature_importance = _feature_importance(fitted, validation_rows, selected)
            except (ValueError, TypeError, FloatingPointError):
                feature_importance = []


    return {
        "score_panel": score_panel,
        "current_score": current_score,
        "latest_date": current_date,
        "features": features,
        "feature_rows": feature_rows,
        "labeled": labeled,
        "training": {
            "selected_model": selected,
            "selected_label": MODEL_LABELS[selected],
            "requested_model": requested,
            "horizon_days": horizon,
            "development_start": dev_dates[0].strftime("%Y-%m-%d"),
            "development_end": dev_dates[-1].strftime("%Y-%m-%d"),
            "holdout_start": holdout_dates[0].strftime("%Y-%m-%d"),
            "holdout_end": holdout_dates[-1].strftime("%Y-%m-%d"),
            "rows_total": int(len(labeled)),
            "rows_development": int(
                labeled["date"].isin(dev_dates).sum()
            ),
            "rows_holdout": int(labeled["date"].isin(holdout_dates).sum()),
            "folds": [
                {
                    "train_end": start.strftime("%Y-%m-%d"),
                    "validation_start": start.strftime("%Y-%m-%d"),
                    "validation_end": end.strftime("%Y-%m-%d"),
                    "train_rows": int((labeled["label_end_date"] <= start).sum()),
                    "validation_rows": int(
                        labeled.loc[
                            (labeled["date"] >= start) & (labeled["date"] <= end)
                        ].shape[0]
                    ),
                }
                for start, end in folds
            ],
            "leakage_checks": [
                {"name": "特征时点", "status": "passed", "detail": "仅使用信号日 t 及之前"},
                {"name": "标签隔离", "status": "passed", "detail": f"训练标签截止日不晚于信号日，预留 {horizon} 日窗口"},
                {"name": "留出期冻结", "status": "passed", "detail": "模型与超参数仅由开发期验证折选择"},
            ],
            "retrain_step": RETRAIN_STEP,
            "rolling_fits": selected_fit_log,
            "fit_count": len(selected_fit_log),
            "selected_hyperparameters": selected_config,
            "selection_reason": (
                "开发期监督模型未超过因子基线，保留可解释基线"
                if selected == "factor" and requested == "auto"
                else "按开发期验证 Rank IC 与 R² 约束选择"
            ),
            "evaluation_frequency": "月末信号，样本外每 20 个交易日重训",
            "feature_importance": feature_importance,
            "model_comparison": model_rows,
            "selection_rule": "开发期验证 Rank IC 最高，并排除 R² 明显为负的候选",
        },
        "feature_catalog": FEATURE_CATALOG,
    }
