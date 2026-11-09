from __future__ import annotations

import json
import math
import threading
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.parse import quote

import pytest

from webapp import market_client
from webapp.server import BadRequest, analyze_payload, create_server


def _snapshot(name: str, industry: str, base: float, drift: float) -> dict:
    rows = []
    start = date(2024, 1, 1)
    for index in range(120):
        close = base * (
            1.0
            + drift * index
            + 0.018 * math.sin(index / 4.0)
            + 0.008 * math.cos(index / 9.0)
        )
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close * 0.995,
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "volume": 1_000_000,
            }
        )
    return {
        "quote": {
            "symbol": "600519",
            "name": name,
            "industry": industry,
            "price": rows[-1]["close"],
            "change_pct": 0.012,
            "turnover_rate": 0.6,
            "market_cap": 1_000_000_000,
            "updated_at": "2026-11-03 10:30:00",
        },
        "history": rows,
    }


def _snapshots() -> dict[str, dict]:
    return {
        "600519": _snapshot("贵州茅台", "食品饮料", 100.0, 0.001),
        "000001": _snapshot("平安银行", "银行", 20.0, 0.0004),
        "300750": _snapshot("宁德时代", "电力设备", 60.0, 0.0008),
    }


def _loader(symbol: str, _days: int) -> dict:
    return _snapshots()[symbol]


def test_analyze_payload_returns_portfolio_report():
    report = analyze_payload(
        {
            "days": 120,
            "holdings": [
                {"symbol": "600519", "weight": 8},
                {"symbol": "000001", "weight": 22},
                {"symbol": "300750", "weight": 15},
            ],
        },
        snapshot_loader=_loader,
    )

    assert report["summary"]["level"] in {"low", "medium", "high", "critical"}
    assert report["metrics"]["invested_weight"] == pytest.approx(0.45)
    assert report["meta"] == {
        "holdings_count": 3,
        "history_days": 120,
        "data_mode": "live",
        "capital": 50000.0,
        "source": "东方财富公开行情",
    }
    assert len(report["holdings"]) == 3
    assert report["nav"]


def test_single_stock_limit_names_by_weight_not_input_order():
    report = analyze_payload(
        {
            "holdings": [
                {"symbol": "000001", "weight": 15},
                {"symbol": "600519", "weight": 30},
            ]
        },
        snapshot_loader=_loader,
    )

    critical = [
        item
        for item in report["suggestions"]
        if item["severity"] == "critical" and item["category"] == "仓位约束"
    ]
    assert critical
    assert "贵州茅台" in critical[0]["detail"]
    assert "平安银行" not in critical[0]["detail"]


def test_payload_rejects_excess_total_weight():
    with pytest.raises(BadRequest, match="不能超过 100"):
        analyze_payload(
            {
                "holdings": [
                    {"symbol": "600519", "weight": 70},
                    {"symbol": "000001", "weight": 40},
                ]
            },
            snapshot_loader=_loader,
        )


def test_payload_rejects_invalid_symbol_and_duplicates():
    with pytest.raises(BadRequest, match="6 位数字"):
        analyze_payload(
            {"holdings": [{"symbol": "ABC", "weight": 10}]},
            snapshot_loader=_loader,
        )
    with pytest.raises(BadRequest, match="重复"):
        analyze_payload(
            {
                "holdings": [
                    {"symbol": "600519", "weight": 10},
                    {"symbol": "600519", "weight": 5},
                ]
            },
            snapshot_loader=_loader,
        )


def test_market_data_error_is_preserved():
    def failing_loader(_symbol: str, _days: int) -> dict:
        raise market_client.MarketDataError("公开行情暂时不可用")

    with pytest.raises(market_client.MarketDataError, match="公开行情暂时不可用"):
        analyze_payload(
            {"holdings": [{"symbol": "600519", "weight": 10}]},
            snapshot_loader=failing_loader,
        )


def test_http_api_and_static_shell():
    server = create_server(
        "127.0.0.1",
        0,
        snapshot_loader=_loader,
        search_loader=lambda _query, _limit: [
            {"symbol": "600519", "name": "贵州茅台", "market": "沪A"}
        ],
        universe_loader=lambda _limit: [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "price": 1480.0,
                "change_pct": 0.0284,
                "amount": 4_210_000_000.0,
                "turnover_rate": 0.0059,
            }
        ],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base}/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "ok"

        with urlopen(f"{base}/api/search?q={quote('茅台')}", timeout=5) as response:
            search = json.loads(response.read().decode("utf-8"))
        assert search["items"][0]["symbol"] == "600519"

        with urlopen(f"{base}/api/market/snapshot?limit=8", timeout=5) as response:
            market = json.loads(response.read().decode("utf-8"))
        assert market["data_mode"] == "live"
        assert market["source"] == "东方财富公开行情"
        assert market["items"][0]["symbol"] == "600519"
        assert market["fetched_at"]

        request = Request(
            f"{base}/api/risk",
            data=json.dumps(
                {"holdings": [{"symbol": "600519", "weight": 10}]}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result["metrics"]["invested_weight"] == pytest.approx(0.1)

        with urlopen(f"{base}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "机器学习量化研究台" in html
        assert "/app.js" in html and "/quant-lab.js" not in html
        assert 'id="researchForm"' in html and 'id="tourOverlay"' in html
        assert 'data-mode="demo"' not in html
        assert 'id="sampleWatchlistButton"' not in html
        assert 'data-mode="live"' in html and "实时行情" in html
        assert 'id="liveMarketBody"' in html and 'id="liveMarketTime"' in html
        with urlopen(f"{base}/app.js", timeout=5) as response:
            app_js = response.read().decode("utf-8")
        assert "/api/market/snapshot?limit=12&data_mode=live" in app_js
        assert "未使用演示数据替代" in app_js
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_quote_normalizes_eastmoney_percentage_fields(monkeypatch):
    payload = {
        "data": {
            "f57": "600519",
            "f58": "贵州 茅台",
            "f43": 148000,
            "f44": 149200,
            "f45": 144100,
            "f46": 143910,
            "f47": 28400,
            "f48": 4210000000,
            "f60": 143910,
            "f116": 1859000000000,
            "f117": 1859000000000,
            "f162": 2152,
            "f167": 682,
            "f168": 59,
            "f169": 4090,
            "f170": 284,
            "f127": "白酒Ⅱ",
            "f128": "贵州",
            "f129": "白酒",
        }
    }
    monkeypatch.setattr(market_client, "_cached", lambda *_args, **_kwargs: payload)

    quote = market_client.get_quote("600519")

    assert quote["name"] == "贵州茅台"
    assert quote["price"] == 1480.0
    assert quote["change_pct"] == pytest.approx(0.0284)
    assert quote["turnover_rate"] == pytest.approx(0.0059)
    assert quote["pe"] == pytest.approx(21.52)
    assert quote["pb"] == pytest.approx(6.82)


def test_search_cleans_stock_names(monkeypatch):
    payload = {
        "QuotationCodeTable": {
            "Data": [
                {
                    "Classify": "AStock",
                    "SecurityTypeName": "沪A",
                    "Code": "600519",
                    "Name": "贵州 茅台",
                    "QuoteID": "1.600519",
                }
            ]
        }
    }
    monkeypatch.setattr(market_client, "_cached", lambda *_args, **_kwargs: payload)

    items = market_client.search_stocks("茅台")

    assert items[0]["symbol"] == "600519"
    assert items[0]["name"] == "贵州茅台"


def test_sina_universe_normalizes_live_percentage_fields(monkeypatch):
    payload = [
        {
            "code": "300750",
            "name": "宁德时代",
            "trade": "265.940",
            "changepercent": 5.165,
            "amount": 13607667742,
            "turnoverratio": 1.20162,
            "per": 16.477,
        }
    ]
    monkeypatch.setattr(market_client, "_cached", lambda *_args, **_kwargs: payload)

    items = market_client._get_sina_universe(10)

    assert items[0]["symbol"] == "300750"
    assert items[0]["change_pct"] == pytest.approx(0.0517)
    assert items[0]["turnover_rate"] == pytest.approx(0.012)
    assert items[0]["source"] == "新浪财经公开行情"


def test_tencent_quote_and_history_fallback(monkeypatch):
    fields = [""] * 46
    fields[1] = "贵州茅台"
    fields[2] = "600519"
    fields[3] = "1445.66"
    fields[4] = "1446.44"
    fields[5] = "1446.70"
    fields[6] = "31789"
    fields[30] = "20261105115611"
    fields[31] = "-0.78"
    fields[32] = "-0.05"
    fields[33] = "1466.00"
    fields[34] = "1430.00"
    fields[35] = "1445.66/31789/4592105073"
    fields[38] = "0.25"
    fields[39] = "22.00"
    text = f'v_sh600519="{"~".join(fields)}";'
    monkeypatch.setattr(market_client, "_cached", lambda *_args, **_kwargs: text)

    quote = market_client._get_tencent_quote("600519")

    assert quote["name"] == "贵州茅台"
    assert quote["change_pct"] == pytest.approx(-0.0005)
    assert quote["amount"] == 4_592_105_073.0
    assert quote["source"] == "腾讯证券公开行情"

    history_payload = {
        "data": {
            "sh600519": {
                "qfqday": [
                    ["2026-11-04", "1470.00", "1446.44", "1480.60", "1439.00", "65434"],
                    ["2026-11-05", "1446.70", "1445.66", "1466.00", "1430.00", "31789"],
                ]
            }
        }
    }
    monkeypatch.setattr(market_client, "_fetch_json", lambda *_args, **_kwargs: history_payload)

    history = market_client._get_tencent_history("600519", 1)

    assert history[-1]["date"] == "2026-11-05"
    assert history[-1]["close"] == pytest.approx(1445.66)
    assert history[-1]["amount"] > 1_000_000_000
    assert "成交额估算" in history[-1]["source"]


def test_sina_history_parser_and_fallback(monkeypatch):
    history_payload = [
        {
            "day": "2026-11-04",
            "open": "1470.000",
            "high": "1480.600",
            "low": "1439.000",
            "close": "1446.440",
            "volume": "6543429",
        }
    ]
    monkeypatch.setattr(
        market_client, "_fetch_json", lambda *_args, **_kwargs: history_payload
    )

    history = market_client._get_sina_history("600519", 1)

    assert history[-1]["date"] == "2026-11-04"
    assert history[-1]["close"] == pytest.approx(1446.44)
    assert history[-1]["amount"] > 1_000_000_000
    assert "成交额估算" in history[-1]["source"]

    monkeypatch.setattr(
        market_client,
        "_get_eastmoney_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            market_client.MarketDataError("blocked")
        ),
    )
    monkeypatch.setattr(
        market_client,
        "_get_tencent_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            market_client.MarketDataError("501")
        ),
    )
    monkeypatch.setattr(
        market_client,
        "_cached",
        lambda _key, ttl, loader: loader(),
    )

    fallback = market_client.get_history("600519", 30)

    assert fallback[-1]["date"] == "2026-11-04"
    assert "新浪财经" in fallback[-1]["source"]
