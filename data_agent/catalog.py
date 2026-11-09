"""Public data source registry used by both the UI and the agent."""

from __future__ import annotations

from copy import deepcopy


SOURCE_CATALOG = [
    {
        "id": "eastmoney",
        "name": "东方财富公开行情",
        "priority": 1,
        "role": "主数据源",
        "capabilities": ["股票池", "证券搜索", "实时行情", "历史日线"],
        "fields": [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "change_pct",
            "turnover",
        ],
        "adjustment": "前复权日线",
        "amount_quality": "接口直接提供",
        "access": "公开 HTTP 接口，无需账号",
        "constraints": [
            "公开接口可能限流或临时调整字段",
            "行业字段可能返回空值或概念分类",
            "历史日期范围受接口返回长度限制",
        ],
    },
    {
        "id": "tencent",
        "name": "腾讯证券公开行情",
        "priority": 2,
        "role": "行情回退源",
        "capabilities": ["实时行情", "历史日线"],
        "fields": [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "change_pct",
        ],
        "adjustment": "前复权日线",
        "amount_quality": "成交额按成交量 × 100 × 收盘价估算",
        "access": "公开 HTTP 接口，无需账号",
        "constraints": [
            "成交额不是交易所直接披露值",
            "换手率字段可能缺失",
            "字段位置依赖公开接口格式",
        ],
    },
    {
        "id": "sina",
        "name": "新浪财经公开行情",
        "priority": 3,
        "role": "股票池与历史行情回退源",
        "capabilities": ["股票池", "历史日线"],
        "fields": ["date", "open", "high", "low", "close", "volume", "amount"],
        "adjustment": "接口日线，复权口径需在数据清单中复核",
        "amount_quality": "成交额按成交量 × 收盘价估算",
        "access": "公开 HTTP 接口，无需账号",
        "constraints": [
            "不提供当前盘口维度的完整元数据",
            "成交额和换手率字段需要复核",
            "历史长度受接口参数限制",
        ],
    },
]


def get_source_catalog() -> list[dict]:
    """Return a defensive copy so callers cannot mutate the registry."""
    return deepcopy(SOURCE_CATALOG)


def get_source(source_id: str) -> dict:
    for source in SOURCE_CATALOG:
        if source["id"] == source_id:
            return deepcopy(source)
    raise ValueError(f"未知数据源: {source_id}")
