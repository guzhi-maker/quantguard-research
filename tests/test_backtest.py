import pandas as pd
import pytest

from config.settings import CostConfig
from tools.backtest_tool import run_backtest


def _simple_inputs():
    dates = pd.DatetimeIndex(["2023-01-02", "2023-01-03", "2023-01-04"])
    prices = pd.DataFrame(
        {"A": [10.0, 10.5, 11.0], "B": [10.0, 10.0, 10.0]},
        index=dates,
    )
    factor = pd.DataFrame(
        {"A": [1.0, 1.0, 1.0], "B": [-1.0, -1.0, -1.0]},
        index=dates,
    )
    return dates, prices, factor


def test_backtest_equal_weight_no_cost():
    _, prices, factor = _simple_inputs()
    result = run_backtest(
        factor, prices, top_n=1, rebalance_freq="D", costs=CostConfig(commission_rate=0.0, stamp_tax_rate=0.0, transfer_fee_rate=0.0, slippage_rate=0.0)
    )
    # A 每日 +5%，全仓持有 => 净值 1 -> 1.05 -> 1.1025
    assert result.nav.iloc[0] == pytest.approx(1.0)
    assert result.nav.iloc[1] == pytest.approx(1.05)
    assert result.nav.iloc[2] == pytest.approx(1.10)


def test_backtest_with_costs_reduces_nav():
    _, prices, factor = _simple_inputs()
    no_cost = run_backtest(
        factor, prices, top_n=1, rebalance_freq="D", costs=CostConfig(commission_rate=0.0)
    )
    with_cost = run_backtest(
        factor,
        prices,
        top_n=1,
        rebalance_freq="D",
        costs=CostConfig(slippage_rate=0.01),
    )
    assert with_cost.nav.iloc[-1] < no_cost.nav.iloc[-1]


def test_backtest_records_turnover_and_trades():
    dates = pd.DatetimeIndex(["2023-01-02", "2023-01-03", "2023-01-04"])
    prices = pd.DataFrame({"A": [10.0, 10.0, 10.0], "B": [10.0, 10.0, 10.0]}, index=dates)
    factor = pd.DataFrame(
        {"A": [1.0, -1.0, -1.0], "B": [-1.0, 1.0, 1.0]},
        index=dates,
    )
    result = run_backtest(factor, prices, top_n=1, rebalance_freq="D", costs=CostConfig(commission_rate=0.0))
    assert result.turnover.iloc[1] == pytest.approx(2.0)
    assert {"buy", "sell"} <= set(result.trades["side"])
    assert result.trades["date"].nunique() >= 2


def test_backtest_rejects_empty_inputs():
    with pytest.raises(ValueError):
        run_backtest(pd.DataFrame(), pd.DataFrame())
