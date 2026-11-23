"""端到端编排：数据 -> 特征 -> 标签 -> 主模型 -> 元模型 -> 组合 -> 诊断/建议 -> 证据包。

不修改采集链路，只读 data/raw 下的原始快照。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import CostConfig
from research import advice as advice_mod
from research import diagnosis as diagnosis_mod
from research import evidence as evidence_mod
from research import features as features_mod
from research import labels as labels_mod
from research import meta as meta_mod
from research import panel as panel_mod
from research import portfolio as portfolio_mod
from research import preferences as preferences_mod
from research import primary as primary_mod
from research import report as report_mod
from research import validation as validation_mod

MODEL_VERSION = "qg-v2"
SHRINK = 0.35
EXPECTED_CLIP = 0.20
MAX_CLUSTERS = 12


@dataclass
class RunResult:
    payload: dict

    def __getitem__(self, key):
        return self.payload[key]

    def get(self, key, default=None):
        return self.payload.get(key, default)

    def as_dict(self) -> dict:
        return self.payload


def _shrink(expected: pd.Series, factor: float = SHRINK) -> pd.Series:
    """预期收益向截面均值收缩，抑制优化器对极端预测的过度反应。"""
    values = expected.dropna()
    if values.empty:
        return expected
    mean = float(values.mean())
    shrunk = mean + factor * (values - mean)
    return shrunk.reindex(expected.index)


def _attributions(
    model,
    latest_rows: pd.DataFrame,
    reference: pd.DataFrame,
    feature_names: list[str],
    names: pd.Series,
    symbols: list[str] | None = None,
    top_features: int = 5,
    top_stocks: int = 10,
) -> dict[str, list]:
    """个股归因：用训练集上的置换重要性筛特征，再按个股 z 值排序取前 3。"""
    importance = {}
    try:
        from sklearn.inspection import permutation_importance

        sample = reference.dropna(subset=feature_names + ["label"])
        if len(sample) > 2000:
            sample = sample.sample(2000, random_state=20260401)
        if len(sample) > 20:
            estimator = model.models.get(0.5) or next(iter(model.models.values()))
            result = permutation_importance(
                estimator,
                sample[feature_names],
                sample["label"],
                n_repeats=3,
                random_state=20260401,
            )
            importance = dict(zip(feature_names, result.importances_mean))
    except Exception:  # noqa: BLE001
        importance = {}

    if not importance:
        importance = {name: 1.0 for name in feature_names}
    ranked = sorted(importance, key=importance.get, reverse=True)[:top_features]
    if symbols:
        rows = latest_rows[latest_rows["symbol"].isin(symbols)]
    else:
        rows = latest_rows.head(top_stocks)
    rows = rows.dropna(subset=feature_names)
    output: dict[str, list] = {}
    for row in rows.itertuples(index=False):
        symbol = str(row.symbol)
        entries = []
        for name in ranked:
            value = float(getattr(row, name))
            score = abs(value) * float(importance.get(name, 0.0) or 1.0)
            entries.append(
                {
                    "feature": name,
                    "z": round(value, 3),
                    "score": round(score, 4),
                }
            )
        entries.sort(key=lambda item: item["score"], reverse=True)
        output[symbol] = entries[:3]
    return output


def _coverage_ok(coverage: dict) -> bool:
    low, high = coverage.get("p10_breach"), coverage.get("p90_breach")
    if low is None or high is None:
        return False
    return 0.05 <= low <= 0.16 and 0.05 <= high <= 0.16 and coverage.get("cross_rate", 0) == 0


DEFAULT_VARIANT = {
    "name": "lean",
    "feature_mode": "extended",
    "feature_set": "whitelist",
    "label": "none",
    "label_baseline": "industry",
    "seeds": 3,
    "calibration": "adaptive",
    "portfolio_mode": "rank",
    "use_meta": "off",
}


def make_variant(**overrides) -> dict:
    variant = dict(DEFAULT_VARIANT)
    variant.update({key: value for key, value in overrides.items() if value is not None})
    return variant


def _coverage_ok(coverage: dict) -> bool:
    low, high = coverage.get("p10_breach"), coverage.get("p90_breach")
    if low is None or high is None:
        return False
    return 0.05 <= low <= 0.16 and 0.05 <= high <= 0.16 and coverage.get("cross_rate", 0) == 0


INDUSTRY_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "industry_map.csv"


def load_industry_map(path: Path | None = None) -> dict[str, str]:
    """读取离线行业映射（symbol -> 行业名），缺失时返回空字典。"""
    target = Path(path or INDUSTRY_MAP_PATH)
    if not target.exists():
        return {}
    import csv

    mapping: dict[str, str] = {}
    with target.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "").strip()
            label = str(row.get("industry") or "").strip()
            if symbol and label and label not in {"证监会行业分类", "未分类"}:
                mapping[symbol] = label
    return mapping


def apply_industry_map(panels, mapping: dict[str, str]) -> dict:
    """用离线映射覆盖行业字段，返回覆盖统计。"""
    if not mapping:
        return {"source": "snapshot", "matched": 0, "industries": int(panels.meta["industry"].nunique())}
    mapped = panels.meta.index.to_series().map(mapping)
    before = panels.meta["industry"].astype(str)
    panels.meta["industry"] = mapped.fillna(before)
    matched = int(mapped.notna().sum())
    return {
        "source": "offline_map",
        "matched": matched,
        "total": int(len(panels.meta)),
        "industries": int(panels.meta["industry"].nunique()),
    }


def build_inputs(
    run_id: str | None = None,
    horizon: int = 2,
    variant: dict | None = None,
    max_clusters: int = MAX_CLUSTERS,
    progress=None,
) -> dict:
    """准备面板、特征、标签与训练集；run_research 与组合回测共用。"""
    _step = progress or (lambda name: None)
    _step("读取快照")
    variant = make_variant(**(variant or {}))
    panels = panel_mod.load_snapshot(run_id)
    industry_stats = apply_industry_map(panels, load_industry_map())
    industry = panel_mod.industry_proxy(panels)
    tradable_all = panel_mod.tradability(panels)
    sellable_all = panel_mod.sellable(panels)
    names = panels.meta["name"]
    _step("面板就绪，开始计算特征")

    preference = preferences_mod.resolve(5.5, horizon)
    days = int(preference.horizon["days"])

    raw_features = features_mod.build_features(
        panels.close,
        panels.amount,
        panels.volume,
        panels.turnover,
        open_price=panels.open,
        change_pct=panels.change_pct,
        industry=industry,
    )
    _step("特征计算完成，生成标签")
    size = features_mod.size_proxy(panels.amount)
    stock_pool = features_mod.eligible_features(days, len(panels.dates))
    feature_mode = str(variant.get("feature_mode", "extended"))
    # 实验结论（600 交易日样本）：微观结构特征在 5/20/60 日都不劣于基础特征，
    # 只有显式指定 base 时才关闭。
    if feature_mode == "base":
        stock_pool = [name for name in stock_pool if name not in features_mod.MICRO_FEATURES]
    stock_pool, dropped_features = features_mod.drop_constant(raw_features, stock_pool)
    if len(stock_pool) < 4:
        raise ValueError("快照历史过短，可用于当前标签期限的特征不足")
    feature_set = str(variant.get("feature_set", "whitelist"))
    if feature_set == "whitelist":
        representatives = [name for name in features_mod.WHITELIST if name in stock_pool]
        cluster_table = pd.DataFrame({"name": representatives, "cluster": 0})
        if len(representatives) < 4:
            representatives, cluster_table = features_mod.cluster_dedupe(
                {name: raw_features[name] for name in stock_pool},
                panels.close,
                max_clusters=max_clusters,
            )
    else:
        representatives, cluster_table = features_mod.cluster_dedupe(
            {name: raw_features[name] for name in stock_pool},
            panels.close,
            max_clusters=max_clusters,
        )
    neutral = {
        name: features_mod.neutralize(raw_features[name], industry, size) for name in representatives
    }
    label_matrix, end_dates = labels_mod.build_label_panel(
        panels.close,
        industry,
        days,
        transform=str(variant.get("label", "none")),
        label_baseline=str(variant.get("label_baseline", "industry")),
    )
    dataset = labels_mod.to_long(label_matrix, neutral, end_dates, representatives)
    dataset = dataset.dropna(subset=representatives)
    if dataset.empty:
        raise ValueError("特征与标签对齐后没有可用样本，请检查快照长度")

    _step("训练集就绪")
    return {
        "variant": variant,
        "panels": panels,
        "industry": industry,
        "tradable": tradable_all,
        "sellable": sellable_all,
        "names": names,
        "preference": preference,
        "days": days,
        "representatives": representatives,
        "cluster_table": cluster_table,
        "dataset": dataset,
        "dropped_features": dropped_features,
        "stock_pool": stock_pool,
        "neutral": neutral,
        "size": size,
        "industry_stats": industry_stats,
    }


def run_research(
    run_id: str | None = None,
    risk: float = 5.5,
    horizon: int = 2,
    holdings: dict[str, float] | None = None,
    capital: float = 1_000_000.0,
    with_report: bool = False,
    client=None,
    max_clusters: int = MAX_CLUSTERS,
    variant: dict | None = None,
    progress=None,
    positions: int | None = None,
) -> RunResult:
    """跑一次完整的 v2 研究流程。variant 控制特征、标签、集成、校准与组合模式。

    progress 是可选的回调，接收字符串阶段名，用于前端展示进度。
    """

    def _step(name: str) -> None:
        if progress is not None:
            try:
                progress(name)
            except Exception:  # noqa: BLE001
                pass

    inputs = build_inputs(
        run_id=run_id,
        horizon=horizon,
        variant=variant,
        max_clusters=max_clusters,
        progress=progress,
    )
    variant = inputs["variant"]
    panels = inputs["panels"]
    industry = inputs["industry"]
    tradable_all = inputs["tradable"]
    sellable_all = inputs["sellable"]
    names = inputs["names"]
    days = inputs["days"]
    representatives = inputs["representatives"]
    cluster_table = inputs["cluster_table"]
    dataset = inputs["dataset"]
    dropped_features = inputs["dropped_features"]
    stock_pool = inputs["stock_pool"]
    neutral = inputs["neutral"]
    preference = preferences_mod.resolve(risk, horizon, positions=positions)
    warnings_list: list[str] = []

    dates = np.sort(dataset["date"].unique())
    folds = validation_mod.walk_forward_folds(dates, n_folds=3, embargo=5)

    oos_frames: list[pd.DataFrame] = []
    fold_log: list[dict] = []
    validation_mode = "walk_forward"

    def _run_fold(train_frame: pd.DataFrame, rows: pd.DataFrame, label: str, start, end, cutoff) -> None:
        model = primary_mod.fit_quantiles(
            train_frame,
            representatives,
            seeds=int(variant.get("seeds", 1)),
        )
        predicted = primary_mod.predict_quantiles(model, rows)
        predicted = predicted.merge(
            dataset[["date", "symbol", "label"]], on=["date", "symbol"], how="left"
        )
        oos_frames.append(predicted)
        fold_log.append(
            {
                "label": label,
                "start": str(pd.Timestamp(start).date()),
                "end": str(pd.Timestamp(end).date()),
                "train_rows": int(len(train_frame)),
                "predict_rows": int(len(predicted)),
                "cutoff": str(pd.Timestamp(cutoff).date()),
            }
        )

    _step("模型训练与滚动验证")
    for fold in folds:
        cutoff = validation_mod.train_cutoff(dates, fold.start, embargo=5)
        if cutoff is None:
            continue
        train = dataset[dataset["label_end_date"] <= cutoff]
        if len(train) < primary_mod.MIN_TRAIN_ROWS:
            continue
        rows = dataset[(dataset["date"] >= fold.start) & (dataset["date"] <= fold.end)]
        if rows.empty:
            continue
        try:
            _run_fold(train, rows, fold.label, fold.start, fold.end, cutoff)
            _step(f"验证折 {fold.label} 完成")
        except ValueError:
            continue

    if not oos_frames:
        # 长标签（如 60 日）在短快照上凑不满滚动折，退化为单次留出验证
        validation_mode = "holdout"
        required = days + 5 + 3
        if len(dates) <= required + 2:
            raise ValueError(
                "样本过短：{} 日标签至少需要 {} 个有效交易日，当前只有 {} 个；"
                "请扩大采集区间".format(days, required + 3, len(dates))
            )
        holdout_count = min(max(3, int(len(dates) * 0.25)), len(dates) - required)
        start_index = len(dates) - holdout_count
        holdout_start = dates[start_index]
        cutoff = validation_mod.train_cutoff(dates, holdout_start, embargo=5)
        if cutoff is None:
            raise ValueError("样本过短：连单次留出验证都无法满足标签隔离要求，请采集更长历史")
        train = dataset[dataset["label_end_date"] <= cutoff]
        rows = dataset[dataset["date"] >= holdout_start]
        if len(train) < primary_mod.MIN_TRAIN_ROWS or rows.empty:
            raise ValueError(
                "样本过短：{} 日标签需要更长的历史窗口，请扩大采集区间".format(days)
            )
        _run_fold(train, rows, "holdout", holdout_start, dates[-1], cutoff)

    oos = pd.concat(oos_frames, ignore_index=True) if oos_frames else pd.DataFrame()
    if oos.empty:
        raise ValueError("滚动验证没有产生样本外预测，样本可能过短")

    calibration = str(variant.get("calibration", "none"))
    offsets = None
    calibration_eval = {"mode": calibration, "samples": 0}
    if calibration == "adaptive":
        # 自适应保形：前 40% 做初始拟合，之后按日在线更新并做因果评估
        offsets = primary_mod.adaptive_offsets(oos)
        calibration_eval = {
            "mode": "adaptive",
            "samples": int(offsets.get("samples", 0)),
            "increments": int(offsets.get("increments", 0)),
            "gamma": offsets.get("gamma"),
            "fit_offsets": {
                key: round(float(value), 6)
                for key, value in offsets.items()
                if key not in {"samples", "increments", "burn_in", "gamma"}
            },
            "holdout_coverage": primary_mod.causal_coverage(oos),
            "raw_coverage": primary_mod.coverage_report(
                oos[["date", "symbol", "P10", "P50", "P90"]], oos["label"]
            ),
        }
    elif calibration in {"conformal", "cqr"}:
        # 前 60% 拟合偏移、后 40% 评估，避免用同一批数据自证
        oos_dates = np.sort(oos["date"].unique())
        cut = max(1, int(len(oos_dates) * 0.6))
        fit_dates = set(oos_dates[:cut])
        eval_dates = set(oos_dates[cut:])
        fit_fn = primary_mod.cqr_offsets if calibration == "cqr" else primary_mod.conformal_offsets
        apply_fn = primary_mod.apply_cqr if calibration == "cqr" else primary_mod.apply_offsets
        fit_offsets = fit_fn(oos[oos["date"].isin(fit_dates)])
        eval_frame = apply_fn(oos[oos["date"].isin(eval_dates)], fit_offsets)
        calibration_eval = {
            "mode": calibration,
            "samples": int(fit_offsets.get("samples", 0)),
            "fit_offsets": {
                key: round(float(value), 6)
                for key, value in fit_offsets.items()
                if key != "samples"
            },
            "holdout_coverage": primary_mod.coverage_report(
                eval_frame[["date", "symbol", "P10", "P50", "P90"]], eval_frame["label"]
            ),
            "raw_coverage": primary_mod.coverage_report(
                oos[oos["date"].isin(eval_dates)][["date", "symbol", "P10", "P50", "P90"]],
                oos[oos["date"].isin(eval_dates)]["label"],
            ),
        }
        offsets = fit_fn(oos)

    coverage = primary_mod.coverage_report(oos[["date", "symbol", "P10", "P50", "P90"]], oos["label"])
    ic_stats = primary_mod.rank_ic(oos[["date", "symbol", "P50"]], oos["label"])
    independent_ic = primary_mod.nonoverlap_ic(oos[["date", "symbol", "P50", "label"]], days)
    bucket = primary_mod.top_bucket(oos[["date", "symbol", "P50"]], oos["label"])
    series = primary_mod.top_series(oos[["date", "symbol", "P50", "label"]])

    use_meta = variant.get("use_meta", "off")
    meta_gate = None
    meta_enabled = str(use_meta).lower() not in {"off", "false", "0", "none"}
    if meta_enabled:
        meta_frame = meta_mod.build_meta_dataset(
            oos[["date", "symbol", "P10", "P50", "P90"]], dataset
        )
        meta_model = meta_mod.fit_meta(meta_frame)
        meta_eval = (
            meta_mod.evaluate_meta(
                meta_mod.meta_probability(meta_model, meta_frame), meta_frame["up"]
            )
            if meta_model.available
            else {"auc": None, "samples": 0}
        )
        if str(use_meta).lower() == "auto":
            auc = meta_eval.get("auc") or 0.0
            ic_value = ic_stats.get("ic_mean") or 0.0
            meta_gate = {
                "auc": auc,
                "ic": ic_value,
                "enabled": bool(auc >= 0.55 and ic_value > 0),
            }
            if not meta_gate["enabled"]:
                meta_eval["status"] = "gated_off"
    else:
        meta_model = meta_mod.MetaModel(status="off")
        meta_eval = {"auc": None, "samples": 0, "status": "off"}

    latest_date = pd.Timestamp(panels.dates.max())
    latest_rows = pd.DataFrame({"date": latest_date, "symbol": panels.symbols})
    for name in representatives:
        latest_rows[name] = neutral[name].loc[latest_date].reindex(panels.symbols).to_numpy()
    latest_rows = latest_rows.dropna(subset=representatives)
    if latest_rows.empty:
        latest_date = pd.Timestamp(dataset["date"].max())
        latest_rows = dataset[dataset["date"] == latest_date].copy()
        if latest_rows.empty:
            raise ValueError("最新交易日没有可用特征，无法生成预测")

    embargo_cut = latest_date - timedelta(days=12)
    full_train = dataset[dataset["label_end_date"] <= embargo_cut]
    if len(full_train) < primary_mod.MIN_TRAIN_ROWS:
        full_train = dataset[dataset["label_end_date"] <= latest_date]
    final_model = primary_mod.fit_quantiles(
        full_train, representatives, seeds=int(variant.get("seeds", 1))
    )

    latest_pred = primary_mod.predict_quantiles(final_model, latest_rows)
    if offsets:
        latest_pred = (
            primary_mod.apply_offsets(latest_pred, offsets)
            if calibration == "conformal"
            else primary_mod.apply_cqr(latest_pred, offsets)
        )
    prediction_map = {
        str(row.symbol): {
            "P10": float(row.P10),
            "P50": float(row.P50),
            "P90": float(row.P90),
        }
        for row in latest_pred.itertuples(index=False)
    }
    latest_pred = latest_pred.set_index("symbol")
    meta_input = latest_pred.assign(
        P50_rank=latest_pred["P50"].rank(pct=True, method="average"),
        spread=latest_pred["P90"] - latest_pred["P10"],
    )
    if meta_enabled and (meta_gate is None or meta_gate.get("enabled")):
        probability = meta_mod.meta_probability(meta_model, meta_input)
        factors = meta_mod.multiplier(probability, preference)
    else:
        probability = pd.Series(0.5, index=latest_pred.index)
        factors = pd.Series(1.0, index=latest_pred.index)

    scale = 20.0 / days
    expected = (latest_pred["P50"] * factors * scale)
    expected = _shrink(expected).clip(-EXPECTED_CLIP, EXPECTED_CLIP)

    latest_tradable = tradable_all.loc[latest_date] if latest_date in tradable_all.index else pd.Series(True, index=expected.index)
    latest_sellable = sellable_all.loc[latest_date] if latest_date in sellable_all.index else pd.Series(True, index=expected.index)
    prices = panels.close.loc[latest_date]

    returns = panels.close.pct_change(fill_method=None)
    scenarios = portfolio_mod.monthly_scenarios(returns, window=400)
    scenarios = scenarios.reindex(columns=[column for column in expected.index if column in scenarios.columns])
    scenarios = scenarios.dropna(how="all")
    if scenarios.shape[0] < 20:
        scenarios = pd.DataFrame(
            np.zeros((20, len(expected))), columns=expected.index
        )
    # 超额基准必须来自候选全集，而不是已选中的子集
    scenario_baseline = scenarios.mean(axis=1)

    preferences = preferences_mod.ladder(horizon, positions=positions)
    portfolio_mode = str(variant.get("portfolio_mode", "optimize"))
    edge_ok = (ic_stats.get("ic_mean") or 0.0) >= 0.02
    if not edge_ok and portfolio_mode in {"optimize", "rank"}:
        portfolio_mode = "index"
        warnings_list.append(
            "样本外 IC 不足 0.02：已自动切换到等权分散模式，避免把噪声放大成集中风险"
        )
    variant["effective_portfolio_mode"] = portfolio_mode
    portfolios = [
        portfolio_mod.build_portfolio(
            expected,
            scenarios,
            item,
            mode=portfolio_mode,
            tradable=latest_tradable,
            prices=prices,
            capital=capital,
            industries=industry,
            baseline=scenario_baseline,
        )
        for item in preferences
    ]
    _step("组合求解")
    selected = portfolio_mod.build_portfolio(
        expected,
        scenarios,
        preference,
        mode=portfolio_mode,
        tradable=latest_tradable,
        prices=prices,
        capital=capital,
        industries=industry,
        baseline=scenario_baseline,
    )
    portfolios = portfolio_mod.enforce_monotonic_cvar(
        portfolios, scenarios, expected, baseline=scenario_baseline
    )
    selected.lots = portfolio_mod.round_to_lots(selected.weights, prices, capital)
    # 逐票明细：投资人可能只取其中一部分，前端据此计算"只买前 N 只"的仓位与现金
    selected_rows = []
    for rank, (symbol, weight) in enumerate(
        sorted(selected.weights.items(), key=lambda pair: -pair[1]), start=1
    ):
        mu = float(expected.get(symbol, 0.0) or 0.0)
        selected_rows.append(
            {
                "rank": rank,
                "symbol": symbol,
                "name": str(names.get(symbol, symbol)),
                "industry": str(industry.get(symbol, "—")),
                "weight": round(float(weight), 6),
                "lots": int(selected.lots.get(symbol, 0)),
                "expected": round(mu, 6),
            }
        )

    reject = panel_mod.reject_rate(
        tradable_all.loc[[latest_date]], panels.close.loc[[latest_date]].notna()
    )
    monotonic = portfolio_mod.monotonic_cvar(portfolios)

    holdings_input = pd.Series(holdings or {}, dtype=float)
    holdings_series = holdings_input.reindex(panels.symbols).fillna(0.0) if len(holdings_input) else holdings_input
    has_real_holdings = bool(len(holdings_input)) and float(holdings_series.sum()) > 0
    _step("持仓诊断")
    if holdings_series.any():
        diagnosis = diagnosis_mod.diagnose(
            holdings_series,
            preference,
            target=selected.weights,
            scenarios=scenarios,
            industries=industry,
            tradable=latest_sellable,
            names=names,
            expected=expected,
        )
        _step("生成建议")
        advice = advice_mod.build_advice(
            holdings_series,
            selected.weights,
            expected,
            preference,
            tradable=latest_tradable,
            prices=prices,
            names=names,
            industries=industry,
            capital=capital,
        )
    else:
        zero_weight = bool(holdings) and float(holdings_input.sum()) <= 0
        unmatched = bool(holdings) and not zero_weight and not holdings_series.any()
        headline = "空仓模式：未提供持仓，仅给出目标组合与建仓计划"
        if zero_weight:
            headline = "示例持仓权重合计为 0，按空仓处理：仅给出目标组合与建仓计划"
        elif unmatched:
            headline = "示例持仓未匹配到快照股票池，按空仓处理：仅给出目标组合与建仓计划"
        diagnosis = {
            "items": [],
            "red": 0,
            "yellow": 0,
            "headline": headline,
            "metrics": {},
        }
        advice = {
            "actions": [],
            "folded": [],
            "budget": 5,
            "safety_count": 0,
            "optimization_count": 0,
            "folded_count": 0,
            "post_check": {"feasible": True},
            "summary": {"buy": 0, "sell": 0, "hold": 0, "estimated_cost": 0.0, "estimated_improvement": 0.0},
        }

    display_symbols = [str(symbol) for symbol in holdings_series[holdings_series > 0].index[:20]]
    display_symbols += [str(symbol) for symbol in selected.weights.index[:20]]
    for symbol in expected.sort_values(ascending=False).index[:5]:
        if symbol not in display_symbols:
            display_symbols.append(str(symbol))
    attributions = _attributions(
        final_model,
        latest_rows,
        full_train,
        representatives,
        names,
        symbols=display_symbols,
    )

    data_quality = {
        "data_hash": _hash_snapshot(panels),
        "reject_rate": reject,
        "coverage": coverage,
        "capital_band": _capital_band(capital),
        "effective_sample": validation_mod.effective_sample(dates, days),
        "validation_mode": validation_mode,
        "industry": inputs.get("industry_stats"),
    }
    model_info = {
        "version": f"{MODEL_VERSION}/{final_model.backend}/{days}d/{len(representatives)}f",
        "backend": final_model.backend,
        "quantiles": [0.1, 0.5, 0.9],
        "train_rows": final_model.train_rows,
        "train_end": final_model.train_end,
        "meta": meta_model.diagnostics,
        "meta_status": meta_model.status,
    }

    effective = validation_mod.effective_sample(dates, days)
    warnings_list: list[str] = []
    if effective < 4:
        warnings_list.append(
            f"独立样本仅 {effective} 个（{days} 日标签），统计结论不稳定"
        )
    if (ic_stats.get("ic_mean") or 0.0) < 0.02:
        warnings_list.append(
            f"样本外 RankIC {ic_stats.get('ic_mean')}，未达到 0.02 有效门槛，建议仅作研究参考"
        )
    if not _coverage_ok(coverage):
        warnings_list.append("分位数覆盖率未落在 8-12% 区间，区间预测不可直接用于风险预算")
    if meta_eval.get("auc") is not None and meta_eval["auc"] < 0.55:
        warnings_list.append("元模型 AUC 低于 0.55，仓位乘数参考价值有限")
    if validation_mode == "holdout":
        warnings_list.append("样本不足以做滚动验证，本次使用单次留出验证")
    if days <= 5:
        warnings_list.append(
            "组合层回测显示 5 日档周频换手的年化成本约 2.6%–13.6%，"
            "高于当前信号强度，建议优先使用 20 日档"
        )
    calibration_ok = None
    if calibration in {"conformal", "cqr", "adaptive"}:
        holdout_cov = calibration_eval.get("holdout_coverage") or {}
        calibration_ok = _coverage_ok(holdout_cov)
        if not calibration_ok:
            warnings_list.append("区间校准后覆盖率仍未达标，区间预测仅供参考")

    pack = evidence_mod.build_evidence_pack(
        run_id=panels.run_id,
        as_of=str(latest_date.date()),
        preference=preference,
        portfolios=portfolios,
        holdings=selected.weights if not holdings_series.any() else holdings_series[holdings_series > 0],
        has_holdings=has_real_holdings,
        predictions=prediction_map,
        meta_probability=probability,
        multipliers=factors,
        tradable=latest_tradable,
        names=names,
        attributions=attributions,
        diagnosis=diagnosis,
        advice=advice,
        model_info=model_info,
        data_quality=data_quality,
        budget_info={"budget": advice.get("budget", 5)},
    )

    if warnings_list:
        pack["limits"] = warnings_list + list(pack.get("limits", []))
    try:
        pack_size = evidence_mod.assert_size(pack)
    except ValueError as exc:
        pack_size = {"tokens": None, "error": str(exc)}

    _step("组装证据包")
    report = None
    if with_report:
        report = report_mod.generate(pack, client=client).as_dict()

    effective_coverage_ok = (
        calibration_ok
        if calibration in {"conformal", "cqr", "adaptive"} and calibration_ok is not None
        else _coverage_ok(coverage)
    )
    acceptance = {
        "coverage_ok": effective_coverage_ok,
        "meta_auc_ok": (
            None
            if meta_eval.get("status") == "off"
            else (meta_eval.get("auc") or 0.0) >= 0.55
        ),
        "cvar_monotonic": monotonic["monotonic"],
        "reject_rate_ok": reject <= 0.25,
        "evidence_size_ok": pack_size.get("tokens") is not None,
        "leakage_free": bool((dataset["label_end_date"] > dataset["date"]).all()),
        "ic_ok": (ic_stats.get("ic_mean") or 0.0) >= 0.02,
        "calibration_ok": calibration_ok,
    }

    payload = {
        "run_id": panels.run_id,
        "as_of": str(latest_date.date()),
        "preference": preference.as_dict(),
        "preference_label": preference.label,
        "panel": panel_mod.panel_summary(panels),
        "features": {
            "available": stock_pool,
            "selected": representatives,
            "clusters": cluster_table.to_dict(orient="records"),
        },
        "model": model_info,
        "variant": variant,
        "metrics": {
            "coverage": coverage,
            "rank_ic": ic_stats,
            "independent_ic": independent_ic,
            "top_bucket": bucket,
            "top_series": {
                str(pd.Timestamp(date).date()): round(float(value), 6)
                for date, value in series.items()
            },
            "meta": meta_eval,
            "meta_gate": meta_gate,
            "calibration": calibration_eval,
            "folds": fold_log,
            "validation_mode": validation_mode,
            "effective_sample": effective,
        },
        "warnings": warnings_list,
        "dropped_features": dropped_features,
        "ladder": portfolio_mod.ladder_table(portfolios).to_dict(orient="records"),
        "monotonic": monotonic,
        "selected": {
            "label": selected.label,
            "rows": selected_rows,
            "weights": {symbol: round(float(value), 6) for symbol, value in selected.weights.items()},
            "lots": {symbol: int(value) for symbol, value in selected.lots.items()},
            "summary": selected.summary(),
            "cash_weight": round(float(selected.cash_weight), 4),
        },
        "diagnosis": diagnosis,
        "advice": advice,
        "names": {
            symbol: str(names.get(symbol, symbol))
            for symbol in sorted(
                set(getattr(selected.weights, "index", selected.weights))
                | set(holdings or {})
            )
        },
        "evidence": pack,
        "evidence_size": pack_size,
        "report": report,
        "acceptance": acceptance,
        "cluster_count": int(cluster_table["cluster"].nunique()),
    }
    return RunResult(payload=payload)


def _capital_band(capital: float) -> str:
    if capital <= 0:
        return "未提供"
    if capital < 100_000:
        return "10 万以下"
    if capital < 500_000:
        return "10-50 万"
    if capital < 2_000_000:
        return "50-200 万"
    return "200 万以上"


def _hash_snapshot(panels) -> str:
    return evidence_mod.data_hash(
        f"{panels.run_id}|{len(panels.symbols)}|{panels.dates.min()}|{panels.dates.max()}"
    )
