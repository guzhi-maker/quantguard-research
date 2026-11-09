"""Deterministic market-data checks and stock risk scoring."""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any

from data_agent.store import CHINA_TZ


SEVERE_NEWS_TERMS = {
    "立案": "监管立案",
    "调查": "监管调查",
    "处罚": "行政处罚",
    "退市": "退市风险",
    "违约": "债务违约",
    "冻结": "资产冻结",
    "爆雷": "重大负面事件",
    "诉讼": "重大诉讼",
    "停牌": "停牌事项",
    "风险提示": "风险提示",
}
MODERATE_NEWS_TERMS = {
    "减持": "股东减持",
    "下滑": "业绩或经营下滑",
    "下降": "指标下降",
    "终止": "项目或交易终止",
    "问询": "监管问询",
    "质押": "股权质押",
    "大跌": "市场大跌",
    "波动": "波动提示",
}
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
LEVEL_LABELS = {
    "low": "低风险",
    "medium": "关注",
    "high": "高风险",
    "critical": "严重预警",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if abs(number) > 1.5:
        return number / 100.0
    return number


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _return_over(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return None
    return values[-1] / values[-periods - 1] - 1.0


def compute_market_metrics(
    daily_rows: list[dict],
    quote: dict | None = None,
) -> dict:
    """Compute compact risk metrics from raw daily rows."""
    ordered = sorted(
        (row for row in daily_rows if _number(row.get("close")) is not None),
        key=lambda row: str(row.get("date") or ""),
    )
    closes = [float(_number(row.get("close"))) for row in ordered]
    volumes = [
        float(_number(row.get("volume")) or 0.0)
        for row in ordered
    ]
    if quote:
        live_price = _number(quote.get("price"))
        if live_price and live_price > 0:
            quote_day = str(quote.get("updated_at") or "")[:10]
            latest_day = (
                str(ordered[-1].get("date") or "")[:10] if ordered else ""
            )
            live_volume = _number(quote.get("volume"))
            if latest_day and quote_day and quote_day == latest_day:
                closes[-1] = live_price
                if live_volume and live_volume > 0:
                    volumes[-1] = live_volume
            elif latest_day and quote_day and quote_day < latest_day:
                pass
            else:
                closes.append(live_price)
                if live_volume and live_volume > 0:
                    volumes.append(live_volume)
    returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    recent_returns = returns[-20:]
    volatility_20 = (
        statistics.pstdev(recent_returns) * math.sqrt(252)
        if len(recent_returns) >= 5
        else None
    )
    ma20 = sum(closes[-20:]) / min(20, len(closes)) if closes else None
    ma60 = sum(closes[-60:]) / min(60, len(closes)) if closes else None
    latest_price = closes[-1] if closes else None
    prior_volumes = [value for value in volumes[-21:-1] if value > 0]
    volume_ratio = (
        volumes[-1] / (sum(prior_volumes) / len(prior_volumes))
        if volumes and prior_volumes
        else None
    )
    return {
        "rows": len(daily_rows),
        "latest_history_date": (
            str(ordered[-1].get("date") or "") if ordered else None
        ),
        "latest_price": round(latest_price, 4) if latest_price else None,
        "change_1d": _return_over(closes, 1),
        "return_5d": _return_over(closes, 5),
        "return_20d": _return_over(closes, 20),
        "volatility_20_annualized": (
            round(volatility_20, 6) if volatility_20 is not None else None
        ),
        "drawdown_20d": round(_max_drawdown(closes[-20:]), 6) if closes else None,
        "drawdown_60d": round(_max_drawdown(closes[-60:]), 6) if closes else None,
        "ma20": round(ma20, 4) if ma20 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "distance_ma20": (
            round(latest_price / ma20 - 1.0, 6)
            if latest_price and ma20
            else None
        ),
        "distance_ma60": (
            round(latest_price / ma60 - 1.0, 6)
            if latest_price and ma60
            else None
        ),
        "volume_ratio": round(volume_ratio, 4) if volume_ratio else None,
    }


def validate_market_data(
    daily_rows: list[dict],
    quote: dict,
    cross_source: dict | None = None,
) -> dict:
    """Validate quote, history, freshness and cross-source consistency."""
    issues: list[dict] = []
    price = _number(quote.get("price"))
    open_price = _number(quote.get("open"))
    high = _number(quote.get("high"))
    low = _number(quote.get("low"))
    prev_close = _number(quote.get("prev_close"))
    volume = _number(quote.get("volume"))
    amount = _number(quote.get("amount"))

    if not price or price <= 0:
        issues.append({"severity": "critical", "message": "实时价格缺失或非法"})
    if not volume or volume <= 0:
        issues.append({"severity": "warning", "message": "实时成交量为空或为零"})
    if not amount or amount <= 0:
        issues.append({"severity": "warning", "message": "实时成交额为空或为零"})
    if high is not None and low is not None and high < low:
        issues.append({"severity": "critical", "message": "最高价低于最低价"})
    if price and high and price > high * 1.005:
        issues.append({"severity": "warning", "message": "现价高于最高价，盘口字段可能延迟"})
    if price and low and price < low * 0.995:
        issues.append({"severity": "warning", "message": "现价低于最低价，盘口字段可能延迟"})
    if open_price and prev_close and open_price / prev_close - 1.0 > 0.5:
        issues.append({"severity": "critical", "message": "开盘价相对昨收异常"})

    dates: set[str] = set()
    invalid_history = 0
    duplicate_dates = 0
    for row in daily_rows:
        day = str(row.get("date") or "")
        if day in dates:
            duplicate_dates += 1
        dates.add(day)
        close = _number(row.get("close"))
        row_high = _number(row.get("high"))
        row_low = _number(row.get("low"))
        if not close or close <= 0 or (
            row_high is not None and row_low is not None and row_high < row_low
        ):
            invalid_history += 1
    if not daily_rows:
        issues.append({"severity": "critical", "message": "未获取到历史日线"})
    if duplicate_dates:
        issues.append(
            {
                "severity": "warning",
                "message": f"历史行情存在 {duplicate_dates} 个重复日期",
            }
        )
    if invalid_history:
        issues.append(
            {
                "severity": "warning",
                "message": f"历史行情存在 {invalid_history} 行非法价格",
            }
        )

    latest_date = max(dates) if dates else ""
    if latest_date:
        try:
            latest = datetime.strptime(latest_date, "%Y-%m-%d").replace(
                tzinfo=CHINA_TZ
            )
            age_days = (datetime.now(CHINA_TZ) - latest).days
            if age_days > 7:
                issues.append(
                    {
                        "severity": "warning",
                        "message": f"最后一条日线距今天 {age_days} 天",
                    }
                )
        except ValueError:
            issues.append({"severity": "warning", "message": "历史日期格式异常"})

    cross = cross_source or {}
    price_gap = _number(cross.get("price_gap_pct"))
    if cross.get("status") == "mismatch":
        issues.append(
            {
                "severity": "critical",
                "message": f"交叉核价偏差 {price_gap:.2%}",
            }
        )
    elif cross.get("status") != "verified":
        issues.append(
            {
                "severity": "warning",
                "message": "未能完成双源交叉核价",
            }
        )

    critical = sum(1 for item in issues if item["severity"] == "critical")
    return {
        "status": "pass" if not issues else ("fail" if critical else "warn"),
        "issue_count": len(issues),
        "critical_count": critical,
        "issues": issues,
        "history_rows": len(daily_rows),
        "duplicate_dates": duplicate_dates,
        "invalid_history_rows": invalid_history,
        "latest_history_date": latest_date or None,
        "cross_source": cross,
    }


def _news_signals(items: list[dict]) -> tuple[list[str], list[str]]:
    severe: list[str] = []
    moderate: list[str] = []
    for item in items:
        if item.get("age_hours") is not None and item["age_hours"] > 24 * 30:
            continue
        text = f"{item.get('title') or ''} {item.get('summary') or ''}"
        for term, label in SEVERE_NEWS_TERMS.items():
            if term in text and label not in severe:
                severe.append(label)
        for term, label in MODERATE_NEWS_TERMS.items():
            if term in text and label not in moderate:
                moderate.append(label)
    return severe, moderate


def assess_stock_risk(
    symbol: str,
    name: str,
    quote: dict,
    daily_rows: list[dict],
    cross_source: dict,
    intelligence: dict,
) -> dict:
    """Build one deterministic stock risk assessment."""
    metrics = compute_market_metrics(daily_rows, quote=quote)
    validation = validate_market_data(daily_rows, quote, cross_source)
    news_items = (intelligence.get("news") or []) + (
        intelligence.get("announcements") or []
    )
    severe_news, moderate_news = _news_signals(news_items)
    alerts: list[dict] = []
    score = 0

    def add(severity: str, title: str, detail: str, points: int) -> None:
        nonlocal score
        score += points
        alerts.append(
            {"severity": severity, "title": title, "detail": detail}
        )

    if validation["critical_count"]:
        add(
            "critical",
            "数据核对未通过",
            f"发现 {validation['critical_count']} 个关键数据问题，应先复核再决策。",
            45,
        )
    elif validation["issue_count"]:
        add(
            "medium",
            "数据存在待复核项",
            f"发现 {validation['issue_count']} 个数据质量问题。",
            10,
        )

    volatility = metrics.get("volatility_20_annualized")
    if volatility and volatility >= 0.55:
        add(
            "high",
            "近期波动偏高",
            f"近 20 日年化波动率约 {volatility:.1%}。",
            18,
        )
    elif volatility and volatility >= 0.40:
        add(
            "medium",
            "近期波动抬头",
            f"近 20 日年化波动率约 {volatility:.1%}。",
            9,
        )

    drawdown_20 = metrics.get("drawdown_20d")
    if drawdown_20 is not None and drawdown_20 <= -0.15:
        add(
            "high",
            "短期回撤扩大",
            f"近 20 日最大回撤约 {drawdown_20:.1%}。",
            20,
        )
    elif drawdown_20 is not None and drawdown_20 <= -0.09:
        add(
            "medium",
            "短期回撤需要关注",
            f"近 20 日最大回撤约 {drawdown_20:.1%}。",
            10,
        )

    change_1d = metrics.get("change_1d")
    if change_1d is not None and change_1d <= -0.05:
        add(
            "high",
            "当日跌幅较大",
            f"最新价相对前一交易日下跌 {abs(change_1d):.1%}。",
            16,
        )
    elif change_1d is not None and change_1d <= -0.03:
        add(
            "medium",
            "当日走势偏弱",
            f"最新价相对前一交易日下跌 {abs(change_1d):.1%}。",
            8,
        )

    if (
        metrics.get("distance_ma20") is not None
        and metrics.get("distance_ma60") is not None
        and metrics["distance_ma20"] < 0
        and metrics["distance_ma60"] < 0
    ):
        add(
            "medium",
            "跌破中期均线",
            "最新价同时低于 20 日和 60 日均线。",
            10,
        )

    volume_ratio = metrics.get("volume_ratio")
    if (
        volume_ratio
        and volume_ratio >= 2.0
        and change_1d is not None
        and change_1d < 0
    ):
        add(
            "high",
            "放量下跌",
            f"成交量为近 20 日均量的 {volume_ratio:.2f} 倍。",
            15,
        )

    if severe_news:
        add(
            "high",
            "近期资讯出现重大风险词",
            "、".join(severe_news),
            32,
        )
    elif moderate_news:
        add(
            "medium",
            "近期资讯出现负面信号",
            "、".join(moderate_news),
            8,
        )

    if not alerts:
        alerts.append(
            {
                "severity": "low",
                "title": "未触发量化预警",
                "detail": "行情、波动、回撤和近期资讯未命中当前规则。",
            }
        )

    score = min(100, score)
    if score >= 70:
        level = "critical"
    elif score >= 40:
        level = "high"
    elif score >= 18:
        level = "medium"
    else:
        level = "low"
    severity_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    alerts.sort(
        key=lambda item: severity_rank.get(item["severity"], 0), reverse=True
    )
    price_change = _pct(quote.get("change_pct"))
    return {
        "symbol": symbol,
        "name": name,
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "score": score,
        "price": _number(quote.get("price")),
        "change_pct": price_change,
        "updated_at": quote.get("updated_at"),
        "headline": alerts[0]["title"] if alerts else "未触发预警",
        "metrics": metrics,
        "validation": validation,
        "news_signals": {
            "severe": severe_news,
            "moderate": moderate_news,
        },
        "alerts": alerts,
        "intelligence": intelligence,
    }


def summarize_risk(assessments: list[dict]) -> dict:
    """Aggregate stock-level risk into a concise brief-level summary."""
    if not assessments:
        return {
            "level": "low",
            "level_label": LEVEL_LABELS["low"],
            "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "headline": "暂无分析结果",
        }
    level = max(
        (item["level"] for item in assessments),
        key=lambda value: LEVEL_ORDER[value],
    )
    counts = {
        key: sum(1 for item in assessments if item["level"] == key)
        for key in LEVEL_ORDER
    }
    high_items = [
        item
        for item in assessments
        if item["level"] in {"critical", "high"}
    ]
    if high_items:
        names = "、".join(f"{item['name']}({item['symbol']})" for item in high_items)
        headline = f"{names} 触发高等级风险规则"
    else:
        headline = "当前样本未触发高等级风险规则"
    return {
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "counts": counts,
        "headline": headline,
    }
