"""v2 Web 接口的单元测试：参数校验、持仓解析、任务状态。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from data_agent import v2_api  # noqa: E402


def test_parse_holdings_accepts_text_and_dict():
    assert v2_api.parse_holdings("600519:0.2,300750:0.15") == {
        "600519": 0.2,
        "300750": 0.15,
    }
    assert v2_api.parse_holdings({"600519": 0.2}) == {"600519": 0.2}
    assert v2_api.parse_holdings("") == {}
    assert v2_api.parse_holdings(None) == {}


@pytest.mark.parametrize(
    "raw",
    ["60051:0.2", "600519:1.5", "abc:0.2", "600519"],
)
def test_parse_holdings_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        v2_api.parse_holdings(raw)


def test_validate_rejects_out_of_range():
    with pytest.raises(ValueError):
        v2_api._validate({"run_id": "RAW-20261115-112954-DE6611", "risk": 11})
    with pytest.raises(ValueError):
        v2_api._validate({"run_id": "RAW-20261115-112954-DE6611", "horizon": 4})
    with pytest.raises(ValueError):
        v2_api._validate({"run_id": "RAW-20261115-112954-DE6611", "capital": 100})


def test_snapshot_listing_reads_manifest(tmp_path, monkeypatch):
    manifest = {
        "run_id": "RAW-20260101-000000-ABCDEF",
        "created_at": "2026-01-01T00:00:00+08:00",
        "summary": {
            "symbols": 12,
            "daily_rows": 3456,
            "date_min": "2025-01-01",
            "date_max": "2025-12-31",
            "sources": ["测试源"],
            "failures": 0,
        },
    }
    (tmp_path / "RAW-20260101-000000-ABCDEF.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.setattr(v2_api, "MANIFEST_DIR", tmp_path)
    items = v2_api.list_snapshots(min_symbols=0)
    assert len(items) == 1
    assert items[0]["run_id"] == manifest["run_id"]
    assert items[0]["symbols"] == 12
    assert items[0]["rows"] == 3456
