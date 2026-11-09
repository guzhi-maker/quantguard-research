from __future__ import annotations

import json
import math
import threading
from datetime import date, timedelta
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from io import StringIO

import pandas as pd
import pytest

from config.settings import CostConfig
from webapp import market_client
from webapp.ml_research import FEATURE_NAMES
from webapp.quant_lab import (
    _accumulation_plan,
    _capacity_target,
    _transaction_cost,
    get_research_dataset,
    get_research_dataset_csv,
    run_research,
)
from webapp.server import create_server


def _universe(size: int) -> list[dict]:
    sectors = ["通信", "电子", "银行", "医药", "电力设备"]
    return [
        {
            "symbol": f"60{index:04d}",
            "name": f"研究股票{index}",
            "industry": sectors[index % len(sectors)],
            "price": 20.0 + index,
            "amount": 500_000_000.0,
        }
        for index in range(max(size, 12))
    ]


def _history(symbol: str, days: int = 260) -> list[dict]:
    index = int(symbol[-2:])
    base = 12.0 + index * 0.4
    drift = 0.0004 + (index % 5) * 0.00008
    rows = []
    start = date(2025, 1, 1)
    for offset in range(days):
        close = base * (1.0 + drift) ** offset * (
            1.0 + 0.025 * math.sin((offset + index) / 7.0)
        )
        rows.append(
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "open": close * 0.995,
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "volume": 5_000_000 + index * 100_000,
                "amount": 200_000_000.0 + index * 30_000_000.0,
            }
        )
    return rows


def _universe_loader(limit: int) -> list[dict]:
    return _universe(limit)


def _history_loader(symbol: str, days: int) -> list[dict]:
    return _history(symbol, days)


@pytest.fixture(scope="module")
def research_dataset_report() -> dict:
    return run_research(
        {
            "capital": 50_000,
            "universe_size": 12,
            "top_n": 8,
            "days": 240,
            "model": "balanced",
            "rebalance": "monthly",
            "exit_days": 5,
        },
        universe_loader=_universe_loader,
        history_loader=_history_loader,
    )


def test_live_research_limitations_match_live_data(research_dataset_report: dict):
    limitations = research_dataset_report["report_context"]["limitations"]
    assert not any("演示数据" in item for item in limitations)
    assert any("成交额" in item for item in limitations)


def test_research_returns_capacity_aware_backtest():
    report = run_research(
        {
            "capital": 500_000_000,
            "universe_size": 12,
            "top_n": 8,
            "days": 240,
            "model": "balanced",
            "rebalance": "monthly",
            "exit_days": 5,
        },
        universe_loader=_universe_loader,
        history_loader=_history_loader,
    )

    assert report["meta"]["universe_size_loaded"] >= 8
    assert report["meta"]["source"] == "东方财富公开行情"
    assert report["summary"]["invested_weight"] > 0
    assert report["summary"]["capacity_utilization"] is not None
    assert 1 <= len(report["holdings"]) <= 8
    assert report["factors"][0]["rank"] == 1
    assert len(report["nav"]) >= 2
    assert report["rebalances"]
    assert len(report["model_comparison"]) == 4
    assert sum(bool(row["selected"]) for row in report["model_comparison"]) == 1
    assert report["training"]["leakage_checks"]
    assert report["data_quality"]["cleaning_rules"]
    assert report["exploration"]["feature_missing_top"]
    assert report["risk"]["actions"]
    cost_parts = sum(
        report["cost_breakdown"][key]["currency"]
        for key in (
            "commission",
            "minimum_commission_extra",
            "transfer_fee",
            "stamp_tax",
            "slippage",
            "impact",
        )
    )
    assert cost_parts == pytest.approx(
        report["cost_breakdown"]["total"]["currency"], abs=0.02
    )
    json.dumps(report, allow_nan=False)


def test_large_capital_reduces_invested_weight_through_capacity_limit():
    common = {
        "universe_size": 12,
        "top_n": 8,
        "days": 240,
        "model": "momentum",
        "rebalance": "monthly",
        "exit_days": 5,
    }
    small = run_research(
        {**common, "capital": 100_000_000},
        universe_loader=_universe_loader,
        history_loader=_history_loader,
    )
    large = run_research(
        {**common, "capital": 100_000_000_000},
        universe_loader=_universe_loader,
        history_loader=_history_loader,
    )

    assert large["summary"]["invested_weight"] < small["summary"]["invested_weight"]
    assert large["capacity"]["cash_weight"] > small["capacity"]["cash_weight"]


def test_small_personal_account_uses_whole_lots_and_visible_costs():
    report = run_research(
        {
            "capital": 50_000,
            "universe_size": 12,
            "top_n": 8,
            "days": 240,
            "model": "balanced",
            "rebalance": "monthly",
            "exit_days": 5,
            "monthly_contribution": 2_000,
            "horizon_years": 3,
        },
        universe_loader=_universe_loader,
        history_loader=_history_loader,
    )

    assert len(report["holdings"]) >= 6
    assert all(row["shares"] > 0 and row["shares"] % 100 == 0 for row in report["holdings"])
    assert all(row["position_value"] <= 5_000.01 for row in report["holdings"])
    assert 0.4 < report["summary"]["invested_weight"] < 0.9
    assert report["execution"]["lot_shortfall"] > 0
    assert report["execution"]["build_cost"] > 0
    assert report["execution"]["minimum_commission_cost"] > 0
    assert report["accumulation"]["total_contributed"] == pytest.approx(122_000)
    assert report["accumulation"]["zero_return_end_value"] == pytest.approx(122_000)
    json.dumps(report, allow_nan=False)


def test_capacity_target_skips_stock_whose_minimum_lot_exceeds_budget():
    scores = pd.Series({"HIGH": 3.0, "A": 2.0, "B": 1.5, "C": 1.0})
    volatility = pd.Series({"HIGH": 0.20, "A": 0.22, "B": 0.24, "C": 0.26})
    amount = pd.Series({symbol: 1_000_000_000.0 for symbol in scores.index})
    prices = pd.Series({"HIGH": 100.0, "A": 10.0, "B": 12.0, "C": 14.0})
    metadata = {
        "HIGH": {"industry": "电子"},
        "A": {"industry": "银行"},
        "B": {"industry": "医药"},
        "C": {"industry": "电力设备"},
    }

    target = _capacity_target(
        scores,
        volatility,
        amount,
        50_000,
        4,
        0.10,
        0.05,
        5,
        metadata=metadata,
        prices=prices,
    )

    assert "HIGH" not in target.index
    assert set(target.index) == {"A", "B", "C"}


def test_transaction_cost_applies_minimum_commission_per_order():
    delta = pd.Series({"A": 0.02, "B": -0.02})
    amount = pd.Series({"A": 1_000_000_000.0, "B": 1_000_000_000.0})

    total_rate, minimum_rate = _transaction_cost(
        delta, amount, 50_000, 5, CostConfig()
    )

    assert total_rate * 50_000 > 10.0
    assert minimum_rate * 50_000 > 9.0


def test_zero_return_accumulation_equals_contributions():
    plan = _accumulation_plan(50_000, 2_000, 3, 0.0)

    assert plan["total_contributed"] == pytest.approx(122_000)
    assert plan["projected_end_value"] == pytest.approx(122_000)
    assert plan["conservative_annual_return"] == pytest.approx(0.08)
    assert plan["conservative_end_value"] > plan["total_contributed"]
    assert plan["projected_gain"] == pytest.approx(0)
    assert "不代表未来" in plan["note"]


def test_research_rejects_invalid_parameters():
    with pytest.raises(ValueError, match="目标持仓数"):
        run_research(
            {"universe_size": 12, "top_n": 14},
            universe_loader=_universe_loader,
            history_loader=_history_loader,
        )


def test_liquid_universe_normalizes_eastmoney_fields(monkeypatch):
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "600519",
                    "f14": "贵州 茅台",
                    "f2": 1480.0,
                    "f3": 2.84,
                    "f5": 100000,
                    "f6": 4210000000.0,
                    "f8": 0.59,
                    "f9": 21.52,
                    "f20": 1859000000000.0,
                    "f100": "白酒",
                }
            ]
        }
    }
    monkeypatch.setattr(market_client, "_cached", lambda *_args, **_kwargs: payload)

    items = market_client.get_liquid_universe(10)

    assert items[0]["name"] == "贵州茅台"
    assert items[0]["change_pct"] == pytest.approx(0.0284)
    assert items[0]["turnover_rate"] == pytest.approx(0.0059)
    assert items[0]["amount"] == pytest.approx(4_210_000_000.0)


def test_http_lab_endpoint():
    def runner(payload: dict) -> dict:
        return {
            "summary": {"capital": payload["capital"], "annual_return": 0.12},
            "meta": {"source": "testing"},
        }

    server = create_server(
        "127.0.0.1",
        0,
        snapshot_loader=lambda _symbol, _days: {},
        search_loader=lambda _query, _limit: [],
        research_runner=runner,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps({"capital": 50_000}).encode("utf-8")
        request = Request(
            f"{base}/api/lab",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result["summary"]["capital"] == 50_000
        with urlopen(f"{base}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "机器学习量化研究台" in html
        assert "/app.js" in html and "/quant-lab.js" not in html
        assert 'id="capitalInput"' in html and 'value="5"' in html
        assert "万元" in html
        assert 'id="contributionInput"' in html and 'id="tourOverlay"' in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
