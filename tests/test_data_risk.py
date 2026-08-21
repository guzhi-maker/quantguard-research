import pandas as pd
import pytest

from tools.data_tool import clean_panel, get_daily_price, get_index_constituents
from tools.risk_tool import evaluate_backtest, screen_universe, validate_drawdown, validate_position_limits


def test_synthetic_panel_is_deterministic():
    panel1 = get_daily_price(["S1000"], "2023-01-01", "2023-03-31", provider="synthetic")
    panel2 = get_daily_price(["S1000"], "2023-01-01", "2023-03-31", provider="synthetic")
    pd.testing.assert_frame_equal(panel1, panel2)


def test_clean_panel_marks_st():
    panel = get_daily_price("S1000", "2023-01-01", "2023-03-31", provider="synthetic")
    cleaned = clean_panel(panel)
    assert "is_st" in cleaned.columns
    assert cleaned.index.names == ["date", "symbol"]


def test_index_constituents_synthetic_point_in_time():
    dec = get_index_constituents("000300", "2019-12-31", provider="synthetic")
    mar = get_index_constituents("000300", "2020-03-31", provider="synthetic")
    assert len(dec) >= 20
    assert set(dec.columns) >= {"symbol", "name", "sector"}
    # 确定性轮换下，两个时点成分应基本一致但不要求完全相同
    assert len(set(dec["symbol"]) & set(mar["symbol"])) > 10


def test_screen_universe_drops_st():
    panel = get_daily_price(["S1000", "S1017"], "2023-01-01", "2023-01-31", provider="synthetic")
    assert panel["is_st"].any()
    screened = screen_universe(panel)
    assert not screened["is_st"].any()


def test_risk_position_limit():
    weights = pd.DataFrame({"A": [0.20]}, index=pd.DatetimeIndex(["2023-01-02"]))
    report = validate_position_limits(weights)
    assert not report.passed
    assert "单票" in report.violations[0]


def test_risk_drawdown_levels():
    warn = validate_drawdown(-0.25)
    circuit = validate_drawdown(-0.35)
    assert warn.passed and warn.warnings
    assert not circuit.passed
