from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data_agent.risk import assess_stock_risk, compute_market_metrics
from data_agent.risk_service import RiskAnalysisService
from data_agent.service import CollectionRequest
from data_agent.store import RawRunStore


def _daily_rows(start: float = 100.0, step: float = 0.5, count: int = 70):
    rows = []
    for index in range(count):
        close = start + index * step
        day = datetime(2026, 11, 9) - timedelta(days=count - 1 - index)
        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1_000_000 + index * 1000,
                "amount": (1_000_000 + index * 1000) * close,
                "source": "测试前复权日行情",
                "adjustment": "qfq",
            }
        )
    return rows


def _quote(price: float = 140.0):
    return {
        "symbol": "600519",
        "name": "贵州茅台",
        "price": price,
        "open": price - 1,
        "high": price + 2,
        "low": price - 2,
        "prev_close": price - 1,
        "change_pct": 0.007,
        "volume": 1_500_000,
        "amount": price * 1_500_000,
        "updated_at": "2026-11-09 15:00:00",
        "source": "测试实时行情",
    }


def _cross(status: str = "verified"):
    return {
        "symbol": "600519",
        "status": status,
        "price_gap_pct": 0.001 if status == "verified" else 0.031,
        "sources": [
            {"id": "eastmoney", "source": "东方财富", "price": 140.0},
            {"id": "tencent", "source": "腾讯证券", "price": 140.14},
        ],
    }


def test_compute_market_metrics_and_cross_source_validation():
    metrics = compute_market_metrics(_daily_rows(), quote=_quote())
    assert metrics["rows"] == 70
    assert metrics["drawdown_20d"] <= 0
    assert metrics["volatility_20_annualized"] is not None
    assert metrics["change_1d"] is not None
    assert metrics["change_1d"] > 0.04

    healthy = assess_stock_risk(
        "600519",
        "贵州茅台",
        _quote(),
        _daily_rows(),
        _cross(),
        {"news": [], "announcements": []},
    )
    assert healthy["validation"]["status"] == "pass"
    assert healthy["level"] == "low"

    mismatch = assess_stock_risk(
        "600519",
        "贵州茅台",
        _quote(),
        _daily_rows(),
        _cross("mismatch"),
        {"news": [], "announcements": []},
    )
    assert mismatch["validation"]["status"] == "fail"
    assert mismatch["level"] in {"high", "critical"}


def test_preopen_quote_does_not_duplicate_latest_close():
    daily = _daily_rows()
    latest_close = daily[-1]["close"]
    quote = {
        **_quote(price=latest_close),
        "prev_close": daily[-2]["close"],
        "updated_at": "2026-11-10 00:09:10",
    }

    metrics = compute_market_metrics(daily, quote=quote)

    expected = latest_close / daily[-2]["close"] - 1.0
    assert metrics["change_1d"] == pytest.approx(expected)
    assert metrics["change_1d"] != pytest.approx(0.0)


def test_indicator_details_and_research_chain_are_explainable():
    result = assess_stock_risk(
        "600519",
        "贵州茅台",
        _quote(),
        _daily_rows(),
        _cross(),
        {
            "news": [
                {
                    "title": "公司发布经营数据公告",
                    "summary": "业绩保持稳定",
                    "published_at": "2026-11-09 10:00:00",
                    "age_hours": 2,
                    "media": "测试媒体",
                    "url": "https://example.com/news",
                }
            ],
            "announcements": [],
        },
    )
    details = result["indicator_details"]
    keys = {item["key"] for item in details}
    assert {
        "latest_price",
        "return_20d",
        "volatility_20_annualized",
        "drawdown_60d",
        "price_gap_pct",
        "news_signal",
    } <= keys
    for item in details:
        assert item["display"]
        assert item["formula"]
        assert item["meaning"]
        assert item["usage"]
        assert item["source"]

    steps = result["research_steps"]
    assert [item["actor"] for item in steps] == [
        "采集器",
        "核价机器人",
        "Python 指标引擎",
        "规则引擎",
        "资讯机器人",
        "DeepSeek",
    ]
    assert all(item["input"] and item["output"] for item in steps)


def test_risk_assessment_uses_negative_news_and_drawdown():
    falling = _daily_rows(start=180.0, step=-1.0)
    intelligence = {
        "news": [
            {
                "category": "news",
                "title": "公司收到监管立案调查通知",
                "summary": "重大事项风险提示",
                "published_at": "2026-11-09 10:00:00",
                "age_hours": 2,
                "media": "测试媒体",
                "url": "https://example.com/news",
            }
        ],
        "announcements": [],
    }
    result = assess_stock_risk(
        "600519",
        "贵州茅台",
        _quote(price=115.0),
        falling,
        _cross(),
        intelligence,
    )
    assert result["level"] in {"high", "critical"}
    assert any(alert["title"] == "近期资讯出现重大风险词" for alert in result["alerts"])


class _FakeCollector:
    def __init__(self, tmp_path):
        self.store = RawRunStore(
            root=tmp_path,
            raw_dir=tmp_path / "raw",
            manifest_dir=tmp_path / "manifests",
        )

    def collect(self, request, emit=None, agent=None):
        emit = emit or (lambda *_args, **_kwargs: None)
        emit("collect", "fake collect", percent=10)
        daily = []
        for symbol in request.symbols:
            for row in _daily_rows(count=60):
                daily.append({**row, "symbol": symbol, "name": "贵州茅台"})
        quote = _quote()
        manifest = self.store.write_run(
            request={**request.to_dict(), "symbols": request.symbols},
            daily_rows=daily,
            quote_rows=[{**quote, "symbol": request.symbols[0]}],
            universe_rows=[
                {
                    "symbol": request.symbols[0],
                    "name": "贵州茅台",
                    "industry": "食品饮料",
                    "source": "测试行情",
                }
            ],
            failures=[],
            trace=[],
            agent=agent,
        )
        return {
            "run_id": manifest["run_id"],
            "summary": manifest["summary"],
            "quality": manifest["quality"],
        }


class _FakeDeepSeek:
    model = "deepseek-test"

    def __init__(self):
        self.called = False

    def chat(self, messages, tools):
        self.called = True
        return {
            "choices": [
                {
                    "message": {
                        "content": "总体结论：测试简报。\n不构成投资建议。"
                    }
                }
            ]
        }


def test_risk_service_collects_verifies_news_and_persists_audit(tmp_path):
    collector = _FakeCollector(tmp_path)
    client = _FakeDeepSeek()
    service = RiskAnalysisService(
        collector=collector,
        client=client,
        quote_verifier=lambda symbol: _cross(),
        news_loader=lambda name, symbol: {
            "symbol": symbol,
            "name": name,
            "news": [
                {
                    "category": "news",
                    "title": "测试新闻",
                    "summary": "测试摘要",
                    "published_at": "2026-11-09 10:00:00",
                    "age_hours": 1,
                    "media": "测试媒体",
                    "url": "https://example.com/news",
                }
            ],
            "announcements": [],
            "errors": [],
        },
        audit_dir=tmp_path / "audits",
    )
    result = service.analyze(
        CollectionRequest(
            universe_size=10,
            history_days=120,
            include_quotes=True,
            symbols=["600519"],
        )
    )
    assert client.called
    assert result["brief"].startswith("总体结论")
    assert result["evidence"]["run_id"].startswith("RAW-")
    assert result["audit_id"].startswith("AUDIT-")
    assert result["research_design"]["split"]
    assert "未来 5 日" in result["research_design"]["labels"]
    assert (tmp_path / "audits" / f"{result['audit_id']}.json").exists()
    assert service.load_audit(result["audit_id"])["evidence"]["daily_rows"] == 60
