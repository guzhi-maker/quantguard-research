"""v2 研究流水线的验收测试。

覆盖六条不变量与关键算法：标签不越界、偏好不进模型、
CVaR 阶梯单调、建议预算与安全豁免、证据包规模、报告校验与回退。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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


def _synthetic_panel(symbols: int = 60, days: int = 200, seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=days)
    drift = rng.normal(0.0004, 0.0006, symbols)
    market = rng.normal(0.0002, 0.008, days)
    noise = rng.normal(0.0, 0.015, (days, symbols))
    returns = drift[None, :] + market[:, None] + noise
    close = pd.DataFrame(100 * np.cumprod(1 + returns, axis=0), index=dates, columns=[f"{i:06d}" for i in range(symbols)])
    amount = pd.DataFrame(rng.lognormal(18, 0.5, (days, symbols)), index=dates, columns=close.columns)
    volume = amount / close
    turnover = pd.DataFrame(rng.uniform(0.005, 0.05, (days, symbols)), index=dates, columns=close.columns)
    meta = pd.DataFrame(
        {
            "name": [f"股票{i}" for i in range(symbols)],
            "industry": ["行业A" if i % 2 == 0 else "行业B" for i in range(symbols)],
        },
        index=close.columns,
    )
    panels = panel_mod.Panels(
        close=close,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        volume=volume,
        amount=amount,
        change_pct=close.pct_change().fillna(0.0) * 100,
        turnover=turnover,
        meta=meta,
        run_id="TEST-SYNTHETIC",
    )
    return panels


@pytest.fixture(scope="module")
def panels():
    return _synthetic_panel()


def test_preferences_interpolate_and_stay_monotonic():
    levels = [preferences_mod.resolve(risk).risk["cvar95"] for risk in (1, 3, 5, 7, 10)]
    assert levels == sorted(levels)
    ladder = preferences_mod.ladder(3)
    assert len(ladder) == 5
    assert ladder[0].risk["cvar95"] < ladder[-1].risk["cvar95"]
    assert preferences_mod.resolve(11).risk_level == 10.0
    assert preferences_mod.resolve(0).risk_level == 1.0
    assert preferences_mod.suggest_tier(0.04)["risk_level"] < preferences_mod.suggest_tier(0.18)["risk_level"]


def test_positions_default_and_override():
    balanced = preferences_mod.resolve(5.5, 2)
    assert balanced.risk["positions"] == 15  # 平衡档默认 15 只
    forced = preferences_mod.resolve(1.0, 2, positions=12)
    assert forced.risk["positions"] == 12
    ladder = preferences_mod.ladder(2, positions=8)
    assert [item.risk["positions"] for item in ladder] == [8, 8, 8, 8, 8]
    assert [item.risk["positions"] for item in preferences_mod.ladder(2)] == [50, 30, 15, 10, 6]


def test_horizon_controls_label_and_band():
    short = preferences_mod.resolve(5, 1)
    middle = preferences_mod.resolve(5, 2)
    long = preferences_mod.resolve(5, 3)
    assert short.horizon["days"] == 5
    assert middle.horizon["days"] == 20
    assert long.horizon["days"] == 60
    assert short.horizon["band_pp"] < middle.horizon["band_pp"] < long.horizon["band_pp"]
    assert short.horizon["turnover_cap"] > middle.horizon["turnover_cap"] > long.horizon["turnover_cap"]


def test_horizon_only_three_tiers():
    assert preferences_mod.HORIZON_MAX == 3
    assert [item["days"] for item in preferences_mod.horizon_options()] == [5, 20, 60]
    assert preferences_mod.resolve(5, 2).horizon["days"] == 20


def test_labels_never_look_ahead(panels):
    industry = panels.meta["industry"]
    labels, ends = labels_mod.build_label_panel(panels.close, industry, 20)
    frame = labels_mod.to_long(labels, {"f": panels.close.pct_change().fillna(0.0)}, ends, ["f"])
    assert not frame.empty
    assert (frame["label_end_date"] > frame["date"]).all()
    mask = labels_mod.training_mask(frame["date"].unique(), pd.Timestamp("2024-06-28"), embargo=5)
    assert not mask.loc[pd.Timestamp("2024-06-28")]


def test_tradability_blocks_limit_up_and_zero_volume(panels):
    close = panels.close.copy()
    change = panels.change_pct.copy()
    high = panels.high.copy()
    low = panels.low.copy()
    volume = panels.volume.copy()
    symbol = close.columns[0]
    date = close.index[100]
    high.loc[date, symbol] = 10.0
    low.loc[date, symbol] = 10.0
    close.loc[date, symbol] = 10.0
    change.loc[date, symbol] = 10.0
    volume.loc[date, close.columns[1]] = 0.0

    panels_modified = panel_mod.Panels(
        close=close,
        open=panels.open,
        high=high,
        low=low,
        volume=volume,
        amount=panels.amount,
        change_pct=change,
        turnover=panels.turnover,
        meta=panels.meta,
        run_id=panels.run_id,
    )
    tradable = panel_mod.tradability(panels_modified)
    assert not bool(tradable.loc[date, symbol])
    assert not bool(tradable.loc[date, close.columns[1]])


def test_ic_and_neutralization(panels):
    industry = panels.meta["industry"]
    forward = labels_mod.forward_returns(panels.close, 20)
    factor = forward.copy()
    ic = features_mod.ic_series(factor, forward).dropna()
    assert ic.mean() > 0.5

    size = features_mod.size_proxy(panels.amount)
    contaminated = size / size.mean()
    neutral = features_mod.neutralize(contaminated, industry, size)
    # 中性化检验的是截面暴露：逐日计算的秩相关应接近 0
    correlations = []
    for date in neutral.index:
        row = neutral.loc[date]
        log_size = np.log(size.loc[date])
        mask = row.notna() & log_size.notna()
        if mask.sum() >= 10 and row[mask].nunique() > 2:
            correlations.append(row[mask].corr(log_size[mask], method="spearman"))
    assert abs(float(np.mean(correlations))) < 0.2


def test_primary_quantiles_are_ordered_and_calibrated(panels):
    industry = panels.meta["industry"]
    days = 20
    labels, ends = labels_mod.build_label_panel(panels.close, industry, days)
    feature = features_mod.neutralize(
        features_mod.build_features(panels.close, panels.amount, panels.volume)["momentum_20"],
        industry,
        features_mod.size_proxy(panels.amount),
    )
    dataset = labels_mod.to_long(labels, {"momentum_20": feature}, ends, ["momentum_20"]).dropna()
    dates = np.sort(dataset["date"].unique())
    split = int(len(dates) * 0.6)
    train = dataset[dataset["date"].isin(dates[:split])]
    test = dataset[dataset["date"].isin(dates[split:])]
    model = primary_mod.fit_quantiles(train, ["momentum_20"])
    predictions = primary_mod.predict_quantiles(model, test).merge(
        dataset[["date", "symbol", "label"]], on=["date", "symbol"], how="left"
    )
    assert (predictions["P10"] <= predictions["P50"]).all()
    assert (predictions["P50"] <= predictions["P90"]).all()
    coverage = primary_mod.coverage_report(predictions, predictions["label"])
    assert coverage["cross_rate"] == 0
    assert 0.0 <= coverage["p10_breach"] <= 0.3
    assert 0.0 <= coverage["p90_breach"] <= 0.3


def test_portfolio_ladder_cvar_is_monotonic(panels):
    rng = np.random.default_rng(11)
    scenarios = pd.DataFrame(rng.normal(0, 0.06, (120, len(panels.symbols))), columns=panels.symbols)
    expected = pd.Series(rng.normal(0.01, 0.01, len(panels.symbols)), index=panels.symbols)
    portfolios = portfolio_mod.build_ladder(
        expected,
        scenarios,
        preferences_mod.ladder(3),
        capital=1_000_000.0,
        prices=panels.close.iloc[-1],
    )
    cvar_values = [portfolio.cvar95 for portfolio in portfolios]
    assert cvar_values == sorted(cvar_values)
    assert portfolio_mod.monotonic_cvar(portfolios)["monotonic"]
    for portfolio in portfolios:
        assert portfolio.weights.sum() <= 1.0 + 1e-9
        assert (portfolio.weights <= portfolio_mod.MIN_POSITION * 100).all()


def test_lots_are_whole_hundreds():
    weights = pd.Series({"a": 0.5, "b": 0.3})
    prices = pd.Series({"a": 10.0, "b": 300.0})
    lots = portfolio_mod.round_to_lots(weights, prices, 100_000.0)
    assert lots["a"] == 50
    assert lots["b"] == 1


def test_meta_requires_out_of_sample_predictions(panels):
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2024-01-02", periods=60)
    rows = []
    for date in dates:
        for symbol in panels.symbols[:30]:
            value = rng.normal(0.01, 0.05)
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "P10": value - 0.05,
                    "P50": value,
                    "P90": value + 0.05,
                    "label": value + rng.normal(0, 0.01),
                }
            )
    labeled = pd.DataFrame(rows)
    frame = meta_mod.build_meta_dataset(labeled[["date", "symbol", "P10", "P50", "P90"]], labeled)
    assert "up" in frame.columns and "P50_rank" in frame.columns
    order = frame["P50_rank"].between(0, 1).all()
    assert order


def test_advice_budget_and_safety_exemption():
    preference = preferences_mod.resolve(5.5, 3)
    symbols = [f"{index:06d}" for index in range(20)]
    holdings = pd.Series({symbols[i]: 0.05 for i in range(10)})
    target = pd.Series({symbols[i]: 0.08 for i in range(15)})
    expected = pd.Series({symbol: 0.02 for symbol in symbols})
    result = advice_mod.build_advice(holdings, target, expected, preference, capital=1_000_000.0)
    optimization = [action for action in result["actions"] if not action["safety"]]
    assert len(optimization) <= result["budget"]
    assert result["budget"] <= 5

    # 安全类不占预算：制造超限持仓
    heavy = pd.Series({symbols[0]: 0.30})
    result_safety = advice_mod.build_advice(heavy, target, expected, preference)
    safety = [action for action in result_safety["actions"] if action["safety"]]
    assert safety, "超限持仓必须触发安全类建议"
    assert len([a for a in result_safety["actions"] if not a["safety"]]) <= result_safety["budget"]


def test_diagnosis_flags_compliance_red():
    preference = preferences_mod.resolve(1.0, 3)
    holdings = pd.Series({"000001": 0.30, "000002": 0.10})
    industries = pd.Series({"000001": "行业A", "000002": "行业B"})
    report = diagnosis_mod.diagnose(holdings, preference, industries=industries)
    compliance = next(item for item in report["items"] if item["key"] == "compliance")
    assert compliance["status"] == "red"
    assert report["red"] >= 1


def test_evidence_pack_stays_small_and_has_six_blocks():
    pack = evidence_mod.build_evidence_pack(
        run_id="TEST",
        as_of="2024-06-28",
        preference=preferences_mod.resolve(5.5, 3),
        portfolios=[],
        holdings=pd.Series(dtype=float),
        predictions={},
        meta_probability=pd.Series(dtype=float),
        multipliers=pd.Series(dtype=float),
        tradable=pd.Series(dtype=bool),
        names=pd.Series(dtype=object),
        attributions={},
        diagnosis={"items": [], "red": 0, "yellow": 0, "headline": "无持仓"},
        advice={"actions": [], "budget": 5, "optimization_count": 0, "folded_count": 0, "post_check": {}},
        model_info={"version": "test"},
        data_quality={"data_hash": "abcd1234", "coverage": {}},
        budget_info={"budget": 5},
    )
    for block in ("meta", "user", "portfolio", "stocks", "diagnosis", "advice", "limits"):
        assert block in pack
    size = evidence_mod.assert_size(pack)
    assert size["tokens"] < evidence_mod.TOKEN_LIMIT


def test_report_validation_rejects_fabricated_numbers():
    pack = evidence_mod.build_evidence_pack(
        run_id="TEST",
        as_of="2024-06-28",
        preference=preferences_mod.resolve(5.5, 3),
        portfolios=[],
        holdings=pd.Series(dtype=float),
        predictions={},
        meta_probability=pd.Series(dtype=float),
        multipliers=pd.Series(dtype=float),
        tradable=pd.Series(dtype=bool),
        names=pd.Series(dtype=object),
        attributions={},
        diagnosis={"items": [], "red": 0, "yellow": 0, "headline": "无持仓"},
        advice={"actions": [], "budget": 5, "optimization_count": 0, "folded_count": 0, "post_check": {}},
        model_info={"version": "test"},
        data_quality={"data_hash": "abcd1234", "coverage": {}},
        budget_info={"budget": 5},
    )
    text = (
        "总体结论：组合正常。组合层面权衡：保守档。个股重点：无。"
        "下一步应核对事项：无。数据限制：仅课程研究，不构成投资建议。"
        "预期收益 87.65%。"
    )
    issues = report_mod.validate_report(text, pack)
    assert any("数字" in issue for issue in issues)
    issues_missing = report_mod.validate_report("总体结论：正常。", pack)
    assert any("段落" in issue for issue in issues_missing)

    fallback = report_mod.fallback_report(pack, "测试回退")
    assert fallback.source == "template"
    for section in report_mod.SECTIONS:
        assert section in fallback.text


def test_report_generate_without_key_falls_back():
    pack = {
        "meta": {"prompt_version": evidence_mod.PROMPT_VERSION},
        "user": {},
        "portfolio": {"ladder": []},
        "stocks": [],
        "diagnosis": {"headline": "无持仓"},
        "advice": {"actions": [], "folded": 0},
        "limits": ["仅课程研究，不构成投资建议"],
    }

    class Client:
        configured = False
        model = "test"

    report = report_mod.generate(pack, client=Client())
    assert report.source == "template"
    assert report.error


def test_validation_tools():
    high = validation_mod.deflated_sharpe(sharpe=2.0, observations=250, trials=10)
    low = validation_mod.deflated_sharpe(sharpe=0.2, observations=250, trials=10)
    assert high["dsr"] > low["dsr"]
    returns = pd.DataFrame(
        np.random.default_rng(5).normal(0.001, 0.02, (24, 4)),
        columns=list("abcd"),
    )
    result = validation_mod.pbo(returns, n_blocks=4, samples=50)
    assert result["pbo"] is None or 0.0 <= result["pbo"] <= 1.0
    folds = validation_mod.walk_forward_folds(pd.bdate_range("2024-01-02", periods=40), n_folds=3)
    assert len(folds) == 3


def test_label_transform_rank_and_winsor(panels):
    industry = panels.meta["industry"]
    base, ends = labels_mod.build_label_panel(panels.close, industry, 20)
    rank, _ = labels_mod.build_label_panel(panels.close, industry, 20, transform="rank")
    winsor, _ = labels_mod.build_label_panel(panels.close, industry, 20, transform="winsor")
    valid = rank.dropna(how="all")
    row = valid.iloc[-1].dropna()
    assert row.min() >= -0.5 and row.max() <= 0.5
    assert winsor.abs().max().max() <= base.abs().max().max() + 1e-9
    with pytest.raises(ValueError):
        labels_mod.transform_labels(base, "unknown")


def test_drop_constant_filters_low_coverage(panels):
    features = features_mod.build_features(
        panels.close, panels.amount, panels.volume, panels.turnover
    )
    broken = pd.DataFrame(np.nan, index=panels.close.index, columns=panels.close.columns)
    features["broken_feature"] = broken
    keep, dropped = features_mod.drop_constant(features, list(features))
    assert "broken_feature" in dropped
    assert "momentum_20" in keep


def test_conformal_offsets_reduce_coverage_error(panels):
    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2024-01-02", periods=60)
    rows = []
    for date in dates:
        for symbol in panels.symbols[:20]:
            actual = rng.normal(0.0, 0.05)
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "P10": actual - rng.normal(0.02, 0.01),
                    "P50": actual,
                    "P90": actual + rng.normal(0.02, 0.01),
                    "label": actual,
                }
            )
    frame = pd.DataFrame(rows)
    before = primary_mod.coverage_report(frame, frame["label"])
    offsets = primary_mod.conformal_offsets(frame)
    adjusted = primary_mod.apply_offsets(frame, offsets)
    after = primary_mod.coverage_report(adjusted, adjusted["label"])
    assert (after["p10_breach"] <= before["p10_breach"] + 0.05) or after["p10_breach"] < 0.15
    assert offsets["samples"] > 0


def test_seed_ensemble_predicts(panels):
    industry = panels.meta["industry"]
    labels, ends = labels_mod.build_label_panel(panels.close, industry, 20)
    feature = features_mod.neutralize(
        features_mod.build_features(panels.close, panels.amount, panels.volume)["momentum_20"],
        industry,
        features_mod.size_proxy(panels.amount),
    )
    dataset = labels_mod.to_long(labels, {"momentum_20": feature}, ends, ["momentum_20"]).dropna()
    model = primary_mod.fit_quantiles(dataset, ["momentum_20"], seeds=3)
    predictions = primary_mod.predict_quantiles(model, dataset.head(50))
    assert model.seeds == 3
    assert len(predictions) == 50
    assert (predictions["P10"] <= predictions["P90"]).all()


def test_rank_portfolio_respects_caps_and_positions(panels):
    rng = np.random.default_rng(13)
    scenarios = pd.DataFrame(rng.normal(0, 0.06, (120, len(panels.symbols))), columns=panels.symbols)
    expected = pd.Series(rng.normal(0.01, 0.01, len(panels.symbols)), index=panels.symbols)
    preference = preferences_mod.resolve(5.5, 3)
    portfolio = portfolio_mod.select_rank_portfolio(
        expected,
        scenarios,
        preference,
        industries=panels.meta["industry"],
    )
    assert portfolio.weights.sum() <= 1.0 + 1e-9
    assert (portfolio.weights <= preference.risk["single_cap"] + 1e-9).all()
    assert len(portfolio.weights) <= int(preference.risk["positions"])


def test_variant_defaults_and_override():
    from research import pipeline as pipeline_mod

    variant = pipeline_mod.make_variant(name="x", seeds=5, label="rank")
    assert variant["name"] == "x"
    assert variant["seeds"] == 5
    assert variant["label"] == "rank"
    assert variant["portfolio_mode"] == "rank"

    default = pipeline_mod.make_variant()
    assert default["feature_mode"] == "extended"
    assert default["feature_set"] == "whitelist"
    assert default["label_baseline"] == "industry"
    assert default["calibration"] == "adaptive"
    assert default["use_meta"] == "off"
    assert default["portfolio_mode"] == "rank"
    assert default["seeds"] == 3


def test_enforce_monotonic_cvar_raises_low_tiers():
    rng = np.random.default_rng(21)
    symbols = [f"{index:06d}" for index in range(12)]
    scenarios = pd.DataFrame(rng.normal(0, 0.05, (80, len(symbols))), columns=symbols)
    expected = pd.Series(0.01, index=symbols)
    preference = preferences_mod.resolve(5.5, 3)

    def make(weight: float):
        weights = pd.Series(weight, index=symbols[:4])
        return portfolio_mod._finalize(
            weights, expected, scenarios, portfolio_mod.CostConfig(), preference
        )

    low = make(0.25)
    high = make(0.10)
    assert low.cvar95 > high.cvar95  # 故意构造非单调
    fixed = portfolio_mod.enforce_monotonic_cvar([low, high], scenarios, expected)
    assert fixed[0].cvar95 < fixed[1].cvar95


def test_backtest_nav_and_costs(panels):
    from config.settings import CostConfig

    from research import backtest as backtest_mod
    from research import scoring as scoring_mod

    industry = panels.meta["industry"]
    labels, ends = labels_mod.build_label_panel(panels.close, industry, 20)
    feature = features_mod.neutralize(
        features_mod.build_features(panels.close, panels.amount, panels.volume)["momentum_20"],
        industry,
        features_mod.size_proxy(panels.amount),
    )
    dataset = labels_mod.to_long(labels, {"momentum_20": feature}, ends, ["momentum_20"]).dropna()
    scores = scoring_mod.rolling_scores(
        dataset, ["momentum_20"], refit_every=40, seeds=1, min_train=500
    )
    assert not scores.empty

    preference = preferences_mod.resolve(5.5, 3)
    free = CostConfig(
        commission_rate=0.0,
        min_commission=0.0,
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
    )
    baseline = backtest_mod.run_portfolio_backtest(scores, panels.close, preference)
    cheap = backtest_mod.run_portfolio_backtest(scores, panels.close, preference, costs=free)
    assert len(baseline.nav) >= 2
    # 首个净值已扣建仓成本，因此略低于 1
    assert 0.9 < baseline.nav.iloc[0] <= 1.0 + 1e-9
    assert cheap.nav.iloc[-1] >= baseline.nav.iloc[-1] - 1e-9
    assert baseline.metrics["rebalances"] >= 1


def test_backtest_buffer_reduces_turnover(panels):
    from research import backtest as backtest_mod

    rng = np.random.default_rng(31)
    dates = pd.bdate_range("2024-01-02", periods=60)
    rows = []
    for date in dates:
        for symbol in panels.symbols:
            rows.append({"date": date, "symbol": symbol, "P50": rng.normal(0, 0.02)})
    scores = pd.DataFrame(rows)
    preference = preferences_mod.resolve(5.5, 3)

    buffered = backtest_mod.run_portfolio_backtest(scores, panels.close, preference, use_buffer=True)
    raw = backtest_mod.run_portfolio_backtest(scores, panels.close, preference, use_buffer=False)
    assert sum(record["turnover"] for record in buffered.rebalances) <= (
        sum(record["turnover"] for record in raw.rebalances) + 1e-6
    )


def test_cqr_restores_interval_coverage():
    rng = np.random.default_rng(41)
    dates = pd.bdate_range("2024-01-02", periods=80)
    rows = []
    for date in dates:
        for symbol in [f"{index:06d}" for index in range(15)]:
            actual = rng.normal(0.0, 0.08)
            # 故意构造过窄区间：上下界都向中位数收缩
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "P10": actual - 0.01 + rng.normal(0.0, 0.03),
                    "P50": actual,
                    "P90": actual + 0.01 + rng.normal(0.0, 0.03),
                    "label": actual,
                }
            )
    frame = pd.DataFrame(rows)
    before = primary_mod.coverage_report(frame, frame["label"])
    offsets = primary_mod.cqr_offsets(frame)
    after = primary_mod.coverage_report(primary_mod.apply_cqr(frame, offsets), frame["label"])
    assert before["p10_breach"] > 0.25
    assert after["p10_breach"] < before["p10_breach"]
    assert offsets["samples"] > 0


def test_adaptive_calibration_restores_coverage():
    rng = np.random.default_rng(51)
    dates = pd.bdate_range("2024-01-02", periods=160)
    rows = []
    for position, date in enumerate(dates):
        scale = 1.0 + position / 160.0  # 波动随时间放大，模拟行情切换
        for symbol in [f"{index:06d}" for index in range(12)]:
            actual = rng.normal(0.0, 0.06 * scale)
            # 预测带有明显误差，区间又给得过窄，因此破坏率会很高
            forecast = actual + rng.normal(0.0, 0.09 * scale)
            half_width = 0.05 * scale
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "P10": forecast - half_width,
                    "P50": forecast,
                    "P90": forecast + half_width,
                    "label": actual,
                }
            )
    frame = pd.DataFrame(rows)
    raw = primary_mod.coverage_report(frame, frame["label"])
    causal = primary_mod.causal_coverage(frame)
    assert raw["p10_breach"] > 0.12
    assert 0.04 <= causal["p10_breach"] <= 0.16
    assert 0.04 <= causal["p90_breach"] <= 0.16


def test_pipeline_end_to_end_when_snapshot_exists():
    runs = panel_mod.list_runs()
    if not runs:
        pytest.skip("没有原始快照，跳过端到端测试")
    from research import pipeline as pipeline_mod

    # 挑一个较小的快照跑通全链路，避免在 200 万行快照上把测试拖到十几分钟
    small_runs = []
    for run in runs:
        daily = Path("data/raw") / run / "market_daily.csv"
        if daily.exists() and 1_000_000 < daily.stat().st_size < 40_000_000:
            small_runs.append(run)
    target_run = small_runs[-1] if small_runs else runs[-1]

    variant = pipeline_mod.make_variant(seeds=1)
    result = pipeline_mod.run_research(
        run_id=target_run, risk=5.5, horizon=2, variant=variant
    ).as_dict()
    assert result["acceptance"]["leakage_free"]
    assert result["acceptance"]["cvar_monotonic"]
    assert len(result["ladder"]) == 5
    assert result["evidence_size"]["tokens"] < evidence_mod.TOKEN_LIMIT
    assert result["selected"]["weights"]
    assert result["metrics"]["independent_ic"]["samples"] >= 1
