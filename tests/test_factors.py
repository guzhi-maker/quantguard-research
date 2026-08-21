import numpy as np
import pandas as pd
import pytest

from tools.factor_tool import (
    compute_factor_matrix,
    cross_sectional_zscore,
    momentum,
    point_in_time_financial_factor,
    volatility,
)


def test_momentum_manual():
    close = pd.Series(np.arange(1, 31, dtype=float))
    result = momentum(close, window=20)
    # 第21个交易日：close[20] / close[0] - 1 = 21 / 1 - 1 = 20
    assert result.iloc[20] == pytest.approx(20.0)
    assert result.iloc[:20].isna().all()


def test_volatility_constant_returns_is_zero():
    close = pd.Series(np.linspace(10, 20, 100))
    close = pd.Series(np.exp(np.linspace(0, 0.1, 100)))
    result = volatility(close, window=20)
    assert result.dropna().abs().max() < 1e-9


def test_cross_sectional_zscore_manual():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = cross_sectional_zscore(series, winsorize=3.0)
    expected = pd.Series([-1.41421356, -0.70710678, 0.0, 0.70710678, 1.41421356])
    pd.testing.assert_series_equal(result, expected, check_exact=False, rtol=1e-5)


def test_factor_matrix_is_cross_sectionally_standardized():
    dates = pd.bdate_range("2023-01-02", periods=30)
    symbols = ["A", "B", "C"]
    close = pd.DataFrame(
        {
            "A": np.linspace(10, 20, 30),
            "B": np.linspace(20, 10, 30),
            "C": np.linspace(15, 15, 30),
        },
        index=dates,
    )
    panel = close.stack().rename("close").reset_index()
    panel.columns = ["date", "symbol", "close"]
    matrix = compute_factor_matrix(panel, factor="momentum_20")
    assert matrix.shape == (30, 3)
    row = matrix.iloc[-1].dropna()
    assert row.mean() == pytest.approx(0.0, abs=1e-9)
    assert row.std(ddof=0) == pytest.approx(1.0, abs=1e-9)


def test_point_in_time_uses_latest_announced_value():
    financials = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "ann_date": ["2023-06-30", "2023-09-30"],
            "roe": [0.10, 0.20],
        }
    )
    result = point_in_time_financial_factor(
        financials, pd.DatetimeIndex(["2023-07-15", "2023-10-15"]), "roe"
    )
    assert result.loc[("2023-07-15", "A")] == pytest.approx(0.10)
    assert result.loc[("2023-10-15", "A")] == pytest.approx(0.20)
