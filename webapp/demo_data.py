"""Deterministic offline market data for classroom-safe QuantGuard demos."""

from __future__ import annotations

import math
import threading
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

DEMO_VERSION = "demo-2026.11.04-v1"
DEMO_SEED = 20261104
DEMO_DAYS = 740
DEMO_SYMBOLS = 120
DEMO_AS_OF = "2026-10-30"

_INDUSTRIES = (
    "电子",
    "计算机",
    "通信",
    "医药",
    "电力设备",
    "银行",
    "食品饮料",
    "机械设备",
    "有色金属",
    "公用事业",
)
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}


def _build() -> dict[str, Any]:
    rng = np.random.default_rng(DEMO_SEED)
    dates = pd.bdate_range(end=DEMO_AS_OF, periods=DEMO_DAYS)
    market = rng.normal(0.00018, 0.0085, DEMO_DAYS)
    market = pd.Series(market, index=dates)
    industry_returns = {
        industry: rng.normal(0.00004, 0.0045, DEMO_DAYS)
        for industry in _INDUSTRIES
    }

    stocks: dict[str, dict[str, Any]] = {}
    close_panel: dict[str, pd.Series] = {}
    amount_panel: dict[str, pd.Series] = {}
    for index in range(DEMO_SYMBOLS):
        symbol = f"60{index + 1:04d}"
        industry = _INDUSTRIES[index % len(_INDUSTRIES)]
        beta = 0.72 + (index % 7) * 0.07
        idiosyncratic = rng.normal(0.0, 0.012 + (index % 5) * 0.0012, DEMO_DAYS)
        cycle = 0.0016 * np.sin(np.arange(DEMO_DAYS) / (9.0 + index % 11))
        returns = beta * market.to_numpy() + industry_returns[industry] + idiosyncratic + cycle
        returns = np.clip(returns, -0.095, 0.095)
        base = 8.0 + (index % 24) * 2.6
        close = base * np.exp(np.cumsum(returns))
        close = np.maximum(close, 2.0)
        liquidity = rng.lognormal(mean=18.15 + (index % 6) * 0.09, sigma=0.18, size=DEMO_DAYS)
        amount = liquidity * (0.88 + 0.24 * np.abs(np.sin(np.arange(DEMO_DAYS) / 17.0)))
        amount = amount * (0.8 + 0.4 * close / close.mean())
        volume = amount / close
        close_series = pd.Series(close, index=dates)
        amount_series = pd.Series(amount, index=dates)
        volume_series = pd.Series(volume, index=dates)
        close_panel[symbol] = close_series
        amount_panel[symbol] = amount_series
        stocks[symbol] = {
            "symbol": symbol,
            "name": f"演示样本{index + 1:03d}",
            "industry": industry,
            "market": "沪A",
            "close": close_series,
            "amount": amount_series,
            "volume": volume_series,
        }

    return {
        "dates": dates,
        "stocks": stocks,
        "close": pd.DataFrame(close_panel),
        "amount": pd.DataFrame(amount_panel),
    }


def _demo() -> dict[str, Any]:
    with _CACHE_LOCK:
        if "data" not in _CACHE:
            _CACHE["data"] = _build()
        return _CACHE["data"]


def _quote(stock: dict[str, Any]) -> dict[str, Any]:
    close = stock["close"]
    latest = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else latest
    change = latest / previous - 1.0
    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "price": round(latest, 4),
        "open": round(float(close.iloc[-1] * 0.997), 4),
        "high": round(float(close.iloc[-1] * 1.012), 4),
        "low": round(float(close.iloc[-1] * 0.988), 4),
        "prev_close": round(previous, 4),
        "change": round(latest - previous, 4),
        "change_pct": round(change, 6),
        "turnover_rate": 0.0125,
        "pe": 18.0,
        "pb": 2.2,
        "volume": round(float(stock["volume"].iloc[-1]), 0),
        "amount": round(float(stock["amount"].iloc[-1]), 2),
        "market_cap": 50_000_000_000.0,
        "float_market_cap": 35_000_000_000.0,
        "industry": stock["industry"],
        "region": "演示",
        "concepts": "确定性离线数据",
        "source": "QuantGuard 内置演示数据",
        "updated_at": f"{DEMO_AS_OF} 15:00:00",
        "version": DEMO_VERSION,
    }


def get_liquid_universe(limit: int = 120) -> list[dict]:
    data = _demo()
    size = max(1, min(int(limit), DEMO_SYMBOLS))
    ranked = sorted(
        data["stocks"].values(),
        key=lambda stock: float(stock["amount"].iloc[-1]),
        reverse=True,
    )
    return [
        {
            "symbol": stock["symbol"],
            "name": stock["name"],
            "industry": stock["industry"],
            "price": round(float(stock["close"].iloc[-1]), 4),
            "change_pct": round(
                float(stock["close"].iloc[-1] / stock["close"].iloc[-2] - 1.0),
                6,
            ),
            "volume": round(float(stock["volume"].iloc[-1]), 0),
            "amount": round(float(stock["amount"].iloc[-1]), 2),
            "turnover_rate": 0.0125,
            "pe": 18.0,
            "market_cap": 50_000_000_000.0,
        }
        for stock in ranked[:size]
    ]


def get_history(symbol: str, days: int = DEMO_DAYS) -> list[dict]:
    data = _demo()
    code = str(symbol or "").zfill(6)
    stock = data["stocks"].get(code)
    if not stock:
        raise ValueError(f"演示股票 {code} 不存在")
    limit = max(30, min(int(days), DEMO_DAYS))
    close = stock["close"].iloc[-limit:]
    amount = stock["amount"].iloc[-limit:]
    volume = stock["volume"].iloc[-limit:]
    rows = []
    for offset, (date, value) in enumerate(close.items()):
        price = float(value)
        previous = float(close.iloc[offset - 1]) if offset else price
        rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "open": round(price * (0.998 + 0.004 * math.sin(offset / 5.0)), 4),
                "close": round(price, 4),
                "high": round(price * 1.008, 4),
                "low": round(price * 0.992, 4),
                "volume": round(float(volume.iloc[offset]), 0),
                "amount": round(float(amount.iloc[offset]), 2),
                "amplitude": 1.6,
                "change_pct": round((price / previous - 1.0) * 100.0, 4),
                "change": round(price - previous, 4),
                "turnover": 1.25,
            }
        )
    return rows


def search_stocks(keyword: str, limit: int = 8) -> list[dict]:
    query = str(keyword or "").strip()
    if not query:
        return []
    matches = []
    for stock in _demo()["stocks"].values():
        if query in stock["symbol"] or query in stock["name"]:
            matches.append(
                {
                    "symbol": stock["symbol"],
                    "name": stock["name"],
                    "quote_id": f"demo.{stock['symbol']}",
                    "market": "演示沪A",
                }
            )
    return matches[: max(1, min(int(limit), 20))]


def get_quote(symbol: str) -> dict:
    code = str(symbol or "").zfill(6)
    stock = _demo()["stocks"].get(code)
    if not stock:
        raise ValueError(f"演示股票 {code} 不存在")
    return _quote(stock)


def get_market_snapshot(symbol: str, days: int = 252) -> dict:
    return {"quote": get_quote(symbol), "history": get_history(symbol, days=days)}


def data_meta() -> dict:
    return {
        "mode": "demo",
        "version": DEMO_VERSION,
        "seed": DEMO_SEED,
        "as_of": DEMO_AS_OF,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "QuantGuard 内置确定性演示数据",
    }
