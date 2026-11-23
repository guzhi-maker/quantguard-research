"""证据包组装：把分析结论压缩成 AI 报告的唯一输入。

六块结构：元信息、用户上下文、组合结论、个股票、诊断结论、建议包、边界声明。
设计口径见 docs/quant-research-v2-workflow.md 第 5 节。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

PROMPT_VERSION = "qg-report-v2.1"
TOKEN_LIMIT = 5000

DISCLAIMER = "仅用于课程研究，不构成投资建议或收益承诺。"

LIMITATIONS = [
    "股票池是抓取时点的流动性快照，不是历史成分股，存在幸存者偏差。",
    "新闻标题只作为待核验线索，不能外推为确定结论。",
    "风险收益曲线右端的预期收益估计误差最大，不应视为可实现收益。",
    "行业分类为当前时点（证监会分类），不是历史时点分类，回看存在轻微前视偏差。",
    "全部收益指标为扣费后口径，费用模型为估算值。",
    DISCLAIMER,
]


def _round(value, digits: int = 4):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return round(number, digits)


def data_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _stock_block(
    holdings: pd.Series,
    predictions: dict[str, dict],
    meta_prob: pd.Series,
    multipliers: pd.Series,
    tradable: pd.Series,
    names: pd.Series,
    attributions: dict[str, list],
    limit: int = 25,
) -> list[dict]:
    """持仓在前、候选在前五：控制证据包规模。"""
    ordered: list[str] = []
    if len(holdings):
        ordered.extend(str(symbol) for symbol in list(holdings.index)[:20])
    ranked = sorted(
        predictions.items(), key=lambda item: (item[1] or {}).get("P50") or -9.0, reverse=True
    )[:5]
    for symbol, _ in ranked:
        if symbol not in ordered:
            ordered.append(str(symbol))

    rows = []
    for symbol in ordered[:limit]:
        values = predictions.get(symbol, {}) or {}
        rows.append(
            {
                "symbol": symbol,
                "name": str(names.get(symbol, symbol)),
                "weight": _round(holdings.get(symbol, 0.0)),
                "P10": _round(values.get("P10")),
                "P50": _round(values.get("P50")),
                "P90": _round(values.get("P90")),
                "meta_prob": _round(meta_prob.get(symbol)),
                "multiplier": _round(multipliers.get(symbol)),
                "tradable": bool(tradable.get(symbol, True)),
                "attributions": attributions.get(symbol, [])[:3],
            }
        )
    return rows


FIELD_GUIDE = {
    "portfolio.ladder[]": "五档组合，每档一行；数值均为小数（0.09 = 9%）",
    "ladder[].cvar95": "最差 5% 月份的平均亏损比例，越大越危险",
    "ladder[].expected_return": "扣费后的预期收益（与期限同口径）",
    "ladder[].volatility": "年化波动率",
    "ladder[].positions": "该档建议持有多少只股票",
    "ladder[].cash_weight": "现金占比，等于 1 - 股票总仓位",
    "ladder[].cost_estimate": "本次调仓的成本占资产比例",
    "stocks[].weight": "该股票在组合中的目标权重",
    "stocks[].P10/P50/P90": "未来收益的分位数预测；P10 是偏悲观情形",
    "stocks[].meta_prob": "元模型判断本次预测可靠的概率；0.5 表示元模型未启用",
    "stocks[].multiplier": "元模型给出的仓位乘数；1.0 表示不加权",
    "stocks[].attributions": "[[特征名, 标准化值 z], ...]；z 是特征相对截面均值的强弱，"
    "z>0 表示该特征值高于均值，z<0 表示低于均值；z 的正负与方向不代表收益方向",
    "user.has_holdings": "是否提供了有效持仓（权重合计 > 0）",
    "user.cvar_budget": "用户风险档位对应的 CVaR 预算",
    "advice.folded": "因预算被折叠的优化建议条数",
    "diagnosis.red/yellow": "红灯/黄灯诊断项数量",
}


def build_evidence_pack(
    *,
    run_id: str,
    as_of: str,
    preference,
    portfolios,
    holdings,
    predictions,
    meta_probability,
    multipliers,
    tradable,
    names,
    attributions,
    diagnosis,
    advice,
    model_info: dict,
    data_quality: dict,
    budget_info: dict,
    has_holdings: bool | None = None,
) -> dict:
    """组装证据包。所有数值都来自上游计算结果，本函数不做估计。"""
    ladder = []
    for portfolio in portfolios:
        summary = portfolio.summary()
        ladder.append(
            {
                "label": summary["label"],
                "cvar95": summary["cvar95"],
                "expected_return": summary["expected_return"],
                "volatility": summary["volatility"],
                "turnover": summary["turnover"],
                "positions": summary["positions"],
                "cash_weight": summary["cash_weight"],
                "cost_estimate": summary["cost_estimate"],
            }
        )

    pack = {
        "meta": {
            "run_id": run_id,
            "as_of": as_of,
            "model_version": model_info.get("version", "v2"),
            "prompt_version": PROMPT_VERSION,
            "data_hash": data_quality.get("data_hash"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "user": {
            "risk_level": _round(preference.risk_level, 1),
            "risk_tier": preference.risk["label"],
            "cvar_budget": _round(preference.risk["cvar95"]),
            "horizon_days": preference.horizon["days"],
            "horizon_label": preference.horizon["label"],
            "has_holdings": (
                bool(has_holdings)
                if has_holdings is not None
                else bool(len(holdings)) and float(holdings.sum()) > 0
            ),
            "capital_band": data_quality.get("capital_band", "未提供"),
        },
        "portfolio": {
            "ladder": ladder,
            "selected_label": preference.risk["label"],
            "reject_rate": _round(data_quality.get("reject_rate")),
            "coverage": data_quality.get("coverage", {}),
        },
        "stocks": _stock_block(
            holdings, predictions, meta_probability, multipliers, tradable, names, attributions
        ),
        "diagnosis": {
            "headline": diagnosis.get("headline"),
            "red": diagnosis.get("red"),
            "yellow": diagnosis.get("yellow"),
            "items": [
                {
                    "label": item["label"],
                    "status": item["status_label"],
                    "evidence": item["evidence"],
                }
                for item in diagnosis.get("items", [])
            ],
        },
        "advice": {
            "budget": advice.get("budget"),
            "output": advice.get("optimization_count"),
            "folded": advice.get("folded_count"),
            "actions": [
                {
                    key: action[key]
                    for key in (
                        "symbol",
                        "name",
                        "action",
                        "delta",
                        "kind",
                        "urgency",
                        "cost",
                        "improvement",
                    )
                }
                for action in advice.get("actions", [])
            ],
            "post_feasible": advice.get("post_check", {}).get("feasible"),
        },
        "limits": LIMITATIONS,
        "field_guide": FIELD_GUIDE,
    }
    return pack


def estimate_tokens(pack: dict) -> int:
    """粗估 token：中文按字符计，数字与标点按 1/2 计。"""
    text = json.dumps(pack, ensure_ascii=False)
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = len(text) - chinese
    return int(chinese + other / 2.5)


def assert_size(pack: dict, limit: int = TOKEN_LIMIT) -> dict:
    tokens = estimate_tokens(pack)
    if tokens > limit:
        raise ValueError(
            f"证据包过大：约 {tokens} token，超过上限 {limit}；请减少个股条数或归因条数"
        )
    return {"tokens": tokens, "limit": limit}


def pack_digest(pack: dict) -> str:
    return hashlib.sha256(
        json.dumps(pack, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
