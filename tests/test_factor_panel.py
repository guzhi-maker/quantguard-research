from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from data_agent.factor_panel import (
    FACTOR_FIELDS,
    _factor_ready,
    _trailing_dividend,
    factor_sample,
    factor_summary_for_api,
    prepare_financials,
)
from data_agent import fundamentals as fundamentals_module
from data_agent.fundamentals import (
    INDEX_FIELDS,
    _dedupe_csv,
    _paginate,
    _write_csv,
    collect_fundamentals,
    fetch_dividends,
    fetch_unadjusted_prices,
)


def _financial_row(
    report_date: str,
    revenue: float,
    profit: float,
    cost: float,
    *,
    symbol: str = "600519",
) -> dict:
    return {
        "symbol": symbol,
        "report_date": report_date,
        "ann_date": report_date,
        "report_type": "测试报告",
        "revenue_ytd": revenue,
        "net_profit_ytd": profit,
        "operating_cf_ytd": profit * 1.2,
        "cost_of_sales_ytd": cost,
        "total_asset": 1_000 + revenue,
        "total_liability": 300,
        "equity": 700 + profit,
        "total_shares": 100,
        "float_shares": 80,
    }


def test_ttm_uses_previous_annual_report_without_future_leakage() -> None:
    raw = pd.DataFrame(
        [
            _financial_row("2023-03-31", 100, 10, 60),
            _financial_row("2023-06-30", 200, 20, 120),
            _financial_row("2023-12-31", 400, 40, 240),
            _financial_row("2024-03-31", 110, 11, 60),
            _financial_row("2024-06-30", 230, 23, 130),
            _financial_row("2024-09-30", 360, 36, 210),
            _financial_row("2024-12-31", 500, 50, 300),
        ]
    )

    result = prepare_financials(raw).set_index("report_date")

    assert result.loc["2024-06-30", "revenue_q"] == pytest.approx(120)
    assert result.loc["2024-06-30", "revenue"] == pytest.approx(120)
    assert result.loc["2024-06-30", "net_profit"] == pytest.approx(12)
    assert result.loc["2024-06-30", "revenue_ttm"] == pytest.approx(530)
    assert result.loc["2024-06-30", "net_profit_ttm"] == pytest.approx(53)
    assert result.loc["2024-12-31", "revenue_ttm"] == pytest.approx(500)


def test_trailing_dividend_only_uses_implemented_ex_dates() -> None:
    dates = pd.Series(pd.to_datetime(["2023-06-01", "2024-06-01"]))
    events = pd.DataFrame(
        {
            "ex_dividend_date": ["2023-06-05", "2024-05-01", "2024-07-01"],
            "cash_per_share": [1.0, 0.5, 2.0],
        }
    )

    result = _trailing_dividend(dates, events)

    assert result.iloc[0] == pytest.approx(0.0)
    assert result.iloc[1] == pytest.approx(1.5)


def test_financial_template_uses_sw_industry_not_missing_cost() -> None:
    raw = pd.DataFrame([_financial_row("2024-03-31", 100, 10, 60)])

    general = prepare_financials(
        raw, industry_by_symbol={"600519": "食品饮料"}
    )
    financial = prepare_financials(
        raw, industry_by_symbol={"600519": "银行"}
    )

    assert general.iloc[0]["financial_template"] == "general"
    assert financial.iloc[0]["financial_template"] == "financial"


def test_financial_row_without_announcement_is_excluded() -> None:
    raw = _financial_row("2024-03-31", 100, 10, 60)
    raw["ann_date"] = None

    result = prepare_financials(pd.DataFrame([raw]))

    assert result.empty


def test_financial_template_does_not_penalize_missing_gross_margin() -> None:
    base = {field: 1.0 for field in FACTOR_FIELDS}
    frame = pd.DataFrame(
        [
            {**base, "gross_margin": np.nan, "financial_template": "financial"},
            {**base, "gross_margin": np.nan, "financial_template": "general"},
        ]
    )

    ready, missing_groups = _factor_ready(frame)

    assert bool(ready.iloc[0]) is True
    assert missing_groups.iloc[0] == ""
    assert bool(ready.iloc[1]) is False
    assert "quality" in missing_groups.iloc[1]


def test_resumed_csv_deduplication_keeps_last_natural_key(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    path.write_text(
        "date,symbol,close_raw,source,fetched_at\n"
        "2026-11-10,600519,1,old,2026-11-10\n"
        "2026-11-10,600519,2,new,2026-11-11\n",
        encoding="utf-8",
    )

    removed = _dedupe_csv(path, ["symbol", "date"], [
        "date", "symbol", "close_raw", "source", "fetched_at"
    ])

    assert removed == 1
    rows = pd.read_csv(path)
    assert rows.to_dict("records") == [
        {
            "date": "2026-11-10",
            "symbol": 600519,
            "close_raw": 2,
            "source": "new",
            "fetched_at": "2026-11-11",
        }
    ]


def test_factor_summary_and_latest_sample_read_duckdb(tmp_path: Path) -> None:
    run_id = "RAW-20261113-120000-ABCDEF"
    root = tmp_path / run_id / "factors"
    root.mkdir(parents=True)
    panel = pd.DataFrame(
        [
            {
                "date": "2026-11-10",
                "symbol": "600519",
                "name": "贵州茅台",
                "industry": "食品饮料",
                **{field: 0.1 for field in FACTOR_FIELDS},
                "report_date": "2026-09-30",
                "ann_date": "2026-10-31",
                "factor_ready": True,
                "missing_groups": "",
            }
        ]
    )
    connection = duckdb.connect(str(root / "factors.duckdb"))
    connection.execute("CREATE TABLE factor_panel AS SELECT * FROM panel")
    connection.close()
    manifest = {
        "raw_run_id": run_id,
        "fund_id": "FUND-20261113-120000-ABCDEF",
        "generated_at": "2026-11-13T12:00:00+08:00",
        "summary": {"rows": 1, "symbols": 1, "factor_ready_ratio": 1.0},
        "by_year": [],
        "by_factor": {"ep_ttm": {"label": "盈利收益率"}},
        "files": {},
        "duckdb": {"status": "ready"},
    }
    (root / "factor_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = factor_summary_for_api(run_id, derived_dir=tmp_path)
    sample = factor_sample(run_id, derived_dir=tmp_path)

    assert summary["summary"]["symbols"] == 1
    assert sample["latest_date"] == "2026-11-10"
    assert sample["rows"][0]["symbol"] == "600519"
    assert sample["rows"][0]["ep_ttm"] == pytest.approx(0.1)
def test_pagination_stops_after_full_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_datacenter(*args, **kwargs):
        page = kwargs["page_number"]
        calls.append(page)
        if page <= 2:
            return [{"page": page, "row": index} for index in range(60)]
        raise fundamentals_module.FundamentalDataError("返回数据为空")

    monkeypatch.setattr(fundamentals_module, "_datacenter", fake_datacenter)

    rows = _paginate("TEST", "(CODE=\"600690\")", page_size=60, max_pages=4)

    assert len(rows) == 120
    assert calls == [1, 2, 3]


def test_empty_dividend_table_is_a_valid_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def empty_paginate(*args, **kwargs):
        raise fundamentals_module.FundamentalDataError("RPT_SHAREBONUS_DET 返回错误: 返回数据为空")

    monkeypatch.setattr(fundamentals_module, "_paginate", empty_paginate)

    assert fetch_dividends("688825") == []


def test_unadjusted_prices_use_sina_daily_close(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch_json(url, params, **kwargs):
        assert params["symbol"] == "sz300308"
        assert params["datalen"] == "1023"
        return [
            {"day": "2026-11-09", "close": "452.03"},
            {"day": "2026-11-10", "close": "471.0"},
        ]

    monkeypatch.setattr(fundamentals_module, "_fetch_json", fake_fetch_json)

    rows = fetch_unadjusted_prices("300308", days=1200)

    assert [row["close_raw"] for row in rows] == [452.03, 471.0]
    assert rows[0]["source"] == "新浪财经不复权日线"


def test_required_price_failure_is_not_marked_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fundamentals_module, "FUNDAMENTAL_DIR", tmp_path)
    staging = tmp_path / "_staging" / "STAGE-TEST"
    staging.mkdir(parents=True)
    _write_csv(staging / "index_daily.csv", [], INDEX_FIELDS)
    (staging / "state.json").write_text(
        json.dumps(
            {
                "done": ["600519"],
                "failures": [
                    {"symbol": "600519", "stage": "prices", "error": "closed"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def failed_bundle(symbol, start_date, days, include_prices, price_limiter=None):
        calls.append(symbol)
        return {
            "symbol": symbol,
            "profile": {"symbol": symbol, "name": "贵州茅台"},
            "financials": [],
            "share_capital": [],
            "dividends": [],
            "prices": [],
            "errors": [{"stage": "prices", "error": "unavailable"}],
        }

    monkeypatch.setattr(fundamentals_module, "fetch_symbol_bundle", failed_bundle)

    manifest = collect_fundamentals(
        ["600519"],
        workers=1,
        staging=staging,
        include_prices=True,
    )

    assert calls == ["600519"]
    assert manifest["status"] == "partial"
    assert manifest["summary"]["unfinished_symbols"] == 1
    assert json.loads((staging / "state.json").read_text(encoding="utf-8"))["done"] == []
