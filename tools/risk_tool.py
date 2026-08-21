"""风控校验层：硬编码规则，独立于 LLM，策略必须先过校验再回测。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import RiskConfig
from tools import factor_tool


@dataclass
class RiskReport:
    passed: bool
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def add_violation(self, message: str) -> None:
        self.violations.append(message)
        self.passed = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "风控校验通过"
        parts = []
        if not self.passed:
            parts.append("风控拦截: " + "；".join(self.violations))
        if self.warnings:
            parts.append("风险提示: " + "；".join(self.warnings))
        return "；".join(parts)


def screen_universe(panel: pd.DataFrame, config: Optional[RiskConfig] = None) -> pd.DataFrame:
    """剔除 ST / *ST / 退市预警股票。"""
    config = config or RiskConfig()
    if "is_st" not in panel.columns:
        return panel
    return panel[~panel["is_st"]]


def validate_position_limits(
    weights: pd.DataFrame,
    config: Optional[RiskConfig] = None,
) -> RiskReport:
    """检查单票仓位上限。"""
    config = config or RiskConfig()
    report = RiskReport(passed=True)
    max_weight = float(weights.max().max()) if len(weights) else 0.0
    if max_weight > config.max_single_position + 1e-9:
        report.add_violation(
            f"单票最大仓位 {max_weight:.2%} 超过上限 {config.max_single_position:.0%}"
        )
    return report


def validate_industry_exposure(
    weights: pd.DataFrame,
    holdings_sector: pd.Series,
    config: Optional[RiskConfig] = None,
) -> RiskReport:
    """检查组合一级行业暴露。holdings_sector 为 symbol -> 行业名。"""
    config = config or RiskConfig()
    report = RiskReport(passed=True)
    if holdings_sector is None or len(holdings_sector) == 0:
        return report
    latest = weights.iloc[-1]
    mapped = pd.DataFrame({"weight": latest, "sector": holdings_sector.reindex(latest.index)})
    exposure = mapped.groupby("sector")["weight"].sum().dropna()
    for sector, weight in exposure.items():
        if weight > config.max_industry_exposure + 1e-9:
            report.add_violation(
                f"行业 {sector} 暴露 {weight:.2%} 超过上限 {config.max_industry_exposure:.0%}"
            )
    return report


def validate_turnover(
    annual_turnover: float,
    config: Optional[RiskConfig] = None,
) -> RiskReport:
    """检查年化换手率约束。"""
    config = config or RiskConfig()
    report = RiskReport(passed=True)
    if annual_turnover > config.max_annual_turnover:
        report.add_violation(
            f"年化换手率 {annual_turnover:.0%} 超过上限 {config.max_annual_turnover:.0%}"
        )
    return report


def validate_drawdown(
    max_drawdown: float,
    config: Optional[RiskConfig] = None,
) -> RiskReport:
    """回撤预警线 20%，熔断线 30%。"""
    config = config or RiskConfig()
    report = RiskReport(passed=True)
    if abs(max_drawdown) >= config.max_drawdown_circuit:
        report.add_violation(
            f"最大回撤 {max_drawdown:.2%} 触发熔断线 {config.max_drawdown_circuit:.0%}"
        )
    elif abs(max_drawdown) >= config.max_drawdown_warn:
        report.add_warning(
            f"最大回撤 {max_drawdown:.2%} 触发预警线 {config.max_drawdown_warn:.0%}"
        )
    return report


def evaluate_backtest(
    weights: pd.DataFrame,
    max_drawdown: float,
    annual_turnover: float,
    holdings_sector: Optional[pd.Series] = None,
    config: Optional[RiskConfig] = None,
) -> RiskReport:
    """综合风控校验，供回测后调用。"""
    report = RiskReport(passed=True)
    for sub in (
        validate_position_limits(weights, config),
        validate_industry_exposure(weights, holdings_sector, config),
        validate_turnover(annual_turnover, config),
        validate_drawdown(max_drawdown, config),
    ):
        report.violations.extend(sub.violations)
        report.warnings.extend(sub.warnings)
    report.passed = not report.violations
    return report


def compute_sector_exposure(
    weights: pd.DataFrame,
    holdings_sector: pd.Series,
    asof: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """计算最近持仓的行业暴露表。"""
    if weights.empty:
        return pd.DataFrame(columns=["sector", "weight"])
    row = weights.loc[asof] if asof is not None and asof in weights.index else weights.iloc[-1]
    mapped = pd.DataFrame({"weight": row, "sector": holdings_sector.reindex(row.index)})
    exposure = mapped.groupby("sector")["weight"].sum().dropna().sort_values(ascending=False)
    return exposure.rename("weight").reset_index()


def compute_style_exposure(
    weights: pd.DataFrame,
    factor_matrix: pd.DataFrame,
    factor_id: Optional[str] = None,
) -> pd.DataFrame:
    """计算组合在给定因子上的加权暴露（使用最近共同交易日）。

    当前输入为单因子矩阵，因此对全部成分的暴露求和，返回一行。
    """
    if weights.empty or factor_matrix.empty:
        return pd.DataFrame(columns=["factor", "style_name", "exposure"])
    common_dates = weights.index.intersection(factor_matrix.index)
    if len(common_dates) == 0:
        return pd.DataFrame(columns=["factor", "style_name", "exposure"])
    date = common_dates[-1]
    latest = weights.loc[date].reindex(factor_matrix.columns).fillna(0.0)
    exposure = float((latest * factor_matrix.loc[date]).sum())
    meta = factor_tool.list_factors().set_index("factor_id")
    style_name = meta.loc[factor_id, "name"] if factor_id in meta.index else "组合因子暴露"
    return pd.DataFrame(
        [{"factor": factor_id or "composite", "style_name": style_name, "exposure": exposure}]
    )
