from __future__ import annotations

import json
import threading
from io import StringIO
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd
import pytest

from webapp.ml_research import FEATURE_NAMES
from webapp.quant_lab import (
    get_research_dataset,
    get_research_dataset_csv,
    run_research,
)
from webapp.server import create_server


@pytest.fixture(scope="module")
def research_report() -> dict:
    return run_research(
        {
            "data_mode": "demo",
            "capital": 50_000,
            "universe_size": 10,
            "top_n": 3,
            "days": 240,
            "model": "factor",
            "rebalance": "monthly",
            "exit_days": 5,
        }
    )


def test_complete_dataset_views_and_label_boundaries(research_report: dict):
    meta = research_report["meta"]
    assert any(
        "演示数据" in item
        for item in research_report["report_context"]["limitations"]
    )
    dataset_id = meta["dataset_id"]
    raw = get_research_dataset(dataset_id, view="raw", offset=0, limit=10)
    model = get_research_dataset(dataset_id, view="model", offset=0, limit=10)

    assert raw["dataset_id"] == dataset_id
    assert raw["experiment_id"] == meta["experiment_id"]
    assert raw["total"] == meta["dataset_rows"]["raw_rows"]
    assert model["total"] == meta["dataset_rows"]["model_rows"]
    assert model["total"] == research_report["data_quality"]["labeled_rows"]
    assert len(raw["rows"]) == 10
    assert [row["date"] for row in raw["rows"]] == sorted(
        [row["date"] for row in raw["rows"]], reverse=True
    )
    assert {
        "date",
        "symbol",
        "name",
        "industry",
        "close",
        "amount",
        "volume",
    } == {column["key"] for column in raw["columns"]}

    model_columns = {column["key"] for column in model["columns"]}
    assert set(FEATURE_NAMES).issubset(model_columns)
    assert {"future_excess", "label_end_date"}.issubset(model_columns)
    assert "future_excess" not in FEATURE_NAMES
    assert [row["date"] for row in model["rows"]] == sorted(
        [row["date"] for row in model["rows"]], reverse=True
    )
    for row in model["rows"]:
        assert row["date"] < row["label_end_date"]


def test_dataset_server_filters_pages_and_exports_complete_csv(
    research_report: dict,
):
    dataset_id = research_report["meta"]["dataset_id"]
    first_page = get_research_dataset(dataset_id, offset=0, limit=10)
    second_page = get_research_dataset(dataset_id, offset=10, limit=10)
    first_keys = {(row["date"], row["symbol"]) for row in first_page["rows"]}
    second_keys = {(row["date"], row["symbol"]) for row in second_page["rows"]}
    assert first_keys.isdisjoint(second_keys)

    symbol = first_page["rows"][0]["symbol"]
    filtered = get_research_dataset(dataset_id, query=symbol, limit=500)
    assert filtered["total"] < filtered["source_total"]
    assert filtered["rows"]
    assert all(row["symbol"] == symbol for row in filtered["rows"])

    latest_date = filtered["rows"][-1]["date"]
    date_filtered = get_research_dataset(
        dataset_id,
        query=symbol,
        date_from=latest_date,
        date_to=latest_date,
        limit=500,
    )
    assert date_filtered["total"] == 1

    with pytest.raises(ValueError, match="view"):
        get_research_dataset(dataset_id, view="invalid")
    with pytest.raises(ValueError, match="offset"):
        get_research_dataset(dataset_id, offset=first_page["total"])
    with pytest.raises(ValueError, match="开始日期"):
        get_research_dataset(dataset_id, date_from="202556")

    raw_csv = get_research_dataset_csv(dataset_id, view="raw")
    model_csv = get_research_dataset_csv(dataset_id, view="model")
    raw_frame = pd.read_csv(StringIO(raw_csv["content"]))
    model_frame = pd.read_csv(StringIO(model_csv["content"]))
    assert len(raw_frame) == raw_csv["rows"] == research_report["meta"]["dataset_rows"]["raw_rows"]
    assert len(model_frame) == model_csv["rows"] == research_report["meta"]["dataset_rows"]["model_rows"]
    assert set(FEATURE_NAMES).issubset(model_frame.columns)
    assert "future_excess" in model_frame.columns


def test_dataset_http_routes_return_pages_errors_and_attachment(
    research_report: dict,
):
    dataset_id = research_report["meta"]["dataset_id"]
    server = create_server(
        "127.0.0.1",
        0,
        snapshot_loader=lambda _symbol, _days: {},
        search_loader=lambda _query, _limit: [],
        research_runner=lambda _payload: research_report,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(
            f"{base}/api/dataset/{dataset_id}?view=model&offset=0&limit=3",
            timeout=5,
        ) as response:
            page = json.loads(response.read().decode("utf-8"))
        assert page["view"] == "model"
        assert page["limit"] == 3 and len(page["rows"]) == 3
        assert page["source_total"] == research_report["meta"]["dataset_rows"]["model_rows"]

        for path, message in (
            (f"{base}/api/dataset/{dataset_id}?view=invalid", "view"),
            (f"{base}/api/dataset/{dataset_id}?offset=999999", "offset"),
            (f"{base}/api/dataset/{dataset_id}?limit=0", "limit"),
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(path, timeout=5)
            assert error.value.code == 400
            detail = json.loads(error.value.read().decode("utf-8"))
            assert message in detail["error"]

        with pytest.raises(HTTPError) as missing:
            urlopen(f"{base}/api/dataset/DATA-NOT-FOUND", timeout=5)
        assert missing.value.code == 404

        with urlopen(
            f"{base}/api/dataset/{dataset_id}/csv?view=raw",
            timeout=10,
        ) as response:
            csv_bytes = response.read()
            assert response.headers.get_content_type() == "text/csv"
            assert "attachment" in response.headers["Content-Disposition"]
            assert response.headers["Content-Disposition"].endswith('_raw.csv"')
        assert csv_bytes.startswith(b"\xef\xbb\xbf")
        frame = pd.read_csv(StringIO(csv_bytes.decode("utf-8-sig")))
        assert len(frame) == research_report["meta"]["dataset_rows"]["raw_rows"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
