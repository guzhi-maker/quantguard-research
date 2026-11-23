"""持仓诊断：六个维度，每项给出状态灯、证据与建议。

设计口径见 docs/quant-research-v2-workflow.md 阶段 6。
不产出单一总分，只给红灯数量与逐项明细。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

GREEN, YELLOW, RED = "green", "yellow", "red"
STATUS_LABELS = {GREEN: "正常", YELLOW: "关注", RED: "需处理"}


def _item(key: str, label: str, status: str, evidence: str, action: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "evidence": evidence,
        "action": action,
    }


def _industry_exposure(weights: pd.Series, industries: pd.Series) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    groups = industries.reindex(weights.index).fillna("未分类")
    return weights.groupby(groups).sum()


def diagnose(
    holdings: pd.Series,
    preference,
    target: pd.Series | None = None,
    scenarios: pd.DataFrame | None = None,
    industries: pd.Series | None = None,
    tradable: pd.Series | None = None,
    names: pd.Series | None = None,
    expected: pd.Series | None = None,
    cost_rate: float = 0.002,
) -> dict:
    """返回六维诊断结果。holdings 为当前权重（0-1）。"""
    weights = holdings[holdings > 0].astype(float).sort_values(ascending=False)
    total = float(weights.sum())
    items: list[dict] = []

    # 1. 一致性：实际波动 vs 声明档位
    if scenarios is not None and not scenarios.empty and not weights.empty:
        matrix = scenarios.reindex(columns=weights.index).fillna(0.0)
        vector = matrix.to_numpy(dtype=float) @ weights.to_numpy(dtype=float)
        realized_vol = float(np.std(vector, ddof=1) * math.sqrt(12.0)) if len(vector) > 2 else 0.0
    else:
        realized_vol = 0.0
    declared_vol = float(preference.risk["vol"])
    ratio = realized_vol / declared_vol if declared_vol > 0 else 1.0
    if ratio >= 1.5:
        status = RED
        action = "按目标组合减仓高波动标的，或把风险偏好上调到与实际一致"
    elif ratio >= 1.15:
        status = YELLOW
        action = "小幅降低高波动持仓"
    else:
        status = GREEN
        action = "维持"
    items.append(
        _item(
            "consistency",
            "偏好一致性",
            status,
            f"实际波动 {realized_vol:.1%}，声明档位目标 {declared_vol:.1%}，比值 {ratio:.2f}",
            action,
        )
    )

    # 2. 合规性
    single_cap = float(preference.risk["single_cap"])
    industry_cap = float(preference.risk["industry_cap"])
    positions_cap = int(preference.risk["positions"])
    cash_floor = float(preference.risk["cash_floor"])
    over_single = weights[weights > single_cap + 1e-9]
    industry = _industry_exposure(weights, industries) if industries is not None else pd.Series(dtype=float)
    over_industry = industry[industry > industry_cap + 1e-9] if not industry.empty else pd.Series(dtype=float)
    cash = max(0.0, 1.0 - total)
    issues = []
    if not over_single.empty:
        issues.append(f"{len(over_single)} 只超单票上限 {single_cap:.0%}")
    if not over_industry.empty:
        issues.append(f"{len(over_industry)} 个行业超上限 {industry_cap:.0%}")
    if len(weights) > positions_cap:
        issues.append(f"持仓 {len(weights)} 只超过档位上限 {positions_cap}")
    if cash < cash_floor - 1e-9:
        issues.append(f"现金 {cash:.1%} 低于底线 {cash_floor:.0%}")
    status = RED if over_single.size and (over_single > single_cap * 1.5).any() else (
        YELLOW if issues else GREEN
    )
    items.append(
        _item(
            "compliance",
            "合规性",
            status,
            "；".join(issues) if issues else "未发现超限",
            "优先处理超限项，减仓顺序按成本最低者优先" if issues else "维持",
        )
    )

    # 3. 可持有性
    if tradable is not None and not weights.empty:
        flag = tradable.reindex(weights.index)
        blocked = flag[flag.fillna(False) == False]  # noqa: E712 - 明确按 False 过滤
    else:
        blocked = pd.Series(dtype=float)
    status = RED if len(blocked) else GREEN
    names_text = ""
    if len(blocked) and names is not None:
        names_text = "、".join(str(names.get(symbol, symbol)) for symbol in blocked.index[:5])
    items.append(
        _item(
            "holdability",
            "可持有性",
            status,
            f"{len(blocked)} 只不可交易或流动性不足" + (f"：{names_text}" if names_text else ""),
            "分批退出不可持有标的，不给补仓建议" if len(blocked) else "维持",
        )
    )

    # 4. 风险集中
    top3 = float(weights.iloc[:3].sum()) if len(weights) >= 3 else float(weights.sum())
    hhi = float((weights.pow(2)).sum())
    correlation = None
    if scenarios is not None and scenarios.shape[1] > 1:
        data = scenarios[[column for column in weights.index if column in scenarios.columns]]
        if data.shape[1] > 1:
            corr = data.corr().to_numpy()
            upper = corr[np.triu_indices_from(corr, k=1)]
            upper = upper[np.isfinite(upper)]
            correlation = float(upper.mean()) if upper.size else None
    status = GREEN
    evidence = f"Top3 权重 {top3:.1%}，HHI {hhi:.3f}"
    if correlation is not None:
        evidence += f"，平均相关性 {correlation:.2f}"
    if top3 >= 0.65 or (correlation is not None and correlation >= 0.7):
        status = RED
    elif top3 >= 0.45 or (correlation is not None and correlation >= 0.55):
        status = YELLOW
    items.append(
        _item(
            "concentration",
            "风险集中",
            status,
            evidence,
            "降低同质持仓，补充低相关标的" if status != GREEN else "维持",
        )
    )

    # 5. 机会成本：换到目标组合的预期改善 vs 交易成本
    gap = None
    improvement = None
    action = "未提供目标组合"
    if target is not None and not target.empty:
        overlap = target.index.union(weights.index)
        delta = target.reindex(overlap).fillna(0.0) - weights.reindex(overlap).fillna(0.0)
        gap = float(delta.abs().sum())
        if expected is not None and not expected.empty:
            improvement = float((delta * expected.reindex(overlap).fillna(0.0)).sum())
        cost = gap * cost_rate
        if improvement is None:
            action = "按目标组合调整" if gap > 0.1 else "已接近目标"
        elif improvement > cost * 2.0:
            action = f"换仓划算：预期改善 {improvement:.2%}，成本约 {cost:.2%}"
        else:
            action = f"换仓不划算：预期改善 {improvement:.2%}，成本约 {cost:.2%}"
    status = GREEN if gap is None or gap <= 0.1 else (
        YELLOW if improvement is None or improvement > gap * cost_rate * 2.0 else GREEN
    )
    items.append(
        _item(
            "opportunity",
            "机会成本",
            status,
            "未提供目标组合" if gap is None else f"与目标组合权重差异合计 {gap:.1%}",
            action,
        )
    )

    # 6. 交易行为：碎片仓位
    fragments = weights[weights < 0.005]
    status = YELLOW if len(fragments) >= 3 else GREEN
    items.append(
        _item(
            "trading",
            "交易行为",
            status,
            f"{len(fragments)} 只碎片仓位（权重 <0.5%）",
            "清理碎片仓位，降低管理成本" if len(fragments) else "维持",
        )
    )

    reds = sum(1 for item in items if item["status"] == RED)
    yellows = sum(1 for item in items if item["status"] == YELLOW)
    return {
        "items": items,
        "red": reds,
        "yellow": yellows,
        "headline": (
            f"{reds} 项红灯、{yellows} 项关注"
            if reds or yellows
            else "六项检查全部正常"
        ),
        "metrics": {
            "positions": int(len(weights)),
            "gross_exposure": round(total, 4),
            "cash_weight": round(cash, 4),
            "realized_vol": round(realized_vol, 4),
            "declared_vol": round(declared_vol, 4),
            "top3_weight": round(top3, 4),
            "hhi": round(hhi, 4),
            "average_correlation": round(correlation, 4) if correlation is not None else None,
        },
    }


def diagnosis_summary(report: dict) -> str:
    lines = [report.get("headline", "")]
    for item in report.get("items", []):
        if item["status"] != GREEN:
            lines.append(f"[{item['status_label']}] {item['label']}：{item['evidence']}")
    return "\n".join(line for line in lines if line)
