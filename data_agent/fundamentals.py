"""Collect point-in-time A-share fundamentals and supporting market data.

The module fetches public Eastmoney data for a fixed universe and writes an
immutable ``FUND-*`` snapshot. It is intentionally separate from the daily
price snapshot so existing raw snapshots remain unchanged and reproducible.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.parse
import threading
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config.settings import DATA_DIR
from data_agent.store import CHINA_TZ, RawRunStore, now_iso
from webapp.market_client import USER_AGENT, normalize_symbol, to_quote_id

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TENCENT_HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_HISTORY_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
FUNDAMENTAL_DIR = DATA_DIR / "fundamentals"
FUND_ID_RE = re.compile(r"^FUND-[0-9]{8}-[0-9]{6}-[A-F0-9]{6}$")
DEFAULT_START_DATE = "2019-01-01"
INDEX_SECID = "1.000300"

PROFILE_FIELDS = [
    "symbol",
    "name",
    "list_date",
    "industry",
    "industry_l2",
    "industry_l3",
    "industry_standard",
    "em_industry",
    "csrc_industry",
    "market",
    "source",
    "fetched_at",
]

FINANCIAL_FIELDS = [
    "symbol",
    "name",
    "report_date",
    "ann_date",
    "report_type",
    "report_year",
    "revenue_ytd",
    "net_profit_ytd",
    "operating_cf_ytd",
    "gross_profit_ytd",
    "cost_of_sales_ytd",
    "total_asset",
    "total_liability",
    "equity",
    "total_shares",
    "float_shares",
    "source",
    "fetched_at",
]

SHARE_CAPITAL_FIELDS = [
    "symbol",
    "end_date",
    "ann_date",
    "listing_date",
    "total_shares",
    "float_shares",
    "change_reason",
    "source",
    "fetched_at",
]

DIVIDEND_FIELDS = [
    "symbol",
    "report_date",
    "ann_date",
    "ex_dividend_date",
    "equity_record_date",
    "cash_per_share",
    "total_shares",
    "status",
    "plan_profile",
    "source",
    "fetched_at",
]

PRICE_FIELDS = [
    "date",
    "symbol",
    "close_raw",
    "source",
    "fetched_at",
]

INDEX_FIELDS = [
    "date",
    "symbol",
    "close_raw",
    "source",
    "fetched_at",
]


class FundamentalDataError(RuntimeError):
    """A public fundamental endpoint could not be parsed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _tencent_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}{code}"

def _secucode(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    raise ValueError(f"无法识别交易所: {code}")


def _fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    referer: str = "https://data.eastmoney.com/",
    attempts: int = 3,
) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except Exception as exc:  # noqa: BLE001 - public endpoint failures vary
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.7 * (attempt + 1))
    raise FundamentalDataError(f"公开数据接口请求失败: {last_error}") from last_error


def _datacenter(
    report_name: str,
    filter_expr: str,
    columns: str = "ALL",
    sort_columns: str = "",
    sort_types: str = "-1",
    page_size: int = 60,
    page_number: int = 1,
) -> list[dict]:
    params: dict[str, Any] = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_expr,
        "pageNumber": page_number,
        "pageSize": page_size,
    }
    if sort_columns:
        params["sortColumns"] = sort_columns
        params["sortTypes"] = sort_types
    payload = _fetch_json(
        DATACENTER_URL,
        params,
        referer="https://emweb.securities.eastmoney.com/",
    )
    if not payload.get("success"):
        message = str(payload.get("message") or payload)
        if "返回数据为空" in message:
            time.sleep(0.75)
            payload = _fetch_json(
                DATACENTER_URL,
                params,
                referer="https://emweb.securities.eastmoney.com/",
            )
        if not payload.get("success"):
            raise FundamentalDataError(
                f"{report_name} 返回错误: {payload.get('message') or payload}"
            )
    result = payload.get("result") or {}
    return list(result.get("data") or [])


def _paginate(
    report_name: str,
    filter_expr: str,
    columns: str = "ALL",
    sort_columns: str = "",
    sort_types: str = "-1",
    page_size: int = 60,
    max_pages: int = 10,
) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            batch = _datacenter(
                report_name,
                filter_expr,
                columns=columns,
                sort_columns=sort_columns,
                sort_types=sort_types,
                page_size=page_size,
                page_number=page,
            )
        except FundamentalDataError as exc:
            # A full final page advances the cursor once; the next page is empty.
            if rows and "返回数据为空" in str(exc):
                break
            raise
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def fetch_profile(symbol: str, fetched_at: str | None = None) -> dict:
    code = normalize_symbol(symbol)
    rows = _datacenter(
        "RPT_F10_BASIC_ORGINFO",
        f'(SECUCODE="{_secucode(code)}")',
        columns=(
            "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,EM2016,"
            "BOARD_NAME_LEVEL,LISTING_DATE,INDUSTRYCSRC1,TRADE_MARKET"
        ),
        page_size=1,
    )
    if not rows:
        raise FundamentalDataError(f"未找到 {code} 的公司资料")
    row = rows[0]
    levels = [
        part.strip()
        for part in str(row.get("BOARD_NAME_LEVEL") or "").split("-")
        if part.strip()
    ]
    em_levels = [
        part.strip()
        for part in str(row.get("EM2016") or "").split("-")
        if part.strip()
    ]
    return {
        "symbol": code,
        "name": str(row.get("SECURITY_NAME_ABBR") or code).strip(),
        "list_date": _date_text(row.get("LISTING_DATE")),
        "industry": levels[0] if levels else (em_levels[0] if em_levels else "未分类"),
        "industry_l2": levels[1] if len(levels) > 1 else "",
        "industry_l3": levels[2] if len(levels) > 2 else "",
        "industry_standard": "申万" if levels else "东方财富",
        "em_industry": str(row.get("EM2016") or ""),
        "csrc_industry": str(row.get("INDUSTRYCSRC1") or ""),
        "market": str(row.get("TRADE_MARKET") or ""),
        "source": "东方财富公司资料",
        "fetched_at": fetched_at or now_iso(),
    }


def fetch_financials(
    symbol: str,
    start_date: str = DEFAULT_START_DATE,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    rows = _paginate(
        "RPT_F10_FINANCE_MAINFINADATA",
        f'(SECUCODE="{_secucode(code)}")',
        columns=(
            "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,"
            "NOTICE_DATE,REPORT_TYPE,REPORT_YEAR,TOTALOPERATEREVE,MLR,"
            "PARENTNETPROFIT,NETCASH_OPERATE_PK,TOTAL_ASSETS_PK,LIABILITY,"
            "TOTAL_EQUITY_PK,TOTAL_SHARE,A_FREE_SHARE"
        ),
        sort_columns="REPORT_DATE",
        sort_types="-1",
        page_size=60,
        max_pages=4,
    )
    items = []
    for row in rows:
        report_date = _date_text(row.get("REPORT_DATE"))
        if not report_date or report_date < start_date:
            continue
        revenue = _number(row.get("TOTALOPERATEREVE"))
        gross_profit = _number(row.get("MLR"))
        items.append(
            {
                "symbol": code,
                "name": str(row.get("SECURITY_NAME_ABBR") or code),
                "report_date": report_date,
                "ann_date": _date_text(row.get("NOTICE_DATE")),
                "report_type": str(row.get("REPORT_TYPE") or ""),
                "report_year": str(row.get("REPORT_YEAR") or report_date[:4]),
                "revenue_ytd": revenue,
                "net_profit_ytd": _number(row.get("PARENTNETPROFIT")),
                "operating_cf_ytd": _number(row.get("NETCASH_OPERATE_PK")),
                "gross_profit_ytd": gross_profit,
                "cost_of_sales_ytd": (
                    revenue - gross_profit
                    if revenue is not None and gross_profit is not None
                    else None
                ),
                "total_asset": _number(row.get("TOTAL_ASSETS_PK")),
                "total_liability": _number(row.get("LIABILITY")),
                "equity": _number(row.get("TOTAL_EQUITY_PK")),
                "total_shares": _number(row.get("TOTAL_SHARE")),
                "float_shares": _number(row.get("A_FREE_SHARE")),
                "source": "东方财富主要财务指标",
                "fetched_at": fetched_at or now_iso(),
            }
        )
    return sorted(items, key=lambda item: item["report_date"])


def fetch_share_capital(
    symbol: str,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    rows = _paginate(
        "RPT_F10_EH_EQUITY",
        f'(SECUCODE="{_secucode(code)}")',
        columns=(
            "SECUCODE,SECURITY_CODE,END_DATE,NOTICE_DATE,LISTING_DATE,"
            "TOTAL_SHARES,FREE_SHARES,CHANGE_REASON"
        ),
        sort_columns="END_DATE",
        sort_types="-1",
        page_size=60,
        max_pages=4,
    )
    items = []
    for row in rows:
        end_date = _date_text(row.get("END_DATE"))
        total_shares = _number(row.get("TOTAL_SHARES"))
        if not end_date or total_shares is None:
            continue
        items.append(
            {
                "symbol": code,
                "end_date": end_date,
                "ann_date": _date_text(row.get("NOTICE_DATE")),
                "listing_date": _date_text(row.get("LISTING_DATE")),
                "total_shares": total_shares,
                "float_shares": _number(row.get("FREE_SHARES")),
                "change_reason": str(row.get("CHANGE_REASON") or ""),
                "source": "东方财富股本变动",
                "fetched_at": fetched_at or now_iso(),
            }
        )
    return sorted(items, key=lambda item: item["end_date"])


def fetch_dividends(
    symbol: str,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    try:
        rows = _paginate(
            "RPT_SHAREBONUS_DET",
            f'(SECURITY_CODE="{code}")',
            columns=(
                "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,"
                "EX_DIVIDEND_DATE,EQUITY_RECORD_DATE,PRETAX_BONUS_RMB,"
                "ASSIGN_PROGRESS,IMPL_PLAN_PROFILE,TOTAL_SHARES"
            ),
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page_size=60,
            max_pages=4,
        )
    except FundamentalDataError as exc:
        # A company with no dividend history is a valid empty event stream.
        if "返回数据为空" in str(exc):
            return []
        raise
    items = []
    for row in rows:
        report_date = _date_text(row.get("REPORT_DATE"))
        ex_date = _date_text(row.get("EX_DIVIDEND_DATE"))
        bonus = _number(row.get("PRETAX_BONUS_RMB"))
        items.append(
            {
                "symbol": code,
                "report_date": report_date,
                "ann_date": _date_text(row.get("NOTICE_DATE")),
                "ex_dividend_date": ex_date,
                "equity_record_date": _date_text(row.get("EQUITY_RECORD_DATE")),
                "cash_per_share": bonus / 10.0 if bonus is not None else None,
                "total_shares": _number(row.get("TOTAL_SHARES")),
                "status": str(row.get("ASSIGN_PROGRESS") or ""),
                "plan_profile": str(row.get("IMPL_PLAN_PROFILE") or ""),
                "source": "东方财富分红送配",
                "fetched_at": fetched_at or now_iso(),
            }
        )
    return sorted(
        items,
        key=lambda item: (item["report_date"] or "", item["ex_dividend_date"] or ""),
    )


def _parse_klines(payload: dict, symbol: str, source: str) -> list[dict]:
    data = payload.get("data") or {}
    items = []
    for line in data.get("klines") or []:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        date = _date_text(parts[0])
        if not date:
            continue
        items.append(
            {
                "date": date,
                "symbol": symbol,
                "close_raw": _number(parts[2]),
                "source": source,
            }
        )
    return items


def _fetch_eastmoney_unadjusted_prices(
    symbol: str,
    days: int = 1200,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    params = {
        "secid": to_quote_id(code),
        "klt": "101",
        "fqt": "0",
        "lmt": str(max(30, min(int(days), 5000))),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = _fetch_json(
        HISTORY_URL,
        params,
        referer="https://quote.eastmoney.com/",
        attempts=1,
    )
    items = _parse_klines(payload, code, "东方财富不复权日行情")
    for item in items:
        item["fetched_at"] = fetched_at or now_iso()
    return items


def _fetch_tencent_unadjusted_prices(
    symbol: str,
    days: int,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    tencent_code = _tencent_symbol(code)
    limit = max(30, min(int(days), 5000))
    payload = _fetch_json(
        TENCENT_HISTORY_URL,
        {"param": f"{tencent_code},day,,,{limit},"},
        referer="https://gu.qq.com/",
        attempts=4,
    )
    stock = (payload.get("data") or {}).get(tencent_code) or {}
    items = []
    for parts in stock.get("day") or []:
        if not isinstance(parts, list) or len(parts) < 3:
            continue
        date = _date_text(parts[0])
        close_raw = _number(parts[2])
        if not date or close_raw is None:
            continue
        items.append(
            {
                "date": date,
                "symbol": code,
                "close_raw": close_raw,
                "source": "腾讯证券不复权日线",
                "fetched_at": fetched_at or now_iso(),
            }
        )
    if not items:
        raise FundamentalDataError(f"腾讯证券未返回 {code} 的不复权日线")
    return sorted(items, key=lambda item: item["date"])


def _fetch_sina_unadjusted_prices(
    symbol: str,
    days: int,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    limit = max(30, min(int(days) + 10, 1023))
    payload = _fetch_json(
        SINA_HISTORY_URL,
        {
            "symbol": _tencent_symbol(code),
            "scale": "240",
            "ma": "no",
            "datalen": str(limit),
        },
        referer="https://finance.sina.com.cn/",
        attempts=3,
    )
    if not isinstance(payload, list):
        raise FundamentalDataError("新浪财经日线返回格式异常")
    items = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        date = _date_text(row.get("day"))
        close_raw = _number(row.get("close"))
        if not date or close_raw is None:
            continue
        items.append(
            {
                "date": date,
                "symbol": code,
                "close_raw": close_raw,
                "source": "新浪财经不复权日线",
                "fetched_at": fetched_at or now_iso(),
            }
        )
    if not items:
        raise FundamentalDataError(f"新浪财经未返回 {code} 的不复权日线")
    return sorted(items, key=lambda item: item["date"])


def fetch_unadjusted_prices(
    symbol: str,
    days: int = 1200,
    fetched_at: str | None = None,
) -> list[dict]:
    code = normalize_symbol(symbol)
    try:
        return _fetch_sina_unadjusted_prices(
            code, days=days, fetched_at=fetched_at
        )
    except FundamentalDataError as sina_error:
        sina_message = str(sina_error)
    try:
        return _fetch_tencent_unadjusted_prices(
            code, days=days, fetched_at=fetched_at
        )
    except FundamentalDataError as tencent_error:
        tencent_message = str(tencent_error)
    try:
        return _fetch_eastmoney_unadjusted_prices(
            code, days=days, fetched_at=fetched_at
        )
    except FundamentalDataError as eastmoney_error:
        raise FundamentalDataError(
            f"不复权日线不可用；新浪: {sina_message}；腾讯: {tencent_message}；"
            f"东方财富: {eastmoney_error}"
        ) from eastmoney_error


def fetch_index_history(
    secid: str = INDEX_SECID,
    days: int = 1600,
    fetched_at: str | None = None,
) -> list[dict]:
    params = {
        "secid": secid,
        "klt": "101",
        "fqt": "1",
        "lmt": str(max(30, min(int(days), 5000))),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = _fetch_json(
        HISTORY_URL,
        params,
        referer="https://quote.eastmoney.com/",
    )
    items = _parse_klines(payload, "000300", "东方财富沪深300指数")
    for item in items:
        item["fetched_at"] = fetched_at or now_iso()
    return items


def fetch_symbol_bundle(
    symbol: str,
    start_date: str = DEFAULT_START_DATE,
    days: int = 1200,
    include_prices: bool = True,
    price_limiter: threading.Semaphore | None = None,
) -> dict:
    fetched_at = now_iso()
    bundle = {
        "symbol": normalize_symbol(symbol),
        "profile": None,
        "financials": [],
        "share_capital": [],
        "dividends": [],
        "prices": [],
        "errors": [],
    }
    tasks = [
        ("profile", lambda: fetch_profile(symbol, fetched_at=fetched_at)),
        (
            "financials",
            lambda: fetch_financials(symbol, start_date=start_date, fetched_at=fetched_at),
        ),
        (
            "share_capital",
            lambda: fetch_share_capital(symbol, fetched_at=fetched_at),
        ),
        ("dividends", lambda: fetch_dividends(symbol, fetched_at=fetched_at)),
    ]
    if include_prices:
        def load_prices() -> list[dict]:
            with price_limiter or nullcontext():
                rows = fetch_unadjusted_prices(
                    symbol, days=days, fetched_at=fetched_at
                )
                time.sleep(0.08)
                return rows

        tasks.append(
            (
                "prices",
                load_prices,
            )
        )
    for name, loader in tasks:
        try:
            bundle[name] = loader()
        except Exception as exc:  # noqa: BLE001 - keep partial evidence
            bundle["errors"].append({"stage": name, "error": str(exc)})
    return bundle


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
    return len(rows)


def _append_price_row(handle, row: dict) -> None:
    handle.write(
        ",".join(
            [
                str(row.get("date") or ""),
                str(row.get("symbol") or ""),
                "" if row.get("close_raw") is None else str(row["close_raw"]),
                str(row.get("source") or ""),
                str(row.get("fetched_at") or ""),
            ]
        )
    )
    handle.write("\n")


def _price_header() -> str:
    return ",".join(PRICE_FIELDS) + "\n"


def _write_manifest(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _latest_index_snapshot() -> tuple[Path, list[dict]] | None:
    for path in sorted(FUNDAMENTAL_DIR.glob("FUND-*/index_daily.csv"), reverse=True):
        rows = _read_csv(path)
        if rows:
            return path, rows
    return None


def _quality(
    profiles: list[dict],
    financials: list[dict],
    share_capital: list[dict],
    dividends: list[dict],
    price_rows: int,
    deduplicated: dict[str, int] | None = None,
) -> dict:
    deduplicated = deduplicated or {}
    profile_symbols = {row["symbol"] for row in profiles}
    financial_symbols = {row["symbol"] for row in financials}
    capital_symbols = {row["symbol"] for row in share_capital}
    dividend_symbols = {row["symbol"] for row in dividends}
    keys = [(row["symbol"], row["report_date"]) for row in financials]
    return {
        "profiles": len(profiles),
        "profile_symbols": len(profile_symbols),
        "financial_rows": len(financials),
        "financial_symbols": len(financial_symbols),
        "share_capital_rows": len(share_capital),
        "share_capital_symbols": len(capital_symbols),
        "dividend_rows": len(dividends),
        "dividend_symbols": len(dividend_symbols),
        "unadjusted_price_rows": price_rows,
        "missing_ann_date": sum(1 for row in financials if not row.get("ann_date")),
        "duplicate_financial_keys": len(keys) - len(set(keys)),
        "deduplicated_rows": deduplicated,
    }


def collect_fundamentals(
    symbols: list[str],
    source_run_id: str = "",
    workers: int = 8,
    price_workers: int = 2,
    start_date: str = DEFAULT_START_DATE,
    history_days: int = 1200,
    include_prices: bool = True,
    staging: Path | None = None,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Fetch fundamentals concurrently and write one immutable FUND snapshot."""
    codes = []
    seen = set()
    for symbol in symbols:
        code = normalize_symbol(symbol)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    if not codes:
        raise ValueError("基本面采集需要至少一只股票")

    if staging is None:
        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
        staging = (
            FUNDAMENTAL_DIR
            / "_staging"
            / f"STAGE-{stamp}-{uuid.uuid4().hex[:6]}"
        )
    staging.mkdir(parents=True, exist_ok=True)
    state_path = staging / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {
            "done": [],
            "failures": [],
            "started_at": now_iso(),
            "source_run_id": source_run_id,
            "workers": workers,
        }
    required_stages = {"profile", "financials", "share_capital"}
    if include_prices:
        required_stages.add("prices")

    def is_required(stage: str) -> bool:
        return stage in required_stages or stage == "bundle"

    failures = list(state.get("failures") or [])
    invalid_done = {
        str(item.get("symbol"))
        for item in failures
        if is_required(str(item.get("stage") or ""))
    }
    done = set(state.get("done") or []) - invalid_done
    state["done"] = sorted(done)
    state["workers"] = workers

    profile_path = staging / "profiles.csv"
    financial_path = staging / "financials.csv"
    capital_path = staging / "share_capital.csv"
    dividend_path = staging / "dividends.csv"
    price_path = staging / "prices_unadjusted.csv"
    if not profile_path.exists():
        _write_csv(profile_path, [], PROFILE_FIELDS)
    if not financial_path.exists():
        _write_csv(financial_path, [], FINANCIAL_FIELDS)
    if not capital_path.exists():
        _write_csv(capital_path, [], SHARE_CAPITAL_FIELDS)
    if not dividend_path.exists():
        _write_csv(dividend_path, [], DIVIDEND_FIELDS)
    if include_prices and not price_path.exists():
        price_path.write_text(_price_header(), encoding="utf-8-sig")

    index_path = staging / "index_daily.csv"
    if not index_path.exists():
        index_source = "东方财富沪深300实时接口"
        try:
            index_rows = fetch_index_history(days=max(history_days, 1600))
        except FundamentalDataError as exc:
            fallback = _latest_index_snapshot()
            if fallback is None:
                raise
            fallback_path, index_rows = fallback
            index_source = f"复用已校验指数快照: {fallback_path.parent.name}"
            print(
                f"[fundamentals] 指数接口失败，复用 {fallback_path.parent.name}: {exc}",
                flush=True,
            )
        _write_csv(index_path, index_rows, INDEX_FIELDS)

    pending = [code for code in codes if code not in done]
    price_limiter = None
    if include_prices:
        price_limiter = threading.BoundedSemaphore(
            max(1, min(int(price_workers), 4))
        )
    price_rows = 0
    price_handle = None
    if include_prices:
        price_handle = price_path.open("a", encoding="utf-8", newline="")
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 32))) as pool:
            futures = {
                pool.submit(
                    fetch_symbol_bundle,
                    code,
                    start_date,
                    history_days,
                    include_prices,
                    price_limiter,
                ): code
                for code in pending
            }
            for index, future in enumerate(as_completed(futures), start=1):
                code = futures[future]
                try:
                    bundle = future.result()
                except Exception as exc:  # noqa: BLE001
                    bundle = {
                        "symbol": code,
                        "profile": None,
                        "financials": [],
                        "share_capital": [],
                        "dividends": [],
                        "prices": [],
                        "errors": [{"stage": "bundle", "error": str(exc)}],
                    }
                if bundle.get("profile"):
                    _append_rows(profile_path, [bundle["profile"]], PROFILE_FIELDS)
                _append_rows(financial_path, bundle.get("financials") or [], FINANCIAL_FIELDS)
                _append_rows(
                    capital_path,
                    bundle.get("share_capital") or [],
                    SHARE_CAPITAL_FIELDS,
                )
                _append_rows(
                    dividend_path,
                    bundle.get("dividends") or [],
                    DIVIDEND_FIELDS,
                )
                if price_handle is not None:
                    for row in bundle.get("prices") or []:
                        _append_price_row(price_handle, row)
                        price_rows += 1
                failures = [item for item in failures if item.get("symbol") != code]
                bundle_errors = list(bundle.get("errors") or [])
                for error in bundle_errors:
                    stage = str(error.get("stage") or "")
                    failures.append(
                        {
                            "symbol": code,
                            **error,
                            "severity": "required" if is_required(stage) else "optional",
                        }
                    )
                has_required_failure = any(
                    is_required(str(error.get("stage") or ""))
                    for error in bundle_errors
                )
                if has_required_failure:
                    done.discard(code)
                else:
                    done.add(code)
                state["done"] = sorted(done)
                state["failures"] = failures
                _write_manifest(state_path, state)
                required_count = sum(
                    1
                    for item in failures
                    if is_required(str(item.get("stage") or ""))
                )
                if progress:
                    progress(
                        {
                            "done": len(done & set(codes)),
                            "total": len(codes),
                            "symbol": code,
                            "failures": required_count,
                        }
                    )
                if index % 20 == 0:
                    print(
                        f"[fundamentals] {len(done)}/{len(codes)} "
                        f"failures={len(failures)}",
                        flush=True,
                    )
    finally:
        if price_handle is not None:
            price_handle.close()

    # A resumed snapshot may contain rows whose write completed before state.json.
    deduplicated = {
        "profiles": _dedupe_csv(profile_path, ["symbol"], PROFILE_FIELDS),
        "financials": _dedupe_csv(
            financial_path, ["symbol", "report_date"], FINANCIAL_FIELDS
        ),
        "share_capital": _dedupe_csv(
            capital_path, ["symbol", "end_date"], SHARE_CAPITAL_FIELDS
        ),
        "dividends": _dedupe_csv(
            dividend_path,
            ["symbol", "report_date", "ex_dividend_date"],
            DIVIDEND_FIELDS,
        ),
    }
    if include_prices:
        deduplicated["prices"] = _dedupe_csv(
            price_path, ["symbol", "date"], PRICE_FIELDS
        )
    profiles = _read_csv(profile_path)
    financials = _read_csv(financial_path)
    share_capital = _read_csv(capital_path)
    dividends = _read_csv(dividend_path)
    quality = _quality(
        profiles,
        financials,
        share_capital,
        dividends,
        _count_rows(price_path) if include_prices else 0,
        deduplicated=deduplicated,
    )
    unfinished = sorted(set(codes) - done)
    required_failures = [
        item for item in failures if is_required(str(item.get("stage") or ""))
    ]
    optional_failures = [
        item for item in failures if not is_required(str(item.get("stage") or ""))
    ]
    fund_id = _new_fund_id()
    target = FUNDAMENTAL_DIR / fund_id
    while target.exists():
        fund_id = _new_fund_id()
        target = FUNDAMENTAL_DIR / fund_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=False)
    for source, name in (
        (profile_path, "profiles.csv"),
        (financial_path, "financials.csv"),
        (capital_path, "share_capital.csv"),
        (dividend_path, "dividends.csv"),
    ):
        target.joinpath(name).write_bytes(source.read_bytes())
    if include_prices and price_path.exists():
        target.joinpath("prices_unadjusted.csv").write_bytes(price_path.read_bytes())
    if index_path.exists():
        target.joinpath("index_daily.csv").write_bytes(index_path.read_bytes())

    files = {}
    for path in sorted(target.iterdir()):
        if path.is_file():
            files[path.name] = {
                "path": path.relative_to(FUNDAMENTAL_DIR.parent).as_posix(),
                "rows": _count_rows(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    manifest = {
        "schema_version": 1,
        "fund_id": fund_id,
        "source_run_id": source_run_id,
        "created_at": now_iso(),
        "status": "complete" if not unfinished and not required_failures else "partial",
        "request": {
            "symbols": len(codes),
            "workers": workers,
            "price_workers": price_workers,
            "start_date": start_date,
            "history_days": history_days,
            "include_prices": include_prices,
        },
        "summary": {
            "symbols": len(profile_symbols(profiles)),
            "financial_rows": len(financials),
            "share_capital_rows": len(share_capital),
            "dividend_rows": len(dividends),
            "unadjusted_price_rows": _count_rows(price_path) if include_prices else 0,
            "index_rows": _count_rows(index_path) if index_path.exists() else 0,
            "index_source": index_source if "index_source" in locals() else "staging已有指数文件",
            "complete_symbols": len(done & set(codes)),
            "unfinished_symbols": len(unfinished),
            "failures": len(failures),
            "required_failures": len(required_failures),
            "optional_failures": len(optional_failures),
        },
        "quality": quality,
        "files": files,
        "failures": failures,
        "limitations": [
            "财务字段来自东方财富主要财务指标，按报告披露日回溯使用，不使用未来公告。",
            "单季值与TTM由累计报表差分计算；银行、保险等模板的毛利率可能为空。",
            "股本变动以变动生效日为时间戳，历史市值使用对应日期前最近股本记录。",
            "分红因子使用除权除息日已实施的税前每股现金分红，不包含未来预案。",
            "不复权收盘价仅用于历史市值和估值，收益率仍使用前复权价格。",
        ],
    }
    _write_manifest(target / "manifest.json", manifest)
    state["result"] = {"fund_id": fund_id, "manifest": str(target / "manifest.json")}
    _write_manifest(state_path, state)
    return manifest


def _append_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _dedupe_csv(path: Path, key_fields: list[str], fields: list[str]) -> int:
    """Keep the last row for each natural key and return removed row count."""
    if not path.exists():
        return 0
    rows = _read_csv(path)
    unique: dict[tuple[str, ...], dict] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        unique[key] = row
    removed = len(rows) - len(unique)
    if removed:
        _write_csv(path, list(unique.values()), fields)
    return removed


def _count_rows(path: Path) -> int:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    return 0


def profile_symbols(profiles: list[dict]) -> set[str]:
    return {str(row.get("symbol")) for row in profiles if row.get("symbol")}


def _new_fund_id() -> str:
    stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
    return f"FUND-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def list_fundamental_manifests() -> list[dict]:
    items = []
    if not FUNDAMENTAL_DIR.exists():
        return items
    for path in FUNDAMENTAL_DIR.glob("FUND-*/manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["manifest_path"] = path.as_posix()
        items.append(payload)
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def load_fundamental_manifest(fund_id: str) -> dict:
    if not FUND_ID_RE.fullmatch(str(fund_id)):
        raise ValueError("基本面快照编号格式无效")
    path = FUNDAMENTAL_DIR / fund_id / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到基本面快照 {fund_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest_path"] = path.as_posix()
    return payload


def latest_fundamental_for_run(run_id: str) -> dict | None:
    for item in list_fundamental_manifests():
        if item.get("source_run_id") == run_id:
            return item
    return None


def fundamental_file(fund_id: str, relative_path: str) -> Path:
    root = (FUNDAMENTAL_DIR / fund_id).resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise ValueError("基本面文件路径越界")
    if not path.is_file():
        raise FileNotFoundError(f"基本面文件不存在: {relative_path}")
    return path


def resolve_source_run(store: RawRunStore | None = None, run_id: str = "") -> dict:
    store = store or RawRunStore()
    if run_id:
        return store.load_run(run_id)
    runs = store.list_runs(1)
    if not runs:
        raise FileNotFoundError("没有可用的原始行情快照")
    return runs[0]
