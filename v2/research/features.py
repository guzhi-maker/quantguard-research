"""特征工程：候选池、截面中性化、去冗余聚类、IC 衰减。

设计口径见 docs/quant-research-v2-workflow.md 阶段 1。

本模块只处理面板数据，不读文件、不写文件。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from research.labels import forward_returns

TRADING_DAYS = 252


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    label: str
    formula: str
    category: str


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("momentum_5", "5 日收益", "close_t / close_{t-5} - 1", "价格趋势"),
    FeatureSpec("momentum_20", "20 日动量", "close_t / close_{t-20} - 1", "价格趋势"),
    FeatureSpec("momentum_60", "60 日动量", "close_t / close_{t-60} - 1", "价格趋势"),
    FeatureSpec("momentum_120", "120 日动量", "close_t / close_{t-120} - 1", "价格趋势"),
    FeatureSpec("volatility_10", "10 日波动", "日收益 10 日标准差 × sqrt(252)", "风险"),
    FeatureSpec("volatility_20", "20 日波动", "日收益 20 日标准差 × sqrt(252)", "风险"),
    FeatureSpec("volatility_60", "60 日波动", "日收益 60 日标准差 × sqrt(252)", "风险"),
    FeatureSpec("price_to_ma20", "价格偏离 20 日均线", "close_t / MA20 - 1", "趋势"),
    FeatureSpec("price_to_ma60", "价格偏离 60 日均线", "close_t / MA60 - 1", "趋势"),
    FeatureSpec("relative_strength_20", "20 日相对位置", "close_t / max(close, 20) - 1", "趋势"),
    FeatureSpec("volume_ratio_5_20", "成交量短长比", "MA5(volume) / MA20(volume)", "流动性"),
    FeatureSpec("amount_ratio_5_20", "成交额短长比", "MA5(amount) / MA20(amount)", "流动性"),
    FeatureSpec("liquidity_log_20", "流动性规模", "log1p(MA20(amount))", "流动性"),
    FeatureSpec("amihud_20", "Amihud 非流动性", "MA20(|return| / amount)", "流动性"),
    FeatureSpec("max_drawdown_60", "60 日最大回撤", "close / rolling_max_60(close) - 1", "风险"),
    FeatureSpec("turnover_mean_20", "20 日均换手", "MA20(turnover)", "流动性"),
    FeatureSpec("beta_20d", "20 日 Beta", "个股日收益对等权市场的 20 日 OLS 斜率", "风险"),
    FeatureSpec("residual_vol_20", "20 日残差波动", "上述回归残差的标准差", "风险"),
    FeatureSpec("gap_open_5", "5 日平均跳空", "MA5(open/prev_close - 1)", "微观结构"),
    FeatureSpec("volatility_ratio_5_20", "短长波动比", "vol5 / vol20 - 1", "微观结构"),
    FeatureSpec("amount_spike_20", "成交额冲击", "amount / MA20(amount) - 1", "微观结构"),
    FeatureSpec("price_volume_corr_20", "量价相关性", "20 日 corr(return, volume 变化)", "微观结构"),
    FeatureSpec("limit_up_count_20", "20 日涨停次数", "20 日内涨幅 >= 9.5% 的次数", "微观结构"),
    FeatureSpec("turnover_change_5_20", "换手短长比", "MA5(turnover) / MA20(turnover) - 1", "微观结构"),
    FeatureSpec("industry_relative_momentum_20", "行业相对动量", "20 日收益 - 所属行业 20 日均值", "微观结构"),
)

MARKET_FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("market_return_20", "股票池 20 日收益", "等权股票池 20 日收益", "市场状态"),
    FeatureSpec("market_breadth", "市场广度", "价格高于 60 日均线的股票比例", "市场状态"),
    FeatureSpec("cross_section_dispersion", "截面离散度", "20 日收益截面标准差", "市场状态"),
)

STOCK_FEATURES = tuple(spec.name for spec in FEATURE_SPECS)

# 微观结构特征：只在扩展变体中使用，历史较短、噪声更大
MICRO_FEATURES = (
    "gap_open_5",
    "volatility_ratio_5_20",
    "amount_spike_20",
    "price_volume_corr_20",
    "limit_up_count_20",
    "turnover_change_5_20",
    "industry_relative_momentum_20",
)

# 每个特征需要的最短历史（交易日），用于按快照长度与标签期限裁剪特征集
FEATURE_LOOKBACK: dict[str, int] = {
    "momentum_5": 5,
    "momentum_20": 20,
    "momentum_60": 60,
    "momentum_120": 120,
    "volatility_10": 10,
    "volatility_20": 20,
    "volatility_60": 60,
    "price_to_ma20": 20,
    "price_to_ma60": 60,
    "relative_strength_20": 20,
    "volume_ratio_5_20": 20,
    "amount_ratio_5_20": 20,
    "liquidity_log_20": 20,
    "amihud_20": 20,
    "max_drawdown_60": 60,
    "turnover_mean_20": 20,
    "beta_20d": 20,
    "residual_vol_20": 20,
    "gap_open_5": 6,
    "volatility_ratio_5_20": 20,
    "amount_spike_20": 20,
    "price_volume_corr_20": 21,
    "limit_up_count_20": 20,
    "turnover_change_5_20": 20,
    "industry_relative_momentum_20": 20,
}


# 固定白名单：10 个有经济含义、彼此低相关的特征。
# 用固定清单替代"每次跑聚类"，避免特征选择本身随样本变化（一种隐性过拟合）。
WHITELIST = (
    "momentum_20",
    "momentum_60",
    "volatility_20",
    "volatility_60",
    "max_drawdown_60",
    "amount_ratio_5_20",
    "amihud_20",
    "gap_open_5",
    "industry_relative_momentum_20",
    "residual_vol_20",
)


def eligible_features(days: int, available_dates: int | None = None) -> list[str]:
    """按标签期限与快照长度裁剪特征集。

    - 短中期标签（<=20 日）：允许 120 日回看；
    - 长标签（>=40 日）：只看 60 日以内，给标签本身留出历史；
    - 若快照偏短，再按"特征 + 标签 + 训练窗口"预算收紧。
    """
    limit = 120 if int(days) <= 20 else 60
    if available_dates is not None:
        budget = available_dates - int(days) - 80
        if budget > 0:
            limit = min(limit, max(20, budget))
    return [name for name in STOCK_FEATURES if FEATURE_LOOKBACK.get(name, 20) <= limit]
FEATURE_CATALOG = pd.DataFrame(
    [
        {
            "name": spec.name,
            "label": spec.label,
            "formula": spec.formula,
            "category": spec.category,
        }
        for spec in (*FEATURE_SPECS, *MARKET_FEATURES)
    ]
)


def _rolling_std(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    return frame.rolling(window, min_periods=max(3, window // 2)).std(ddof=1)


def build_features(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    volume: pd.DataFrame,
    turnover: pd.DataFrame | None = None,
    open_price: pd.DataFrame | None = None,
    change_pct: pd.DataFrame | None = None,
    industry: pd.Series | None = None,
) -> dict[str, pd.DataFrame]:
    """构建全部候选特征，返回 name -> 宽表。"""
    returns = close.pct_change(fill_method=None)
    amount_safe = amount.clip(lower=1.0)

    features: dict[str, pd.DataFrame] = {
        "momentum_5": close / close.shift(5) - 1.0,
        "momentum_20": close / close.shift(20) - 1.0,
        "momentum_60": close / close.shift(60) - 1.0,
        "momentum_120": close / close.shift(120) - 1.0,
        "volatility_10": _rolling_std(returns, 10) * np.sqrt(TRADING_DAYS),
        "volatility_20": _rolling_std(returns, 20) * np.sqrt(TRADING_DAYS),
        "volatility_60": _rolling_std(returns, 60) * np.sqrt(TRADING_DAYS),
        "price_to_ma20": close / close.rolling(20, min_periods=10).mean() - 1.0,
        "price_to_ma60": close / close.rolling(60, min_periods=30).mean() - 1.0,
        "relative_strength_20": close / close.rolling(20, min_periods=10).max() - 1.0,
        "volume_ratio_5_20": (
            volume.rolling(5, min_periods=3).mean() / volume.rolling(20, min_periods=10).mean()
        ),
        "amount_ratio_5_20": (
            amount.rolling(5, min_periods=3).mean() / amount.rolling(20, min_periods=10).mean()
        ),
        "liquidity_log_20": np.log1p(amount.rolling(20, min_periods=10).mean().clip(lower=1.0)),
        "amihud_20": (returns.abs() / amount_safe).rolling(20, min_periods=10).mean(),
        "max_drawdown_60": close / close.rolling(60, min_periods=30).max() - 1.0,
        "turnover_mean_20": (
            turnover.rolling(20, min_periods=10).mean()
            if turnover is not None
            else pd.DataFrame(np.nan, index=close.index, columns=close.columns)
        ),
    }

    # 微观结构特征：跳空、波动比、成交额冲击、量价相关、涨停次数、换手变化
    if open_price is not None:
        gap = open_price / close.shift(1) - 1.0
        features["gap_open_5"] = gap.rolling(5, min_periods=3).mean()
    else:
        features["gap_open_5"] = pd.DataFrame(np.nan, index=close.index, columns=close.columns)

    vol5 = _rolling_std(returns, 5) * np.sqrt(TRADING_DAYS)
    vol20 = _rolling_std(returns, 20) * np.sqrt(TRADING_DAYS)
    features["volatility_ratio_5_20"] = vol5 / vol20.replace(0.0, np.nan) - 1.0
    features["amount_spike_20"] = (
        amount / amount.rolling(20, min_periods=10).mean().replace(0.0, np.nan) - 1.0
    )
    features["price_volume_corr_20"] = returns.rolling(20, min_periods=10).corr(
        volume.pct_change(fill_method=None)
    )
    if change_pct is not None:
        spike = (change_pct >= 9.5).astype(float)
        features["limit_up_count_20"] = spike.rolling(20, min_periods=10).sum()
    else:
        features["limit_up_count_20"] = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    if turnover is not None:
        features["turnover_change_5_20"] = (
            turnover.rolling(5, min_periods=3).mean()
            / turnover.rolling(20, min_periods=10).mean().replace(0.0, np.nan)
            - 1.0
        )
    else:
        features["turnover_change_5_20"] = pd.DataFrame(np.nan, index=close.index, columns=close.columns)

    if industry is not None:
        groups = industry.reindex(close.columns).fillna("未分类")
        momentum_20 = close / close.shift(20) - 1.0
        industry_mean = momentum_20.T.groupby(groups).transform("mean").T
        features["industry_relative_momentum_20"] = momentum_20 - industry_mean
    else:
        features["industry_relative_momentum_20"] = pd.DataFrame(
            np.nan, index=close.index, columns=close.columns
        )

    # Beta 与残差波动：对等权市场收益做滚动回归（协方差形式）
    market = returns.mean(axis=1)
    mean_y = returns.rolling(20, min_periods=10).mean()
    mean_x = market.rolling(20, min_periods=10).mean()
    cov = (returns.mul(market, axis=0)).rolling(20, min_periods=10).mean() - mean_y.mul(mean_x, axis=0)
    var_y = returns.pow(2).rolling(20, min_periods=10).mean() - mean_y.pow(2)
    var_x = market.pow(2).rolling(20, min_periods=10).mean() - mean_x.pow(2)
    beta = cov.div(var_x.replace(0.0, np.nan), axis=0)
    residual_var = var_y - beta * cov
    features["beta_20d"] = beta
    features["residual_vol_20"] = np.sqrt(residual_var.clip(lower=0.0))

    equal_nav = (1.0 + market.fillna(0.0)).cumprod()
    market_return = equal_nav / equal_nav.shift(20) - 1.0
    ma60 = close.rolling(60, min_periods=30).mean()
    breadth = (close > ma60).mean(axis=1)
    dispersion = (close / close.shift(20) - 1.0).std(axis=1)
    for name, series in (
        ("market_return_20", market_return),
        ("market_breadth", breadth),
        ("cross_section_dispersion", dispersion),
    ):
        features[name] = pd.DataFrame(
            np.repeat(series.to_numpy()[:, None], close.shape[1], axis=1),
            index=close.index,
            columns=close.columns,
        )

    return {name: frame.reindex(index=close.index, columns=close.columns) for name, frame in features.items()}


def cross_sectional_zscore(row: pd.Series, winsorize: float = 3.0) -> pd.Series:
    """单行截面：先缩尾再标准化。"""
    values = pd.to_numeric(row, errors="coerce").dropna()
    if values.empty:
        return pd.Series(np.nan, index=row.index)
    mean, std = values.mean(), values.std(ddof=0)
    if not np.isfinite(std) or std <= 0:
        return pd.Series(0.0, index=row.index)
    clipped = values.clip(mean - winsorize * std, mean + winsorize * std)
    return ((clipped - mean) / std).reindex(row.index)


def neutralize(
    matrix: pd.DataFrame,
    industry: pd.Series,
    size: pd.DataFrame,
    winsorize: float = 3.0,
    min_obs: int = 10,
) -> pd.DataFrame:
    """对行业与 ln(成交额) 做逐截面回归，返回残差的截面 z-score。

    行业退化（单一行业）时自动只对规模做中性化。
    """
    aligned_industry = industry.reindex(matrix.columns).fillna("未分类")
    dummies = pd.get_dummies(aligned_industry).astype(float)
    if dummies.shape[1] > 1:
        dummies = dummies.iloc[:, 1:]
    else:
        dummies = dummies.iloc[:, :0]

    output = pd.DataFrame(np.nan, index=matrix.index, columns=matrix.columns, dtype=float)
    size_log = np.log1p(size.clip(lower=1.0))

    for date in matrix.index:
        row = matrix.loc[date]
        valid = row.notna()
        count = int(valid.sum())
        if count < min_obs:
            continue
        columns = [np.ones(count)]
        for column in dummies.columns:
            columns.append(dummies.loc[valid, column].to_numpy(dtype=float))
        sizes = size_log.loc[date].reindex(row.index)[valid].to_numpy(dtype=float)
        size_std = sizes.std()
        if np.isfinite(size_std) and size_std > 0:
            columns.append((sizes - sizes.mean()) / size_std)
        design = np.column_stack(columns)
        target = row[valid].to_numpy(dtype=float)
        try:
            coef, *_ = np.linalg.lstsq(design, target, rcond=None)
        except np.linalg.LinAlgError:
            continue
        residual = target - design @ coef
        series = pd.Series(residual, index=row.index[valid])
        output.loc[date, series.index] = cross_sectional_zscore(series, winsorize=winsorize)

    return output


def ic_series(factor: pd.DataFrame, forward: pd.DataFrame, min_obs: int = 8) -> pd.Series:
    """逐日截面 Spearman 相关（Rank IC）。

    只对"样本足够且因子有区分度"的截面计算，避免常量输入产生无意义相关。
    """
    index = factor.index.intersection(forward.index)
    columns = factor.columns.intersection(forward.columns)
    if len(index) == 0 or len(columns) == 0:
        return pd.Series(dtype=float)
    left, right = factor.loc[index, columns], forward.loc[index, columns]
    paired = left.notna() & right.notna()
    counts = paired.sum(axis=1)
    unique = left.where(paired).nunique(axis=1)
    eligible = (counts >= min_obs) & (unique >= 3)
    if not eligible.any():
        return pd.Series(np.nan, index=index)
    ic = left.loc[eligible].corrwith(right.loc[eligible], axis=1, method="spearman")
    return ic.reindex(index)


def factor_ic_summary(
    features: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    names: list[str] | None = None,
    horizon: int = 20,
) -> pd.DataFrame:
    """每个因子在给定持有期上的 IC 均值、标准差与 ICIR。"""
    forward = forward_returns(close, horizon)
    rows = []
    for name in names or list(features):
        series = ic_series(features[name], forward).dropna()
        if len(series) < 3:
            rows.append({"name": name, "ic_mean": np.nan, "ic_std": np.nan, "ic_ir": np.nan, "samples": len(series)})
            continue
        mean, std = float(series.mean()), float(series.std(ddof=1))
        rows.append(
            {
                "name": name,
                "ic_mean": mean,
                "ic_std": std,
                "ic_ir": float(mean / std * np.sqrt(TRADING_DAYS / horizon)) if std > 0 else np.nan,
                "samples": len(series),
            }
        )
    return pd.DataFrame(rows)


def ic_decay(
    features: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 10, 20, 40, 60),
) -> pd.DataFrame:
    """IC 衰减曲线与半衰期（指数拟合）。"""
    rows = []
    for name, frame in features.items():
        curve = {}
        for horizon in horizons:
            series = ic_series(frame, forward_returns(close, horizon)).dropna()
            curve[horizon] = float(series.mean()) if len(series) >= 3 else np.nan
        positive = [(h, v) for h, v in curve.items() if np.isfinite(v) and v > 0]
        half_life = np.nan
        if len(positive) >= 2:
            xs = np.array([h for h, _ in positive], dtype=float)
            ys = np.log(np.array([v for _, v in positive], dtype=float))
            slope = np.polyfit(xs, ys, 1)[0]
            if slope < 0:
                half_life = float(np.log(2.0) / -slope)
        rows.append(
            {
                "name": name,
                **{f"ic_h{horizon}": value for horizon, value in curve.items()},
                "half_life": half_life,
            }
        )
    return pd.DataFrame(rows)


def cluster_dedupe(
    features: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    names: list[str] | None = None,
    horizon: int = 20,
    max_clusters: int = 20,
) -> tuple[list[str], pd.DataFrame]:
    """按 IC 序列相关性聚类去冗余，每簇保留 |IC| 最大的代表。"""
    candidates = names or list(features)
    forward = forward_returns(close, horizon)
    ic_table = pd.DataFrame(
        {name: ic_series(features[name], forward) for name in candidates}
    )
    ic_table = ic_table.dropna(how="all")
    if ic_table.shape[1] <= 1 or len(ic_table) < 3:
        return candidates, pd.DataFrame({"name": candidates, "cluster": 0, "ic_mean": np.nan})

    correlation = ic_table.corr(method="spearman").abs().fillna(0.0)
    distance = 1.0 - correlation.to_numpy()
    np.fill_diagonal(distance, 0.0)
    distance = (distance + distance.T) / 2.0
    condensed = squareform(distance, checks=False)
    clusters = fcluster(linkage(condensed, method="average"), t=max_clusters, criterion="maxclust")

    ranking = pd.DataFrame(
        {"name": candidates, "cluster": clusters}
    )
    ranking["ic_mean"] = ranking["name"].map(ic_table.mean().to_dict())
    ranking["abs_ic"] = ranking["ic_mean"].abs()
    representatives = (
        ranking.sort_values(["cluster", "abs_ic"], ascending=[True, False])
        .groupby("cluster", as_index=False)
        .first()
    )
    representatives = representatives.sort_values("abs_ic", ascending=False)
    return representatives["name"].tolist(), ranking.drop(columns=["abs_ic"])


def drop_constant(
    features: dict[str, pd.DataFrame],
    names: list[str],
    lookback: int = 60,
    min_unique: int = 3,
    min_coverage: float = 0.30,
) -> tuple[list[str], list[str]]:
    """剔除不可用特征：全 NaN、覆盖率过低、截面几乎无区分度。

    返回 (保留, 剔除)，用于在建模前做一次轻量数据体检。
    """
    keep: list[str] = []
    dropped: list[str] = []
    for name in names:
        frame = features.get(name)
        if frame is None:
            dropped.append(name)
            continue
        coverage = float(frame.notna().to_numpy().mean())
        if coverage < min_coverage:
            dropped.append(name)
            continue
        tail = frame.tail(lookback)
        unique = tail.apply(lambda row: row.nunique(dropna=True), axis=1)
        if float(unique.mean()) < min_unique:
            dropped.append(name)
            continue
        keep.append(name)
    return keep, dropped


def size_proxy(amount: pd.DataFrame) -> pd.DataFrame:
    """规模代理：20 日均成交额（用于中性化）。"""
    return amount.rolling(20, min_periods=5).mean().fillna(amount.median())
