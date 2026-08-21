import numpy as np
import pandas as pd
import pytest

from tools.performance_tool import compute_annual_turnover, compute_metrics


def test_metrics_annual_return_manual():
    nav = pd.Series([1.0, 1.1, 1.21, 1.331], index=pd.bdate_range("2023-01-02", periods=4))
    metrics = compute_metrics(nav, risk_free_rate=0.0, periods_per_year=3)
    # 4 个净值点 = 3 期，年化 = 1.331^(3/3) - 1 = 33.1%
    assert metrics["annual_return"] == pytest.approx(0.331)
    assert metrics["cumulative_return"] == pytest.approx(0.331)


def test_max_drawdown_manual():
    nav = pd.Series([1.0, 1.2, 0.9, 1.0])
    metrics = compute_metrics(nav, risk_free_rate=0.0, periods_per_year=3)
    assert metrics["max_drawdown"] == pytest.approx(-0.25)


def test_info_ratio_with_identical_excess():
    dates = pd.bdate_range("2023-01-02", periods=50)
    nav = pd.Series(np.linspace(1.0, 1.5, 50), index=dates)
    benchmark = pd.Series(np.linspace(1.0, 1.3, 50), index=dates)
    metrics = compute_metrics(nav, benchmark_nav=benchmark, risk_free_rate=0.0)
    assert metrics["excess_annual_return"] > 0


def test_annual_turnover():
    turnover = pd.Series([1.0] * 12, index=pd.date_range("2023-01-31", periods=12, freq="ME"))
    dates = pd.DatetimeIndex(["2023-01-01", "2023-12-31"])
    annual = compute_annual_turnover(turnover, dates)
    assert annual == pytest.approx(12.0, rel=0.05)
