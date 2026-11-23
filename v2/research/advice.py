"""建议引擎：三类触发、三道闸、建议预算与交易包自洽。

设计口径见 docs/quant-research-v2-workflow.md 阶段 7：
- 三类归因：再平衡型 / 信号型 / 风险型
- 三道闸：带宽、成本（改善 > 成本 × 2）、确认（与上期方向不一致时降级）
- 建议预算：优化型每周 5 条（持仓 <=10 时 2 条），安全类不占预算
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from config.settings import CostConfig

BAND_TOLERANCE = 0.15  # 带宽内的相对容差


@dataclass
class Advice:
    symbol: str
    name: str
    action: str
    current_weight: float
    target_weight: float
    delta: float
    lots: int = 0
    amount: float = 0.0
    kind: str = "rebalance"
    urgency: str = "normal"
    reason: str = ""
    improvement: float = 0.0
    cost: float = 0.0
    safety: bool = False

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "action": self.action,
            "current_weight": round(self.current_weight, 4),
            "target_weight": round(self.target_weight, 4),
            "delta": round(self.delta, 4),
            "lots": int(self.lots),
            "amount": round(self.amount, 2),
            "kind": self.kind,
            "urgency": self.urgency,
            "reason": self.reason,
            "improvement": round(self.improvement, 6),
            "cost": round(self.cost, 6),
            "safety": bool(self.safety),
        }


def _band(preference) -> float:
    return float(preference.horizon["band_pp"]) / 100.0


def _label(delta: float, band: float, current: float, target: float) -> str:
    if target <= 1e-9 and current > 1e-9:
        return "清仓"
    if current <= 1e-9 and target > 1e-9:
        return "买入"
    if delta > band:
        return "加仓" if delta < 2 * band else "大幅加仓"
    if delta < -band:
        return "小幅减仓" if delta > -2 * band else "减仓"
    return "持有"


def build_advice(
    holdings: pd.Series,
    target: pd.Series,
    expected: pd.Series,
    preference,
    tradable: pd.Series | None = None,
    prices: pd.Series | None = None,
    names: pd.Series | None = None,
    industries: pd.Series | None = None,
    last_actions: dict[str, str] | None = None,
    capital: float = 1_000_000.0,
    costs: CostConfig | None = None,
    budget: int | None = None,
) -> dict:
    """生成成组建议：安全类全量 + 优化型按预算裁剪。"""
    costs = costs or CostConfig()
    current = holdings.reindex(holdings.index.intersection(holdings.index)).fillna(0.0).astype(float)
    target = target.fillna(0.0).astype(float)
    universe = current.index.union(target.index)
    current = current.reindex(universe).fillna(0.0)
    target = target.reindex(universe).fillna(0.0)
    expected = expected.reindex(universe).fillna(0.0)

    band = _band(preference)
    single_cap = float(preference.risk["single_cap"])
    industry_cap = float(preference.risk["industry_cap"])
    last = last_actions or {}
    round_trip = costs.round_trip_rate()

    candidates: list[Advice] = []
    for symbol in universe:
        held, desired = float(current[symbol]), float(target[symbol])
        delta = desired - held
        if abs(delta) < 1e-12:
            continue
        flag = bool(tradable.get(symbol, True)) if tradable is not None else True
        name = str(names.get(symbol, symbol)) if names is not None else str(symbol)
        price = float(prices.get(symbol, 0.0)) if prices is not None else 0.0
        improvement = delta * float(expected[symbol])
        cost = abs(delta) * round_trip

        # 安全类一：可持有性（停牌/ST/流动性）
        if not flag:
            kind = "safety_holdability" if held > 0 else "risk"
            candidates.append(
                Advice(
                    symbol=symbol,
                    name=name,
                    action="等待" if held <= 0 else "减仓",
                    current_weight=held,
                    target_weight=desired,
                    delta=delta,
                    kind=kind,
                    urgency="high",
                    reason="当日不可交易（停牌/涨停/流动性不足），建议等开板或次日处理",
                    improvement=improvement,
                    cost=cost,
                    safety=held > 0,
                )
            )
            continue

        # 安全类二：合规超限
        if held > single_cap * 1.2 and delta < 0:
            candidates.append(
                Advice(
                    symbol=symbol,
                    name=name,
                    action="减仓",
                    current_weight=held,
                    target_weight=desired,
                    delta=delta,
                    kind="safety_compliance",
                    urgency="high",
                    reason=f"当前权重 {held:.1%} 超过单票上限 {single_cap:.0%}",
                    improvement=improvement,
                    cost=cost,
                    safety=True,
                )
            )
            continue

        # 信号恶化：预期为负且仍持有
        if held > 0 and float(expected[symbol]) < 0 and desired < held:
            candidates.append(
                Advice(
                    symbol=symbol,
                    name=name,
                    action="减仓",
                    current_weight=held,
                    target_weight=desired,
                    delta=delta,
                    kind="risk",
                    urgency="high",
                    reason="预期收益转负，风险预算收缩",
                    improvement=improvement,
                    cost=cost,
                    safety=True,
                )
            )
            continue

        # 带宽闸
        kind = "rebalance"
        if held > 0 and desired > 0 and abs(delta) < band * (1 - BAND_TOLERANCE):
            kind = "signal" if abs(delta) > band * 0.5 else "rebalance"
        # 成本闸
        if improvement <= cost * 2.0:
            continue
        # 确认闸：与上期方向相反且幅度不大则降级
        previous_action = last.get(symbol)
        direction = 1 if delta > 0 else -1
        previous_direction = 0
        if previous_action in {"加仓", "大幅加仓", "买入"}:
            previous_direction = 1
        elif previous_action in {"减仓", "小幅减仓", "清仓"}:
            previous_direction = -1
        if previous_direction and previous_direction != direction and abs(delta) < band * 1.5:
            kind = "observe"
            continue

        action = _label(delta, band, held, desired)
        lots = 0
        amount = 0.0
        if price > 0 and capital > 0:
            lots = int(math.floor(abs(delta) * capital / (price * 100) + 1e-9))
            amount = round(lots * 100 * price, 2)
        candidates.append(
            Advice(
                symbol=symbol,
                name=name,
                action=action,
                current_weight=held,
                target_weight=desired,
                delta=delta,
                lots=lots,
                amount=amount,
                kind=kind,
                urgency="normal",
                reason=(
                    f"目标权重 {desired:.1%} 与当前 {held:.1%} 偏离 {delta:+.1%}"
                    if kind == "rebalance"
                    else f"预期收益 {float(expected[symbol]):.2%}，信号变化触发调整"
                ),
                improvement=improvement,
                cost=cost,
            )
        )

    safety = [item for item in candidates if item.safety]
    optimization = [item for item in candidates if not item.safety]
    optimization.sort(key=lambda item: (item.improvement / item.cost) if item.cost > 0 else 0.0, reverse=True)

    if budget is None:
        budget = 2 if int((current > 0).sum()) <= 10 else 5
    kept = safety + optimization[:budget]
    folded = optimization[budget:]
    kept.sort(key=lambda item: (0 if item.safety else 1, -item.improvement))

    return {
        "actions": [item.as_dict() for item in kept],
        "folded": [item.as_dict() for item in folded],
        "budget": budget,
        "safety_count": len(safety),
        "optimization_count": len(kept) - len(safety),
        "folded_count": len(folded),
        "post_check": _post_check(kept, current, target, industries, single_cap, industry_cap),
        "summary": {
            "buy": sum(1 for item in kept if item.action in {"买入", "加仓", "大幅加仓"}),
            "sell": sum(1 for item in kept if item.action in {"减仓", "小幅减仓", "清仓"}),
            "hold": sum(1 for item in kept if item.action in {"持有", "等待"}),
            "estimated_cost": round(sum(item.cost for item in kept), 6),
            "estimated_improvement": round(sum(item.improvement for item in kept), 6),
        },
    }


def _post_check(
    actions: list[Advice],
    current: pd.Series,
    target: pd.Series,
    industries: pd.Series | None,
    single_cap: float,
    industry_cap: float,
) -> dict:
    """执行交易包后的约束检查。"""
    post = current.copy()
    for item in actions:
        if item.action in {"持有", "等待", "观察"}:
            continue
        post[item.symbol] = max(0.0, min(item.target_weight, post.get(item.symbol, 0.0) + item.delta))
    over_single = [symbol for symbol, weight in post.items() if weight > single_cap + 1e-9]
    over_industry = []
    if industries is not None:
        groups = industries.reindex(post.index).fillna("未分类")
        exposure = post.groupby(groups).sum()
        over_industry = [group for group, weight in exposure.items() if weight > industry_cap + 1e-9]
    return {
        "feasible": not over_single and not over_industry,
        "over_single": over_single,
        "over_industry": over_industry,
        "gross_exposure": round(float(post.sum()), 4),
    }


def advice_table(result: dict) -> pd.DataFrame:
    rows = result.get("actions", [])
    if not rows:
        return pd.DataFrame(columns=["symbol", "action", "delta"])
    return pd.DataFrame(rows)
