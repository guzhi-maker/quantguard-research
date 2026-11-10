"""公开证券行情客户端，带多源回退、内存缓存和标准化输出。"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
MARKET_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
SINA_MARKET_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
TENCENT_HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_HISTORY_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantGuard/1.0"

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


class MarketDataError(RuntimeError):
    """公开行情接口暂时不可用。"""


def _cached(key: str, ttl: int, loader):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] <= ttl:
            return hit[1]
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def _fetch_json(url: str, referer: str = "https://quote.eastmoney.com/") -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)
    except Exception as exc:
        raise MarketDataError(f"行情接口请求失败: {exc}") from exc


def _fetch_text_gb18030(url: str, referer: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.read().decode("gb18030", errors="replace")
    except Exception as exc:
        raise MarketDataError(f"备用行情接口请求失败: {exc}") from exc


def normalize_symbol(symbol: str) -> str:
    digits = re.sub(r"\D", "", str(symbol or ""))
    if len(digits) != 6:
        raise ValueError("股票代码必须是 6 位数字")
    return digits


def to_quote_id(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def _scaled(value: Optional[float], divisor: float = 100.0) -> Optional[float]:
    if value is None or value == "-":
        return None
    try:
        return round(float(value) / divisor, 4)
    except (TypeError, ValueError):
        return None


def _percentage(value: Optional[float]) -> Optional[float]:
    """Convert percentage fields such as 284 to the ratio 0.0284."""
    return _scaled(value, divisor=10000.0)


def _clean_name(value: Any, fallback: str) -> str:
    return re.sub(r"\s+", "", str(value or "")) or fallback


def _number(value: Any) -> Optional[float]:
    if value is None or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tencent_code(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}{code}"


def _is_tradable_share(symbol: str, name: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", symbol)) and not any(
        word in name.upper() for word in ("ST", "退")
    )


def _get_sina_universe(size: int) -> list[dict]:
    def load():
        items = []
        seen = set()
        for page in range(1, (size + 99) // 100 + 1):
            query = urllib.parse.urlencode(
                {
                    "page": page,
                    "num": 100,
                    "sort": "amount",
                    "asc": 0,
                    "node": "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                }
            )
            url = f"{SINA_MARKET_URL}?{query}"
            payload = _cached(
                f"sina-universe:{size}:{page}",
                ttl=45,
                loader=lambda url=url: _fetch_json(
                    url, referer="https://finance.sina.com.cn/"
                ),
            )
            if not isinstance(payload, list) or not payload:
                break
            for row in payload:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("code") or "").strip()
                name = _clean_name(row.get("name"), symbol)
                if not _is_tradable_share(symbol, name) or symbol in seen:
                    continue
                seen.add(symbol)
                items.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "industry": "A股",
                        "price": _number(row.get("trade")),
                        "change_pct": _scaled(row.get("changepercent")),
                        "volume": _number(row.get("volume")),
                        "amount": _number(row.get("amount")),
                        "turnover_rate": _scaled(row.get("turnoverratio")),
                        "pe": _number(row.get("per")),
                        "market_cap": _number(row.get("nmc")),
                        "source": "新浪财经公开行情",
                    }
                )
                if len(items) >= size:
                    break
            if len(items) >= size or len(payload) < 100:
                break
        if not items:
            raise MarketDataError("新浪公开行情未返回可用股票池")
        return items[:size]

    return load()


def _get_eastmoney_universe(limit: int = 40) -> list[dict]:
    """Return the most actively traded A shares ordered by turnover value."""
    size = max(10, min(int(limit), 1500))
    fields = "f12,f14,f2,f3,f5,f6,f8,f9,f20,f100"
    market_filter = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"

    def load():
        items = []
        seen = set()
        for page in range(1, (size + 99) // 100 + 1):
            url = (
                f"{MARKET_LIST_URL}?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2"
                f"&fid=f6&fs={market_filter}&fields={fields}"
            )
            payload = _cached(
                f"eastmoney-universe:{size}:{page}",
                ttl=60,
                loader=lambda url=url: _fetch_json(url),
            )
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not rows:
                break
            for row in rows:
                symbol = str(row.get("f12") or "").strip()
                name = _clean_name(row.get("f14"), symbol)
                if not _is_tradable_share(symbol, name) or symbol in seen:
                    continue
                seen.add(symbol)
                items.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "industry": str(row.get("f100") or "未分类").strip(),
                        "price": _number(row.get("f2")),
                        "change_pct": _scaled(row.get("f3")),
                        "volume": _number(row.get("f5")),
                        "amount": _number(row.get("f6")),
                        "turnover_rate": _scaled(row.get("f8")),
                        "pe": _number(row.get("f9")),
                        "market_cap": _number(row.get("f20")),
                        "source": "东方财富公开行情",
                    }
                )
                if len(items) >= size:
                    break
            if len(items) >= size or len(rows) < 100:
                break
        if not items:
            raise MarketDataError("未获取到可用于研究的股票池")
        return items[:size]

    return load()


def get_liquid_universe(limit: int = 40) -> list[dict]:
    """Load a live liquid-share universe, falling back to Sina when needed."""
    size = max(10, min(int(limit), 1500))
    try:
        return _get_eastmoney_universe(size)
    except MarketDataError:
        return _get_sina_universe(size)


def search_stocks(keyword: str, limit: int = 8) -> list[dict]:
    query = (keyword or "").strip()
    if not query:
        return []
    encoded = urllib.parse.quote(query)
    url = (
        f"{SEARCH_URL}?input={encoded}&type=14&token={SEARCH_TOKEN}"
        f"&count={max(1, min(int(limit), 20))}"
    )
    payload = _cached(f"search:{query}:{limit}", ttl=900, loader=lambda: _fetch_json(url))
    data = (payload.get("QuotationCodeTable") or {}).get("Data") or []
    items = []
    for item in data:
        classify = str(item.get("Classify") or "")
        security_type = str(item.get("SecurityTypeName") or "")
        if classify and classify != "AStock" and "A" not in security_type:
            continue
        symbol = str(item.get("Code") or item.get("UnifiedCode") or "").strip()
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        items.append(
            {
                "symbol": symbol,
                "name": _clean_name(item.get("Name"), symbol),
                "quote_id": str(item.get("QuoteID") or to_quote_id(symbol)),
                "market": str(item.get("SecurityTypeName") or ""),
            }
        )
    return items[:limit]


def _get_tencent_quote(symbol: str) -> dict:
    code = normalize_symbol(symbol)
    tencent_code = _tencent_code(code)
    payload = _cached(
        f"tencent-quote:{code}",
        ttl=15,
        loader=lambda: _fetch_text_gb18030(
            f"{TENCENT_QUOTE_URL}{tencent_code}",
            referer="https://gu.qq.com/",
        ),
    )
    marker = f'v_{tencent_code}="'
    start = payload.find(marker)
    if start < 0:
        raise MarketDataError(f"腾讯证券未返回股票 {code} 的行情")
    start += len(marker)
    end = payload.find('"', start)
    fields = payload[start:end].split("~")
    if len(fields) < 46:
        raise MarketDataError(f"腾讯证券行情字段不完整: {code}")
    amount = None
    traded = fields[35].split("/")
    if len(traded) >= 3:
        amount = _number(traded[2])
    timestamp = fields[30]
    updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if len(timestamp) >= 14 and timestamp.isdigit():
        updated_at = (
            f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]} "
            f"{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}"
        )
    return {
        "symbol": code,
        "name": _clean_name(fields[1], code),
        "price": _number(fields[3]),
        "open": _number(fields[5]),
        "high": _number(fields[33]),
        "low": _number(fields[34]),
        "prev_close": _number(fields[4]),
        "change": _number(fields[31]),
        "change_pct": _scaled(fields[32]),
        "turnover_rate": _scaled(fields[38]),
        "pe": _number(fields[39]),
        "pb": None,
        "volume": _number(fields[6]),
        "amount": amount if amount is not None else _number(fields[37]) * 10_000,
        "market_cap": _number(fields[45]) * 100_000_000 if _number(fields[45]) else None,
        "float_market_cap": _number(fields[44]) * 100_000_000 if _number(fields[44]) else None,
        "industry": "A股",
        "region": "",
        "concepts": "",
        "source": "腾讯证券公开行情",
        "updated_at": updated_at,
    }


def _get_eastmoney_quote(symbol: str) -> dict:
    code = normalize_symbol(symbol)
    quote_id = to_quote_id(code)
    fields = (
        "f57,f58,f43,f44,f45,f46,f47,f48,f60,f116,f117,"
        "f162,f167,f168,f169,f170,f127,f128,f129"
    )
    url = f"{QUOTE_URL}?secid={quote_id}&fields={fields}"
    payload = _cached(f"quote:{code}", ttl=20, loader=lambda: _fetch_json(url))
    data = payload.get("data")
    if not data:
        raise MarketDataError(f"未找到股票 {code} 的实时行情")
    return {
        "symbol": str(data.get("f57") or code),
        "name": _clean_name(data.get("f58"), code),
        "price": _scaled(data.get("f43")),
        "open": _scaled(data.get("f46")),
        "high": _scaled(data.get("f44")),
        "low": _scaled(data.get("f45")),
        "prev_close": _scaled(data.get("f60")),
        "change": _scaled(data.get("f169")),
        "change_pct": _percentage(data.get("f170")),
        "turnover_rate": _percentage(data.get("f168")),
        "pe": _scaled(data.get("f162")),
        "pb": _scaled(data.get("f167")),
        "volume": data.get("f47"),
        "amount": data.get("f48"),
        "market_cap": data.get("f116"),
        "float_market_cap": data.get("f117"),
        "industry": str(data.get("f127") or "未分类"),
        "region": str(data.get("f128") or ""),
        "concepts": str(data.get("f129") or ""),
        "source": "东方财富",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _get_sina_quote(symbol: str) -> dict:
    code = normalize_symbol(symbol)
    sina_code = _tencent_code(code)
    payload = _cached(
        f"sina-quote:{code}",
        ttl=15,
        loader=lambda: _fetch_text_gb18030(
            f"{SINA_QUOTE_URL}{sina_code}",
            referer="https://finance.sina.com.cn/",
        ),
    )
    marker = f'var hq_str_{sina_code}="'
    start = payload.find(marker)
    if start < 0:
        raise MarketDataError(f"新浪财经未返回股票 {code} 的行情")
    start += len(marker)
    end = payload.find('"', start)
    fields = payload[start:end].split(",")
    if len(fields) < 32:
        raise MarketDataError(f"新浪财经行情字段不完整: {code}")
    price = _number(fields[3])
    prev_close = _number(fields[2])
    if not price or price <= 0:
        raise MarketDataError(f"新浪财经未返回股票 {code} 的有效现价")
    return {
        "symbol": code,
        "name": _clean_name(fields[0], code),
        "price": price,
        "open": _number(fields[1]),
        "high": _number(fields[4]),
        "low": _number(fields[5]),
        "prev_close": prev_close,
        "change": round(price - prev_close, 4) if prev_close else None,
        "change_pct": (
            round(price / prev_close - 1.0, 6) if prev_close else None
        ),
        "turnover_rate": None,
        "pe": None,
        "pb": None,
        "volume": _number(fields[8]),
        "amount": _number(fields[9]),
        "market_cap": None,
        "float_market_cap": None,
        "industry": "A股",
        "source": "新浪财经公开行情",
        "updated_at": f"{fields[30]} {fields[31]}",
    }


def get_quote(symbol: str) -> dict:
    """Load a live quote, falling back to Tencent when Eastmoney is unavailable."""
    try:
        return _get_eastmoney_quote(symbol)
    except MarketDataError:
        return _get_tencent_quote(symbol)


def get_quote_comparison(symbol: str) -> dict:
    """Fetch two independent quotes and compare the latest prices."""
    code = normalize_symbol(symbol)
    sources = []
    for source_id, loader in (
        ("eastmoney", _get_eastmoney_quote),
        ("tencent", _get_tencent_quote),
        ("sina", _get_sina_quote),
    ):
        try:
            quote = loader(code)
            sources.append(
                {
                    "id": source_id,
                    "source": quote.get("source"),
                    "price": quote.get("price"),
                    "updated_at": quote.get("updated_at"),
                }
            )
        except Exception as exc:
            sources.append(
                {"id": source_id, "error": str(exc)}
            )
    prices = [
        _number(item.get("price"))
        for item in sources
        if _number(item.get("price")) and _number(item.get("price")) > 0
    ]
    available = [
        (item.get("id"), _number(item.get("price")))
        for item in sources
        if _number(item.get("price")) and _number(item.get("price")) > 0
    ]
    gap = None
    if len(prices) >= 2:
        midpoint = sum(prices[:2]) / 2.0
        gap = abs(prices[0] - prices[1]) / midpoint if midpoint else None
    compared = None
    if len(available) >= 2:
        pair = available[:2]
        midpoint = (pair[0][1] + pair[1][1]) / 2.0
        gap = abs(pair[0][1] - pair[1][1]) / midpoint if midpoint else None
        compared = [pair[0][0], pair[1][0]]
    if gap is None:
        status = "unavailable"
    elif gap <= 0.01:
        status = "verified"
    else:
        status = "mismatch"
    return {
        "symbol": code,
        "status": status,
        "price_gap_pct": round(gap, 6) if gap is not None else None,
        "compared": compared,
        "sources": sources,
    }


def _get_eastmoney_history(symbol: str, days: int = 252) -> list[dict]:
    code = normalize_symbol(symbol)
    limit = max(30, min(int(days), 500))
    quote_id = to_quote_id(code)
    fields1 = "f1,f2,f3,f4,f5,f6"
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    url = (
        f"{HISTORY_URL}?secid={quote_id}&klt=101&fqt=1&lmt={limit}"
        f"&end=20500101&fields1={fields1}&fields2={fields2}"
    )
    payload = _cached(f"history:{code}:{limit}", ttl=180, loader=lambda: _fetch_json(url))
    data = payload.get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
                "amplitude": float(parts[7]),
                "change_pct": float(parts[8]),
                "change": float(parts[9]),
                "turnover": float(parts[10]),
                "source": "东方财富前复权日行情",
            }
        )
    if not rows:
        raise MarketDataError(f"未找到股票 {code} 的历史行情")
    return rows


def _get_tencent_history(symbol: str, limit: int) -> list[dict]:
    code = normalize_symbol(symbol)
    tencent_code = _tencent_code(code)
    request_limit = min(limit + 1, 501)
    url = (
        f"{TENCENT_HISTORY_URL}?param={tencent_code},day,,,{request_limit},qfq"
    )
    payload = _fetch_json(url, referer="https://gu.qq.com/")
    data = payload.get("data") or {}
    stock = data.get(tencent_code) or {}
    lines = stock.get("qfqday") or stock.get("day") or []
    rows = []
    previous_close = None
    for parts in lines:
        if not isinstance(parts, list) or len(parts) < 6:
            continue
        try:
            open_price = float(parts[1])
            close = float(parts[2])
            high = float(parts[3])
            low = float(parts[4])
            volume = float(parts[5])
        except (TypeError, ValueError):
            continue
        change = close - previous_close if previous_close else 0.0
        change_pct = change / previous_close if previous_close else 0.0
        amplitude = (high - low) / previous_close if previous_close else 0.0
        rows.append(
            {
                "date": str(parts[0]),
                "open": open_price,
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": volume * 100.0 * close,
                "amplitude": amplitude,
                "change_pct": change_pct,
                "change": change,
                "turnover": 0.0,
                "source": "腾讯证券前复权日行情（成交额估算）",
            }
        )
        previous_close = close
    if not rows:
        raise MarketDataError(f"腾讯证券未找到股票 {code} 的历史行情")
    return rows[-limit:]


def _get_sina_history(symbol: str, limit: int) -> list[dict]:
    code = normalize_symbol(symbol)
    sina_code = _tencent_code(code)
    query = urllib.parse.urlencode(
        {"symbol": sina_code, "scale": 240, "ma": "no", "datalen": min(limit + 1, 1023)}
    )
    payload = _fetch_json(
        f"{SINA_HISTORY_URL}?{query}",
        referer="https://finance.sina.com.cn/",
    )
    if not isinstance(payload, list):
        raise MarketDataError("新浪历史行情返回格式异常")
    rows = []
    previous_close = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            open_price = float(item["open"])
            close = float(item["close"])
            high = float(item["high"])
            low = float(item["low"])
            volume = float(item["volume"])
            day = str(item["day"])
        except (KeyError, TypeError, ValueError):
            continue
        change = close - previous_close if previous_close else 0.0
        change_pct = change / previous_close if previous_close else 0.0
        amplitude = (high - low) / previous_close if previous_close else 0.0
        rows.append(
            {
                "date": day,
                "open": open_price,
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": volume * close,
                "amplitude": amplitude,
                "change_pct": change_pct,
                "change": change,
                "turnover": 0.0,
                "source": "新浪财经日行情（成交额估算）",
            }
        )
        previous_close = close
    if not rows:
        raise MarketDataError(f"新浪财经未找到股票 {code} 的历史行情")
    return rows[-limit:]


def get_history(symbol: str, days: int = 252) -> list[dict]:
    """Load daily bars with Eastmoney, Tencent, then Sina fallback."""
    code = normalize_symbol(symbol)
    limit = max(30, min(int(days), 500))
    try:
        return _get_eastmoney_history(code, limit)
    except MarketDataError:
        try:
            return _cached(
                f"tencent-history:{code}:{limit}",
                ttl=180,
                loader=lambda: _get_tencent_history(code, limit),
            )
        except MarketDataError as tencent_error:
            try:
                return _cached(
                    f"sina-history:{code}:{limit}",
                    ttl=180,
                    loader=lambda: _get_sina_history(code, limit),
                )
            except MarketDataError as sina_error:
                raise MarketDataError(
                    f"腾讯历史行情不可用：{tencent_error}；新浪历史行情不可用：{sina_error}"
                ) from sina_error


def get_market_snapshot(symbol: str, days: int = 252) -> dict:
    return {
        "quote": get_quote(symbol),
        "history": get_history(symbol, days=days),
    }
