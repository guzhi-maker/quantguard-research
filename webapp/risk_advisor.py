"""组合风控分析：集中度、行业暴露、波动、回撤与相关性。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import RiskConfig

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _safe_float(value: Optional[float], default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean_holdings(holdings: list[dict]) -> tuple[list[dict], float]:
    cleaned = []
    total = 0.0
    for item in holdings or []:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol.isdigit() or len(symbol) != 6:
            continue
        weight = _safe_float(item.get("weight"))
        if weight <= 0:
            continue
        cleaned.append({"symbol": symbol, "weight_pct": weight})
        total += weight
    if not cleaned:
        raise ValueError("请至少添加一只持仓股票")
    if total > 100.001:
        raise ValueError(f"总仓位为 {total:.1f}%，不能超过 100%")
    return cleaned, total


def _close_series(history: list[dict]) -> pd.Series:
    rows = []
    for item in history or []:
        try:
            rows.append((pd.Timestamp(item["date"]), float(item["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return pd.Series(dtype=float)
    series = pd.Series(dict(rows), dtype=float).sort_index()
    return series[~series.index.duplicated(keep="last")]


def _risk_level(score: int) -> tuple[str, str]:
    if score < 25:
        return "low", "低风险"
    if score < 50:
        return "medium", "关注"
    if score < 72:
        return "high", "高风险"
    return "critical", "严重超限"


def _suggestion(
    severity: str,
    category: str,
    title: str,
    detail: str,
    action: str,
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "action": action,
    }


def analyze_portfolio(
    holdings: list[dict],
    market_data: dict[str, dict],
    config: Optional[RiskConfig] = None,
) -> dict:
    """根据实时行情和历史收益生成组合风险报告。"""
    config = config or RiskConfig()
    positions, total_weight_pct = _clean_holdings(holdings)
    missing = [p["symbol"] for p in positions if p["symbol"] not in market_data]
    if missing:
        raise ValueError(f"缺少行情数据: {', '.join(missing)}")

    returns_map = {}
    holding_rows = []
    latest_times = []
    quotes = {}
    for position in positions:
        symbol = position["symbol"]
        snapshot = market_data[symbol]
        quote = snapshot.get("quote") or {}
        quotes[symbol] = quote
        close = _close_series(snapshot.get("history") or [])
        if close.empty:
            raise ValueError(f"{symbol} 历史行情为空")
        returns = close.pct_change().dropna()
        returns_map[symbol] = returns
        weight = position["weight_pct"] / 100.0
        vol_60 = float(returns.tail(60).std(ddof=1) * np.sqrt(252)) if len(returns) > 3 else 0.0
        ret_20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) > 21 else 0.0
        holding_rows.append(
            {
                "symbol": symbol,
                "name": quote.get("name") or symbol,
                "industry": quote.get("industry") or "未分类",
                "price": _safe_float(quote.get("price")),
                "change_pct": _safe_float(quote.get("change_pct")),
                "weight_pct": round(position["weight_pct"], 2),
                "return_20d": round(ret_20, 6),
                "volatility_60d": round(vol_60, 6),
                "turnover_rate": _safe_float(quote.get("turnover_rate")),
                "market_cap": _safe_float(quote.get("market_cap")),
            }
        )
        if quote.get("updated_at"):
            latest_times.append(str(quote["updated_at"]))

    returns_df = pd.DataFrame(returns_map).sort_index().fillna(0.0)
    weights = pd.Series(
        {p["symbol"]: p["weight_pct"] / 100.0 for p in positions}, dtype=float
    )
    portfolio_returns = (returns_df * weights).sum(axis=1)
    nav = (1.0 + portfolio_returns).cumprod()
    if not nav.empty:
        nav.iloc[0] = 1.0
    annual_volatility = (
        float(portfolio_returns.std(ddof=1) * np.sqrt(252))
        if len(portfolio_returns) > 2
        else 0.0
    )
    max_drawdown = float((nav / nav.cummax() - 1.0).min()) if not nav.empty else 0.0

    stock_weights = weights[weights > 0].sort_values(ascending=False)
    invested_weight = float(stock_weights.sum())
    normalized_weights = stock_weights / invested_weight if invested_weight > 0 else stock_weights
    hhi = float((normalized_weights**2).sum()) if not normalized_weights.empty else 0.0
    top3_weight = float(stock_weights.head(3).sum())
    max_single_weight = float(stock_weights.max()) if not stock_weights.empty else 0.0

    if returns_df.shape[1] > 1:
        corr = returns_df.corr()
        pairs = [
            float(corr.iloc[i, j])
            for i in range(len(corr.columns))
            for j in range(i + 1, len(corr.columns))
            if not pd.isna(corr.iloc[i, j])
        ]
        average_correlation = float(np.mean(pairs)) if pairs else 0.0
    else:
        average_correlation = 0.0

    sector_map: dict[str, float] = {}
    for symbol, weight in stock_weights.items():
        industry = quotes.get(symbol, {}).get("industry") or "未分类"
        sector_map[industry] = sector_map.get(industry, 0.0) + float(weight)
    sector_exposure = [
        {"sector": sector, "weight": round(weight, 6)}
        for sector, weight in sorted(sector_map.items(), key=lambda item: item[1], reverse=True)
    ]
    max_sector_weight = max(sector_map.values(), default=0.0)

    score = 0.0
    score += max(0.0, (max_single_weight - config.max_single_position) * 180.0)
    score += max(0.0, (max_sector_weight - config.max_industry_exposure) * 120.0)
    score += max(0.0, (annual_volatility - 0.15) * 90.0)
    score += max(0.0, (abs(max_drawdown) - 0.10) * 110.0)
    score += max(0.0, (average_correlation - 0.55) * 45.0)
    score += max(0.0, (top3_weight - 0.65) * 60.0)
    cash_weight = max(0.0, 1.0 - total_weight_pct / 100.0)
    if cash_weight < 0.03:
        score += 4.0
    score = int(max(0, min(100, round(score))))
    risk_key, risk_label = _risk_level(score)

    suggestions = []
    largest_holding = max(holding_rows, key=lambda item: item["weight_pct"])
    if max_single_weight > config.max_single_position + 1e-9:
        largest = largest_holding
        suggestions.append(
            _suggestion(
                "critical",
                "仓位约束",
                "单票仓位超过硬性上限",
                f"{largest['name']} 当前权重 {max_single_weight:.1%}，"
                f"高于单票 {config.max_single_position:.0%} 上限。",
                f"将 {largest['name']} 至少下调 "
                f"{(max_single_weight - config.max_single_position) * 100:.1f} 个百分点。",
            )
        )
    if max_sector_weight > config.max_industry_exposure + 1e-9:
        sector, weight = max(sector_map.items(), key=lambda item: item[1])
        suggestions.append(
            _suggestion(
                "critical",
                "行业暴露",
                "行业集中度超过组合阈值",
                f"{sector} 行业暴露 {weight:.1%}，高于 "
                f"{config.max_industry_exposure:.0%} 上限。",
                "优先降低同一行业内权重最高标的，补充低相关行业资产。",
            )
        )
    if annual_volatility > 0.25:
        suggestions.append(
            _suggestion(
                "warning",
                "组合波动",
                "年化波动率偏高",
                f"组合年化波动率约 {annual_volatility:.1%}，已高于 25% 的风险观察线。",
                "降低高波动个股权重，保留现金缓冲，或加入低相关资产。",
            )
        )
    if abs(max_drawdown) > 0.20:
        suggestions.append(
            _suggestion(
                "warning",
                "回撤控制",
                "历史最大回撤触及预警区",
                f"样本期最大回撤 {max_drawdown:.1%}，组合修复能力需要复核。",
                "设置组合回撤预警线与个股止损线，避免单次亏损继续放大。",
            )
        )
    if average_correlation > 0.65:
        suggestions.append(
            _suggestion(
                "warning",
                "相关性",
                "持仓走势高度同步",
                f"持仓两两平均相关系数 {average_correlation:.2f}，分散化效果有限。",
                "增加不同驱动逻辑或行业暴露的标的，降低同涨同跌风险。",
            )
        )
    if top3_weight > 0.75:
        suggestions.append(
            _suggestion(
                "warning",
                "集中度",
                "前三大持仓占比过高",
                f"前三大持仓合计 {top3_weight:.1%}，组合收益高度依赖少数标的。",
                "将前三大持仓合计权重控制在 75% 以下，并为单一标的设置权重上限。",
            )
        )
    if cash_weight < 0.03:
        suggestions.append(
            _suggestion(
                "info",
                "现金缓冲",
                "现金缓冲偏低",
                f"当前现金及未配置仓位约 {cash_weight:.1%}。",
                "建议保留至少 3%-5% 的机动仓位，用于波动期调整。",
            )
        )
    if not suggestions:
        suggestions.append(
            _suggestion(
                "info",
                "组合结构",
                "当前组合未触发主要硬性阈值",
                "集中度、行业暴露、波动和回撤指标均处于可接受区间。",
                "保持每周复核权重偏离，并关注个股公告与行业事件。",
            )
        )
    suggestions.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 9))

    if risk_key == "critical":
        headline = "组合存在多项硬性超限，建议先降低集中度再调整结构"
    elif risk_key == "high":
        headline = "组合风险偏高，优先处理超限仓位与高相关暴露"
    elif risk_key == "medium":
        headline = "组合整体可控，但仍有若干指标需要跟踪"
    else:
        headline = "组合结构较为均衡，维持当前风控约束"

    chart_step = max(1, len(nav) // 120)
    curve = [
        {"date": pd.Timestamp(date).strftime("%Y-%m-%d"), "value": round(float(value), 6)}
        for date, value in nav.iloc[::chart_step].items()
    ]
    if not nav.empty and (not curve or curve[-1]["date"] != nav.index[-1].strftime("%Y-%m-%d")):
        curve.append(
            {
                "date": pd.Timestamp(nav.index[-1]).strftime("%Y-%m-%d"),
                "value": round(float(nav.iloc[-1]), 6),
            }
        )

    holding_rows.sort(key=lambda item: item["weight_pct"], reverse=True)
    for row in holding_rows:
        row["risk_flags"] = []
        if row["weight_pct"] / 100.0 > config.max_single_position:
            row["risk_flags"].append("权重超限")
        if row["volatility_60d"] > 0.45:
            row["risk_flags"].append("高波动")
        if row["return_20d"] < -0.10:
            row["risk_flags"].append("近20日回撤")

    return {
        "summary": {
            "score": score,
            "level": risk_key,
            "label": risk_label,
            "headline": headline,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_as_of": max(latest_times) if latest_times else "",
        },
        "metrics": {
            "annual_volatility": round(annual_volatility, 6),
            "max_drawdown": round(max_drawdown, 6),
            "max_single_weight": round(max_single_weight, 6),
            "max_sector_weight": round(max_sector_weight, 6),
            "average_correlation": round(average_correlation, 4),
            "concentration_hhi": round(hhi, 4),
            "top3_weight": round(top3_weight, 6),
            "cash_weight": round(cash_weight, 6),
            "invested_weight": round(invested_weight, 6),
        },
        "thresholds": {
            "max_single_position": config.max_single_position,
            "max_industry_exposure": config.max_industry_exposure,
            "max_drawdown_warn": config.max_drawdown_warn,
        },
        "holdings": holding_rows,
        "sector_exposure": sector_exposure,
        "suggestions": suggestions,
        "nav": curve,
    }
