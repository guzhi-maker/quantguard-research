"""Public Eastmoney news and announcement connectors."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from data_agent.store import CHINA_TZ, now_iso


NEWS_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantGuard/1.1"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


class NewsDataError(RuntimeError):
    """Public news or announcement request failed."""


def _fetch_text(url: str, referer: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    proxy = str(os.getenv("NEWS_PROXY") or "").strip()
    opener = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        if proxy
        else urllib.request.build_opener()
    )
    try:
        with opener.open(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise NewsDataError(f"公开资讯接口请求失败: {exc}") from exc


def _clean_text(value: Any, limit: int = 240) -> str:
    text = html.unescape(TAG_RE.sub("", str(value or "")))
    text = SPACE_RE.sub(" ", text).strip()
    return text[:limit]


def _parse_jsonp(value: str) -> dict:
    text = value.strip()
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        raise NewsDataError("东方财富资讯接口返回格式异常")
    try:
        payload = json.loads(text[start + 1 : end])
    except json.JSONDecodeError as exc:
        raise NewsDataError("东方财富资讯接口返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise NewsDataError("东方财富资讯接口返回类型异常")
    return payload


def _age_hours(value: str) -> float | None:
    try:
        published = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=CHINA_TZ
        )
    except (TypeError, ValueError):
        try:
            published = datetime.fromisoformat(str(value)).replace(tzinfo=CHINA_TZ)
        except (TypeError, ValueError):
            return None
    return round(
        max(0.0, (datetime.now(CHINA_TZ) - published).total_seconds() / 3600), 1
    )


def fetch_stock_news(name: str, symbol: str, limit: int = 8) -> list[dict]:
    """Search recent public news for one security through Eastmoney."""
    keyword = _clean_text(name or symbol, 40)
    size = max(1, min(int(limit), 12))
    payload = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "time",
                "pageIndex": 1,
                "pageSize": size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    query = urllib.parse.urlencode(
        {"cb": "callback", "param": json.dumps(payload, ensure_ascii=False)}
    )
    data = _parse_jsonp(
        _fetch_text(f"{NEWS_SEARCH_URL}?{query}", "https://so.eastmoney.com/")
    )
    rows = ((data.get("result") or {}).get("cmsArticleWebOld") or [])
    fetched_at = now_iso()
    items = []
    for row in rows:
        title = _clean_text(row.get("title"), 160)
        if not title:
            continue
        published_at = str(row.get("date") or "")
        items.append(
            {
                "symbol": symbol,
                "category": "news",
                "title": title,
                "summary": _clean_text(row.get("content"), 320),
                "published_at": published_at,
                "age_hours": _age_hours(published_at),
                "media": _clean_text(row.get("mediaName"), 60),
                "url": str(row.get("url") or ""),
                "source": "东方财富资讯搜索",
                "fetched_at": fetched_at,
            }
        )
    return items[:size]


def fetch_stock_announcements(symbol: str, limit: int = 6) -> list[dict]:
    """Fetch recent listed-company announcements for one security."""
    size = max(1, min(int(limit), 10))
    query = urllib.parse.urlencode(
        {
            "sr": "-1",
            "page_size": size,
            "page_index": 1,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": symbol,
        }
    )
    raw = _fetch_text(
        f"{ANNOUNCEMENT_URL}?{query}", "https://data.eastmoney.com/"
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NewsDataError("公告接口返回了无效 JSON") from exc
    rows = ((payload.get("data") or {}).get("list") or [])
    fetched_at = now_iso()
    items = []
    for row in rows:
        title = _clean_text(row.get("title_ch") or row.get("title"), 180)
        if not title:
            continue
        art_code = str(row.get("art_code") or "")
        columns = row.get("columns") or []
        category_names = [
            str(column.get("column_name") or "").strip()
            for column in columns
            if isinstance(column, dict) and column.get("column_name")
        ]
        items.append(
            {
                "symbol": symbol,
                "category": "announcement",
                "title": title,
                "summary": " / ".join(category_names) or "上市公司公告",
                "published_at": str(
                    row.get("notice_date") or row.get("display_time") or ""
                ),
                "age_hours": _age_hours(
                    str(row.get("notice_date") or row.get("display_time") or "")
                ),
                "media": "上市公司公告",
                "url": (
                    f"https://data.eastmoney.com/notices/detail/{symbol}/{art_code}.html"
                    if art_code
                    else "https://data.eastmoney.com/notices/"
                ),
                "source": "东方财富公告",
                "fetched_at": fetched_at,
            }
        )
    return items[:size]


def fetch_symbol_intelligence(
    name: str,
    symbol: str,
    news_limit: int = 8,
    announcement_limit: int = 6,
) -> dict:
    """Load both news and announcements while preserving partial failures."""
    errors = []
    news = []
    announcements = []
    try:
        news = fetch_stock_news(name, symbol, news_limit)
    except Exception as exc:
        errors.append({"channel": "news", "error": str(exc)})
    try:
        announcements = fetch_stock_announcements(symbol, announcement_limit)
    except Exception as exc:
        errors.append({"channel": "announcement", "error": str(exc)})
    return {
        "symbol": symbol,
        "name": name,
        "news": news,
        "announcements": announcements,
        "errors": errors,
        "fetched_at": now_iso(),
    }
