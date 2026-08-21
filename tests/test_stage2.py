import pandas as pd
import pytest

from memory.strategy_store import StrategyStore
from memory.text_memory import TextMemoryStore
from tools import factor_research_tool, factor_tool, reflection_tool, risk_tool


def test_text_memory_fallback_search(tmp_path):
    store = TextMemoryStore(path=tmp_path / "memory_text.json", chroma_dir=tmp_path / "chroma")
    store.add_document("20日动量选股策略，月度调仓，等权持有前20只")
    store.add_document("沪深300 价值因子低估值策略")
    hits = store.search("动量月度调仓策略", k=3, threshold=0.0)
    assert hits
    assert "动量" in hits[0]["text"]


def test_strategy_store_factor_upsert(tmp_path):
    store = StrategyStore(db_path=tmp_path / "memory.db")
    store.upsert_factor_stats(
        factor_id="momentum_20",
        factor_name="20日动量",
        factor_type="technical",
        ic_mean=0.04,
        ic_ir=0.35,
        half_life_days=120.0,
        ic_win_rate=0.55,
        in_sample_ic=0.05,
        out_sample_ic=0.01,
    )
    df = store.query_factor_library()
    assert len(df) == 1
    assert df.iloc[0]["ic_mean"] == pytest.approx(0.04)


def test_factor_research_basic(tmp_path):
    from tools.data_tool import clean_panel, get_daily_price

    panel = clean_panel(
        get_daily_price(["S1000", "S1001", "S1002", "S1003", "S1004"],
                        "2023-01-01", "2023-12-31", provider="synthetic")
    )
    price = panel["close"].unstack("symbol")
    result = factor_research_tool.evaluate_factors(
        panel, price, factor_ids=["momentum_20", "turnover"], horizons=(1, 5)
    )
    assert {"momentum_20", "turnover"} <= set(result.summary["factor_id"])
    assert "ic_mean" in result.summary.columns
    assert any(key.startswith("momentum_20_h") for key in result.ic_series)


def test_reflection_diagnoses_cost_and_drawdown():
    metrics = {
        "annual_return": 0.05,
        "benchmark_annual_return": 0.04,
        "max_drawdown": -0.22,
        "annual_turnover": 2.5,
        "sharpe_ratio": 0.4,
        "calmar_ratio": 0.2,
    }
    diags = reflection_tool.diagnose_strategy(metrics)
    dimensions = {d.dimension for d in diags}
    assert {"成本", "风险"} <= dimensions


def test_risk_exposure_tables():
    weights = pd.DataFrame(
        {"A": [0.6], "B": [0.4]}, index=pd.DatetimeIndex(["2023-01-02"])
    )
    sector = pd.Series({"A": "银行", "B": "医药"})
    sector_df = risk_tool.compute_sector_exposure(weights, sector)
    assert sector_df.loc[0, "weight"] == pytest.approx(0.6)

    factor_matrix = pd.DataFrame(
        {"A": [1.0], "B": [-0.5]}, index=pd.DatetimeIndex(["2023-01-02"])
    )
    style_df = risk_tool.compute_style_exposure(weights, factor_matrix, factor_id="momentum_20")
    assert style_df.loc[0, "exposure"] == pytest.approx(0.4)


def test_factor_registry_has_classic_categories():
    factors = factor_tool.list_factors()
    assert len(factors) >= 10
    assert set(factors["type"]) >= {"technical", "value", "quality"}
