from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import pyarrow.parquet as pq

from data_agent.agent import DeepSeekAgent, DeepSeekClient
from data_agent.research_exports import build_research_exports
from data_agent.server import create_server
from data_agent.service import CollectorService, CollectionRequest
from data_agent.store import RawRunStore
from tools.package_research_exports import build_research_bundle
from webapp import market_client


class FakeGateway:
    def search(self, keyword: str, limit: int = 8):
        return [{"symbol": "600519", "name": "贵州茅台", "industry": "食品饮料"}]

    def universe(self, limit: int):
        return [
            {
                "symbol": f"6000{index:02d}",
                "name": f"测试股票{index}",
                "industry": "测试行业",
                "price": 10 + index,
                "amount": 1_000_000 - index,
                "source": "测试公开行情",
            }
            for index in range(limit)
        ]

    def quote(self, symbol: str):
        return {
            "symbol": symbol,
            "name": "贵州茅台" if symbol == "600519" else f"股票{symbol}",
            "industry": "食品饮料",
            "price": 100.0,
            "amount": 2_000_000.0,
            "source": "测试实时行情",
        }

    def history(self, symbol: str, days: int):
        return [
            {
                "date": f"2026-10-{index + 1:02d}",
                "open": 10.0 + index,
                "close": 10.1 + index,
                "high": 10.5 + index,
                "low": 9.8 + index,
                "volume": 1000 + index,
                "amount": 10_000 + index,
                "source": "东方财富前复权日行情",
            }
            for index in range(min(days, 9))
        ]

    def probe(self, source_id: str):
        return {"id": source_id, "status": "ok", "latency_ms": 12}


def make_service(tmp_path):
    store = RawRunStore(
        root=tmp_path,
        raw_dir=tmp_path / "raw",
        manifest_dir=tmp_path / "manifests",
    )
    return CollectorService(
        store=store,
        gateway=FakeGateway(),
        derived_dir=tmp_path / "derived",
    )


def test_collection_request_validation():
    request = CollectionRequest.from_payload(
        {
            "universe_size": "20",
            "history_days": "60",
            "include_quotes": "false",
            "symbols": "600519，000001,600519",
        }
    )
    assert request.universe_size == 20
    assert request.history_days == 60
    assert request.include_quotes is False
    assert request.symbols == ["600519", "000001"]


def test_collection_request_supports_expanded_limits():
    request = CollectionRequest.from_payload(
        {"universe_size": "5000", "history_days": "250"}
    )
    assert request.universe_size == 5000
    assert request.history_days == 250
    request = CollectionRequest.from_payload(
        {"universe_size": "5000", "history_days": "5000"}
    )
    assert request.history_days == 5000
    with pytest.raises(ValueError):
        CollectionRequest.from_payload(
            {"universe_size": 5001, "history_days": 250}
        )
    with pytest.raises(ValueError):
        CollectionRequest.from_payload(
            {"universe_size": 1000, "history_days": 5001}
        )
    with pytest.raises(ValueError):
        CollectionRequest.from_payload(
            {"universe_size": 5000, "history_days": 250, "symbols": ",".join([
                f"{index:06d}" for index in range(5001)
            ])}
        )


def test_long_history_skips_truncated_tencent_fallback(monkeypatch):
    def eastmoney_unavailable(*_args, **_kwargs):
        raise market_client.MarketDataError("primary unavailable")

    def tencent_must_not_run(*_args, **_kwargs):
        pytest.fail("Tencent fallback must not truncate a 1000-day request")

    def sina_long_history(_symbol, limit):
        return [{"date": f"2020-01-{index + 1:02d}"} for index in range(limit)]

    monkeypatch.setattr(market_client, "_get_eastmoney_history", eastmoney_unavailable)
    monkeypatch.setattr(market_client, "_get_tencent_history", tencent_must_not_run)
    monkeypatch.setattr(market_client, "_get_sina_history", sina_long_history)
    market_client._CACHE.clear()

    rows = market_client.get_history("600519", 1000)
    assert len(rows) == 1000


def test_run_summaries_omit_trace_but_keep_evidence(tmp_path):
    service = make_service(tmp_path)
    result = service.collect(
        CollectionRequest(universe_size=12, history_days=30)
    )
    summaries = service.store.list_run_summaries(limit=5)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["run_id"] == result["run_id"]
    assert "trace" not in summary
    assert "symbols" not in summary["request"]
    assert summary["files"]["daily"]["rows"] == 108
    assert summary["manifest_path"].startswith("manifests/")


def test_fixed_collection_writes_csv_manifest_and_hashes(tmp_path):
    service = make_service(tmp_path)
    job = service.create_job(
        {
            "mode": "fixed",
            "universe_size": 12,
            "history_days": 30,
            "include_quotes": True,
        },
        background=False,
    )
    assert job["status"] == "complete"
    run_id = job["result"]["run_id"]
    manifest = service.store.load_run(run_id)
    assert manifest["summary"]["symbols"] == 12
    assert manifest["summary"]["daily_rows"] == 108
    assert manifest["files"]["daily"]["rows"] == 108
    assert len(manifest["files"]["daily"]["sha256"]) == 64
    assert manifest["lineage"][0]["source"] == "东方财富前复权日行情"

    page = service.store.read_rows(run_id, "daily", limit=5, query="600000")
    assert page["total"] == 9
    assert page["rows"][0]["symbol"] == "600000"
    assert page["rows"][0]["fetched_at"]


def test_deepseek_agent_uses_registered_tools_and_persists_trace(tmp_path):
    service = make_service(tmp_path)
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-sources",
                                "type": "function",
                                "function": {
                                    "name": "list_data_sources",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-collect",
                                "type": "function",
                                "function": {
                                    "name": "collect_market_data",
                                    "arguments": json.dumps(
                                        {
                                            "universe_size": 12,
                                            "history_days": 30,
                                            "include_quotes": True,
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "已通过公开接口完成采集。",
                        "tool_calls": [],
                    }
                }
            ]
        },
    ]

    def transport(_url, _headers, payload, _timeout):
        assert payload["tools"]
        return responses.pop(0)

    client = DeepSeekClient(api_key="test-key", transport=transport)
    agent = DeepSeekAgent(service, client=client)
    result = agent.run(
        CollectionRequest(
            universe_size=12,
            history_days=30,
            include_quotes=True,
            query="采集 12 只股票",
        )
    )
    manifest = service.store.load_run(result["run_id"])
    assert manifest["agent"]["provider"] == "deepseek"
    assert manifest["agent"]["final_response"] == "已通过公开接口完成采集。"
    assert [item["tool"] for item in manifest["agent"]["tool_trace"]] == [
        "list_data_sources",
        "collect_market_data",
    ]


def test_agent_rejects_malformed_tool_arguments_without_collecting(tmp_path):
    service = make_service(tmp_path)
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-invalid",
                                "type": "function",
                                "function": {
                                    "name": "collect_market_data",
                                    "arguments": "{invalid-json",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "采集任务参数已纠正。",
                        "tool_calls": [],
                    }
                }
            ]
        },
    ]

    client = DeepSeekClient(
        api_key="test-key",
        transport=lambda *_args: responses.pop(0),
    )
    agent = DeepSeekAgent(service, client=client, max_steps=2)
    try:
        agent.run(CollectionRequest(universe_size=12, history_days=30))
    except ValueError as exc:
        assert "未完成真实数据采集" in str(exc)
    else:
        raise AssertionError("invalid tool arguments must not start a collection")

    assert service.store.list_runs() == []


def test_research_exports_build_coverage_parquet_and_manifest(tmp_path):
    service = make_service(tmp_path)
    result = service.collect(
        CollectionRequest(universe_size=12, history_days=30)
    )
    run_id = result["run_id"]
    exported = build_research_exports(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
    )

    assert exported["coverage"]["summary"]["trading_sessions"] == 9
    assert exported["coverage"]["summary"]["symbols"] == 12
    assert exported["coverage"]["summary"]["lifetime_completeness"] == 1.0
    assert exported["parquet"]["daily_rows"] == 108
    coverage_dir = tmp_path / "derived" / run_id
    assert (coverage_dir / "coverage_by_symbol.csv").is_file()
    parquet_files = list(
        (coverage_dir / "parquet" / "market_daily").rglob("*.parquet")
    )
    assert len(parquet_files) == 1
    assert pq.read_table(parquet_files[0]).num_rows == 108
    assert (coverage_dir / "export_manifest.json").is_file()
    cached = build_research_exports(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
    )
    assert cached["generated_at"] == exported["generated_at"]
    rebuilt = build_research_exports(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
        rebuild=True,
    )
    assert rebuilt["parquet"]["daily_rows"] == 108


def test_research_bundle_packages_docs_and_derived_files(tmp_path):
    service = make_service(tmp_path)
    result = service.collect(
        CollectionRequest(universe_size=12, history_days=30)
    )
    run_id = result["run_id"]
    build_research_exports(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
    )
    bundle = build_research_bundle(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
        exports_dir=tmp_path / "exports",
    )
    assert bundle["parquet_rows"] == 108
    assert bundle["duckdb"] in {"ready", "unavailable"}
    archive_path = Path(bundle["path"])
    assert archive_path.is_file()
    assert archive_path.with_suffix(".zip.sha256").is_file()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    prefix = f"RESEARCH-{run_id}"
    assert f"{prefix}/README.md" in names
    assert f"{prefix}/coverage_by_symbol.csv" in names
    assert f"{prefix}/raw_manifest.json" in names
    assert f"{prefix}/docs/dataset-card.md" in names
    assert any(name.endswith(".parquet") for name in names)


def test_collector_server_api_and_static_page(tmp_path):
    service = make_service(tmp_path)
    job = service.create_job(
        {
            "mode": "fixed",
            "universe_size": 12,
            "history_days": 30,
            "include_quotes": True,
        },
        background=False,
    )
    run_id = job["result"]["run_id"]
    server = create_server(service=service)
    build_research_exports(
        run_id,
        store=service.store,
        derived_dir=tmp_path / "derived",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base}/api/health", timeout=5) as response:
            assert json.loads(response.read().decode("utf-8"))["status"] == "ok"

        request = Request(
            f"{base}/api/search",
            data=json.dumps({"keyword": "茅台"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["items"][0]["symbol"] == "600519"

        with urlopen(f"{base}/api/runs/{run_id}/rows?view=daily&limit=3", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["total"] == 108
        assert len(payload["rows"]) == 3

        with urlopen(f"{base}/api/runs/{run_id}/coverage", timeout=5) as response:
            coverage = json.loads(response.read().decode("utf-8"))
        assert coverage["summary"]["trading_sessions"] == 9

        with urlopen(f"{base}/api/runs/{run_id}/derived", timeout=5) as response:
            derived = json.loads(response.read().decode("utf-8"))
        assert derived["parquet"]["daily_rows"] == 108

        export_request = Request(
            f"{base}/api/runs/{run_id}/exports",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(export_request, timeout=10) as response:
            export_result = json.loads(response.read().decode("utf-8"))
        assert export_result["parquet_rows"] == 108

        with urlopen(
            f"{base}/api/runs/{run_id}/derived-file?path=coverage_by_symbol.csv",
            timeout=5,
        ) as response:
            coverage_csv = response.read().decode("utf-8-sig")
        assert "missing_sessions" in coverage_csv
        assert "600001" in coverage_csv

        with urlopen(f"{base}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "股票风险体检" in html
        assert 'id="analysisForm"' in html
        assert "/app.js" in html
        assert "研究数据池" in html
        assert 'id="dataPoolForm"' in html
        assert 'id="reportTitle"' in html
        assert 'id="researchChain"' in html
        assert 'id="indicatorTable"' in html
        assert 'id="universeSize"' in html
        assert 'id="coverageBuckets"' in html
        assert 'id="coverageYears"' in html
        assert 'id="buildExportsButton"' in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
