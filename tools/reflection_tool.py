"""策略反思与迭代工具：诊断、参数变体与目标评分。

规则化反思不依赖 LLM，所有结论都有绩效指标支撑；LLM 接入后可作为
反思 Agent 的数据底座。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from tools import factor_tool


@dataclass
class Diagnostic:
    dimension: str
    severity: str
    issue: str
    evidence: dict
    suggestion: str


def diagnose_strategy(
    metrics: dict,
    risk_report=None,
    params: Optional[dict] = None,
) -> list[Diagnostic]:
    """从收益、风险、成本、稳定性四个维度给出数据支撑的诊断。"""
    diags: list[Diagnostic] = []
    params = params or {}
    drawdown = abs(float(metrics.get("max_drawdown") or 0.0))
    turnover = float(metrics.get("annual_turnover") or 0.0)
    annual_return = metrics.get("annual_return")
    bench_return = metrics.get("benchmark_annual_return")
    sharpe = metrics.get("sharpe_ratio")
    calmar = metrics.get("calmar_ratio")

    if turnover > 2.0:
        diags.append(
            Diagnostic(
                dimension="成本",
                severity="critical",
                issue="年化换手率超过风控上限",
                evidence={"annual_turnover": turnover, "limit": 2.0},
                suggestion="延长调仓周期、增加持仓数量，或引入换股阈值降低无效换手。",
            )
        )
    elif turnover > 1.0:
        diags.append(
            Diagnostic(
                dimension="成本",
                severity="warning",
                issue="换手率偏高，交易成本侵蚀收益",
                evidence={"annual_turnover": turnover},
                suggestion="优先测试周频/月频与更大持仓组合的换手差异。",
            )
        )

    if drawdown >= 0.30:
        diags.append(
            Diagnostic(
                dimension="风险",
                severity="critical",
                issue="最大回撤触发熔断线",
                evidence={"max_drawdown": -drawdown, "circuit_line": 0.30},
                suggestion="降低单票与行业集中度，加入止损或波动率目标仓位控制。",
            )
        )
    elif drawdown >= 0.20:
        diags.append(
            Diagnostic(
                dimension="风险",
                severity="warning",
                issue="最大回撤触发预警线",
                evidence={"max_drawdown": -drawdown, "warn_line": 0.20},
                suggestion="检查最大回撤发生时段，并补充行业/风格暴露分析。",
            )
        )

    if annual_return is not None and bench_return is not None:
        excess = annual_return - bench_return
        if excess <= 0.02:
            diags.append(
                Diagnostic(
                    dimension="收益",
                    severity="critical" if excess <= 0 else "warning",
                    issue="超额收益不显著",
                    evidence={"excess_annual_return": excess},
                    suggestion="替换或增强因子，扩大选股范围，检查因子是否仍有有效信息。",
                )
            )
    elif annual_return is not None and annual_return < 0.02:
        diags.append(
            Diagnostic(
                dimension="收益",
                severity="warning",
                issue="绝对收益偏低",
                evidence={"annual_return": annual_return},
                suggestion="提升因子质量或调整持仓集中度。",
            )
        )

    if sharpe is not None and (not np.isfinite(sharpe) or sharpe < 0.5):
        diags.append(
            Diagnostic(
                dimension="稳定性",
                severity="warning",
                issue="夏普比率偏低，风险调整后收益不足",
                evidence={"sharpe_ratio": sharpe},
                suggestion="降低换手与波动暴露，优先选择参数更稳健的变体。",
            )
        )
    if calmar is not None and np.isfinite(calmar) and calmar < 0.5:
        diags.append(
            Diagnostic(
                dimension="稳定性",
                severity="warning",
                issue="卡玛比率偏低，回撤修复能力不足",
                evidence={"calmar_ratio": calmar},
                suggestion="评估最大回撤时段并加入回撤控制机制。",
            )
        )
    return diags


def propose_variants(
    base_factor: str = "momentum_20",
    base_params: Optional[dict] = None,
    top_n: int = 20,
    rebalance_freq: str = "ME",
) -> list[dict]:
    """生成 2-3 个维度上的有限变体，限制迭代规模防止过拟合。"""
    registry = factor_tool.FACTOR_REGISTRY.get(base_factor, {})
    default_window = int((registry.get("params") or {}).get("window", 0) or 0)
    base_params = dict(base_params or {})

    variants: list[dict] = []
    if base_factor.startswith(("momentum", "volatility")) and default_window > 0:
        windows = sorted(
            {
                max(5, default_window // 2),
                default_window,
                default_window * 2,
            }
        )
        for window in windows:
            for freq in ("W", "ME"):
                params = {**base_params, "window": window}
                variants.append(
                    {
                        "name": f"{base_factor}_w{window}_{freq}",
                        "factor": base_factor,
                        "params": params,
                        "top_n": top_n,
                        "rebalance_freq": freq,
                        "rationale": f"检验窗口 {window} 与调仓频率 {freq} 的稳定性",
                    }
                )
    else:
        for n in sorted({10, top_n, 30}):
            for freq in ("W", "ME"):
                variants.append(
                    {
                        "name": f"{base_factor}_n{n}_{freq}",
                        "factor": base_factor,
                        "params": base_params,
                        "top_n": n,
                        "rebalance_freq": freq,
                        "rationale": f"检验持仓数 {n} 与调仓频率 {freq} 的稳定性",
                    }
                )
    return variants


def objective_score(metrics: dict) -> float:
    """综合评分：夏普为主，惩罚回撤与过高换手。"""
    sharpe = metrics.get("sharpe_ratio")
    drawdown = abs(float(metrics.get("max_drawdown") or 0.0))
    turnover = float(metrics.get("annual_turnover") or 0.0)
    if sharpe is None or not np.isfinite(sharpe):
        return -math.inf
    return float(sharpe - 0.5 * drawdown - max(turnover - 1.0, 0.0))
