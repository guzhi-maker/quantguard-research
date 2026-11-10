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


def _fmt_number(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:,.{digits}f}{suffix}"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:+.{digits}%}"


def build_indicator_details(
    metrics: dict,
    validation: dict,
    intelligence: dict,
    quote: dict | None = None,
) -> list[dict]:
    """Explain every reported indicator: value, formula, meaning, usage and source."""
    quote = quote or {}
    cross = validation.get("cross_source") or {}
    news_items = (intelligence.get("news") or []) + (
        intelligence.get("announcements") or []
    )
    severe, moderate = _news_signals(news_items)
    price_source = str(quote.get("source") or "公开实时行情")
    history_source = "公开前复权日线（东方财富优先，腾讯/新浪回退）"

    def item(
        key: str,
        label: str,
        value: Any,
        display: str,
        formula: str,
        meaning: str,
        usage: str,
        source: str,
        window: str,
    ) -> dict:
        return {
            "key": key,
            "label": label,
            "value": value,
            "display": display,
            "formula": formula,
            "meaning": meaning,
            "usage": usage,
            "source": source,
            "window": window,
        }

    details = [
        item(
            "latest_price",
            "最新价",
            metrics.get("latest_price"),
            _fmt_number(metrics.get("latest_price"), 2, " 元"),
            "实时行情最新成交价；实时行情缺失时回退到最后一条日线收盘价",
            "市场当前对该股票形成的边际价格，是计算收益、均线与回撤的基准",
            "作为前视收益标签的定价基准；建模时要与财报因子保持同一复权与时点口径",
            price_source,
            "最新交易日",
        ),
        item(
            "change_1d",
            "1 日收益",
            metrics.get("change_1d"),
            _fmt_pct(metrics.get("change_1d")),
            "最新价 / 前一交易日收盘价 - 1",
            "单日价格变动的方向和幅度，反映短期冲击与情绪",
            "可作为日频收益标签或短周期反转/动量因子的输入；需按交易日对齐",
            history_source,
            "1 个交易日",
        ),
        item(
            "return_5d",
            "5 日收益",
            metrics.get("return_5d"),
            _fmt_pct(metrics.get("return_5d")),
            "最新价 / 5 个交易日前收盘价 - 1",
            "约一周的价格动量，平滑了单日噪声",
            "适合做周频动量因子；建模前做横截面去极值与标准化",
            history_source,
            "5 个交易日",
        ),
        item(
            "return_20d",
            "20 日收益",
            metrics.get("return_20d"),
            _fmt_pct(metrics.get("return_20d")),
            "最新价 / 20 个交易日前收盘价 - 1",
            "约一个月的趋势表现，是常用的中期动量特征",
            "可作为月度动量因子，与未来 5/20 日收益做因子检验和分层回测",
            history_source,
            "20 个交易日",
        ),
        item(
            "volatility_20_annualized",
            "20 日年化波动率",
            metrics.get("volatility_20_annualized"),
            _fmt_pct(metrics.get("volatility_20_annualized")),
            "近 20 个日收益率的标准差 × √252",
            "近期收益的不确定性与风险水平，数值越高价格越不稳定",
            "用于组合风险预算、波动率目标和仓位约束；也可作为风险因子做分层检验",
            history_source,
            "20 个交易日",
        ),
        item(
            "drawdown_20d",
            "20 日最大回撤",
            metrics.get("drawdown_20d"),
            _fmt_pct(metrics.get("drawdown_20d")),
            "近 20 个交易日内，任一峰值之后的最差区间跌幅",
            "衡量短期下行风险与持有体验，负数越大表示回撤越深",
            "可作为回撤惩罚项或风险过滤条件；与收益标签分开评估，避免重复计算风险",
            history_source,
            "20 个交易日",
        ),
        item(
            "drawdown_60d",
            "60 日最大回撤",
            metrics.get("drawdown_60d"),
            _fmt_pct(metrics.get("drawdown_60d")),
            "近 60 个交易日内，任一峰值之后的最差区间跌幅",
            "衡量中期回撤深度，可区分短期噪声与持续下行",
            "可用于构造中期风险状态变量，作为组合再平衡的辅助条件",
            history_source,
            "60 个交易日",
        ),
        item(
            "ma20",
            "20 日均线",
            metrics.get("ma20"),
            _fmt_number(metrics.get("ma20"), 2, " 元"),
            "最近 20 个交易日收盘价的算术平均",
            "短期价格中枢，用于平滑日常波动",
            "可作为趋势基准；建模时通常使用价格相对均线的偏离而不是均线绝对值",
            history_source,
            "20 个交易日",
        ),
        item(
            "ma60",
            "60 日均线",
            metrics.get("ma60"),
            _fmt_number(metrics.get("ma60"), 2, " 元"),
            "最近 60 个交易日收盘价的算术平均",
            "中期价格中枢，用于识别更持续的趋势变化",
            "与 20 日均线交叉可构造趋势状态特征；需做时间序列切分避免未来信息泄漏",
            history_source,
            "60 个交易日",
        ),
        item(
            "distance_ma20",
            "偏离 20 日均线",
            metrics.get("distance_ma20"),
            _fmt_pct(metrics.get("distance_ma20")),
            "最新价 / 20 日均线 - 1",
            "当前价格相对短期中枢的偏离程度，正值表示站上均线",
            "可作为短期趋势/超买超卖特征；建议按行业做横截面标准化",
            history_source,
            "20 个交易日",
        ),
        item(
            "distance_ma60",
            "偏离 60 日均线",
            metrics.get("distance_ma60"),
            _fmt_pct(metrics.get("distance_ma60")),
            "最新价 / 60 日均线 - 1",
            "当前价格相对中期中枢的偏离程度，正值表示中期趋势偏强",
            "可作为中期趋势特征；与波动率、成交量组合做交互项检验",
            history_source,
            "60 个交易日",
        ),
        item(
            "volume_ratio",
            "成交量比",
            metrics.get("volume_ratio"),
            _fmt_number(metrics.get("volume_ratio"), 2, " 倍"),
            "最新成交量 / 前 20 个交易日平均成交量",
            "衡量交易活跃度是否异常，数值越高说明当前换手相对放大",
            "可作为流动性/异动特征，与价格方向组合成放量上涨或放量下跌规则",
            history_source,
            "1 日 vs 20 日均量",
        ),
        item(
            "price_gap_pct",
            "双源核价偏差",
            cross.get("price_gap_pct"),
            _fmt_pct(cross.get("price_gap_pct")),
            "|东方财富价 - 腾讯价| / 两源均价；双方涨跌停或字段缺失时不比较",
            "衡量两个独立行情源的报价一致性，是数据可信度的直接证据",
            "作为数据质量门控：偏差超阈值先复核再进入特征工程，不直接作为 alpha 因子",
            "东方财富日行情 + 腾讯证券实时行情",
            "同一时点",
        ),
        item(
            "news_signal",
            "资讯信号",
            len(severe) + len(moderate),
            f"重大 {len(severe)} 项 / 关注 {len(moderate)} 项",
            "对近 30 天新闻与公告标题做词典匹配，统计严重与中等风险词命中数",
            "反映文本信息中的事件线索数量；命中不等于事实成立，也不能单独推导收益",
            "可作为事件/情绪因子的候选特征；建模型前需人工抽样核验并做时间衰减",
            "东方财富新闻与公告公开检索",
            "近 30 天",
        ),
    ]
    return details


def build_research_steps(
    validation: dict,
    metrics: dict,
    intelligence: dict,
    cross_source: dict,
) -> list[dict]:
    """Describe the reproducible pipeline that produced this stock report."""
    cross_status = str(cross_source.get("status") or "unavailable")
    cross_label = {
        "verified": "双源核价一致",
        "mismatch": "双源价格偏差超阈值",
        "unavailable": "双源核价不可用",
    }.get(cross_status, cross_status)
    news_count = len(intelligence.get("news") or [])
    announcement_count = len(intelligence.get("announcements") or [])
    validation_status = str(validation.get("status") or "warn")
    return [
        {
            "step": 1,
            "actor": "采集器",
            "action": "重新抓取个股行情",
            "input": "股票代码、历史窗口",
            "output": f"{metrics.get('rows') or 0} 行日线，最新日期 {metrics.get('latest_history_date') or '—'}",
            "status": "done",
        },
        {
            "step": 2,
            "actor": "核价机器人",
            "action": "东方财富与腾讯双源交叉核价",
            "input": "同一时点两源最新价",
            "output": cross_label,
            "status": "done" if cross_status == "verified" else "warn",
        },
        {
            "step": 3,
            "actor": "Python 指标引擎",
            "action": "按固定公式计算收益、波动、回撤、均线与量比",
            "input": "复权日线收盘价与成交量",
            "output": "14 项可复现指标（数值由代码计算，DeepSeek 不参与生成）",
            "status": "done",
        },
        {
            "step": 4,
            "actor": "规则引擎",
            "action": "按波动、回撤、均线、量价和数据质量规则打分",
            "input": "指标数值 + 核价结果",
            "output": f"数据核对 {validation_status}，发现 {validation.get('issue_count', 0)} 个数据问题",
            "status": "done" if validation_status == "pass" else "warn",
        },
        {
            "step": 5,
            "actor": "资讯机器人",
            "action": "检索实时新闻与公司公告并做风险词匹配",
            "input": "股票名称、代码、近 30 天公开资讯",
            "output": f"{news_count} 条新闻、{announcement_count} 条公告",
            "status": "done" if (news_count or announcement_count) else "warn",
        },
        {
            "step": 6,
            "actor": "DeepSeek",
            "action": "读取结构化证据并撰写研究简报",
            "input": "指标、核价、规则预警与新闻标题",
            "output": "中文研究解读；不得编造数值，不得把标题外推为确定结论",
            "status": "done",
        },
    ]


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
        "indicator_details": build_indicator_details(
            metrics, validation, intelligence, quote
        ),
        "research_steps": build_research_steps(
            validation, metrics, intelligence, cross_source
        ),
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
