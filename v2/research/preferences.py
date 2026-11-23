"""投资偏好解析：风险预算（CVaR 主轴）与持有期限。

设计口径见 docs/quant-research-v2-workflow.md 第 2 节。

核心约束：
- 风险偏好只解析为组合层参数，绝不进入模型层。
- 期限偏好只解析为标签长度、调仓频率、建议带宽与成本假设。
- 档位之间线性插值，允许用户落在非整数档。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

RISK_MIN = 1.0
RISK_MAX = 10.0
HORIZON_MIN = 1
HORIZON_MAX = 3

RISK_TIERS: tuple[dict, ...] = (
    {
        "key": "conservative",
        "label": "保守",
        "cvar95": 0.04,
        "vol": 0.08,
        "mdd": 0.10,
        "single_cap": 0.04,
        "industry_cap": 0.12,
        "positions": 50,
        "cash_floor": 0.20,
    },
    {
        "key": "steady",
        "label": "稳健",
        "cvar95": 0.06,
        "vol": 0.12,
        "mdd": 0.15,
        "single_cap": 0.06,
        "industry_cap": 0.16,
        "positions": 30,
        "cash_floor": 0.12,
    },
    {
        "key": "balanced",
        "label": "平衡",
        "cvar95": 0.09,
        "vol": 0.16,
        "mdd": 0.20,
        "single_cap": 0.08,
        "industry_cap": 0.22,
        "positions": 15,
        "cash_floor": 0.05,
    },
    {
        "key": "growth",
        "label": "进取",
        "cvar95": 0.13,
        "vol": 0.22,
        "mdd": 0.27,
        "single_cap": 0.12,
        "industry_cap": 0.30,
        "positions": 10,
        "cash_floor": 0.02,
    },
    {
        "key": "aggressive",
        "label": "激进",
        "cvar95": 0.18,
        "vol": 0.28,
        "mdd": 0.35,
        "single_cap": 0.15,
        "industry_cap": 0.40,
        "positions": 6,
        "cash_floor": 0.00,
    },
)

_NUMERIC_RISK_FIELDS = (
    "cvar95",
    "vol",
    "mdd",
    "single_cap",
    "industry_cap",
    "positions",
    "cash_floor",
)

HORIZON_TIERS: tuple[dict, ...] = (
    {
        "level": 1,
        "label": "短",
        "days": 5,
        "rebalance": "W",
        "min_hold": 5,
        "turnover_cap": 1.2,
        "band_pp": 1.5,
        "cost_bps": 2.0,
        "half_life_max": 10,
    },
    {
        "level": 2,
        "label": "中",
        "days": 20,
        "rebalance": "ME",
        "min_hold": 20,
        "turnover_cap": 0.6,
        "band_pp": 2.5,
        "cost_bps": 0.0,
        "half_life_max": 40,
    },
    {
        "level": 3,
        "label": "长",
        "days": 60,
        "rebalance": "QE",
        "min_hold": 60,
        "turnover_cap": 0.3,
        "band_pp": 4.0,
        "cost_bps": -1.0,
        "half_life_max": 10**6,
    },
)


@dataclass(frozen=True)
class Preference:
    """解析后的偏好：组合层读 risk，模型层只读 horizon。"""

    risk_level: float
    horizon_level: int
    risk: dict = field(default_factory=dict)
    horizon: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"R{self.risk_level:.1f}-H{self.horizon_level}"

    @property
    def label(self) -> str:
        return f"{self.risk['label']} / {self.horizon['label']}"

    def as_dict(self) -> dict:
        return {
            "risk_level": round(self.risk_level, 2),
            "horizon_level": self.horizon_level,
            "risk": dict(self.risk),
            "horizon": dict(self.horizon),
        }


def _clamp(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _interpolate_risk(level: float) -> dict:
    """把 1-10 的连续档位映射到五档之间。"""
    span = RISK_MAX - RISK_MIN
    position = (level - RISK_MIN) / span * (len(RISK_TIERS) - 1)
    lower = int(position)
    upper = min(lower + 1, len(RISK_TIERS) - 1)
    weight = position - lower
    left, right = RISK_TIERS[lower], RISK_TIERS[upper]

    resolved: dict = {}
    for name in _NUMERIC_RISK_FIELDS:
        resolved[name] = float(left[name]) * (1 - weight) + float(right[name]) * weight
    resolved["positions"] = int(round(resolved["positions"]))
    resolved["key"] = left["key"] if weight < 0.5 else right["key"]
    resolved["label"] = left["label"] if weight < 0.5 else right["label"]
    resolved["tier_index"] = lower if weight < 0.5 else upper
    return resolved


def resolve(risk: float = 5.0, horizon: int = 2, positions: int | None = None) -> Preference:
    """解析风险与期限偏好。risk 支持 1.0-10.0，horizon 支持 1-3（5/20/60 日）。

    positions 可选：覆盖档位默认持仓数（例如固定 15 只）。
    """
    risk_level = _clamp(float(risk), RISK_MIN, RISK_MAX)
    horizon_level = int(_clamp(int(horizon), HORIZON_MIN, HORIZON_MAX))
    risk_dict = _interpolate_risk(risk_level)
    if positions:
        risk_dict["positions"] = max(1, int(positions))
    return Preference(
        risk_level=risk_level,
        horizon_level=horizon_level,
        risk=risk_dict,
        horizon=dict(HORIZON_TIERS[horizon_level - 1]),
    )


def ladder(horizon: int = 2, positions: int | None = None) -> list[Preference]:
    """生成五档风险阶梯，用于组合层一次解出全部方案。"""
    return [
        resolve(level, horizon, positions=positions)
        for level in (1.0, 3.25, 5.5, 7.75, 10.0)
    ]


def suggest_tier(max_monthly_loss: float) -> dict:
    """用"最差一个月能接受亏多少"反推推荐档位。

    返回推荐档位、对应的 CVaR 预算与最接近的标准档标签。
    """
    loss = _clamp(abs(float(max_monthly_loss)), 0.01, 0.40)
    span = RISK_MAX - RISK_MIN
    # CVaR 随档位线性变化：1 档 4%，10 档 18%
    low_cvar = RISK_TIERS[0]["cvar95"]
    high_cvar = RISK_TIERS[-1]["cvar95"]
    ratio = _clamp((loss - low_cvar) / (high_cvar - low_cvar), 0.0, 1.0)
    level = RISK_MIN + ratio * span
    resolved = resolve(level)
    return {
        "risk_level": round(level, 2),
        "cvar95": round(resolved.risk["cvar95"], 4),
        "tier": resolved.risk["label"],
        "suggestion": resolved.label,
    }


def horizon_options() -> list[dict]:
    return [dict(item) for item in HORIZON_TIERS]


def risk_tier_table() -> list[dict]:
    return [dict(item) for item in RISK_TIERS]


def describe(preference: Preference) -> str:
    risk = preference.risk
    horizon = preference.horizon
    return (
        f"{risk['label']}（CVaR95 {risk['cvar95']:.1%}，波动目标 {risk['vol']:.1%}，"
        f"单票上限 {risk['single_cap']:.0%}，持仓 {risk['positions']} 只）"
        f" / {horizon['label']}（{horizon['days']} 日标签，{horizon['rebalance']} 调仓，"
        f"带宽 ±{horizon['band_pp']:.1f}pp）"
    )


def cost_profile(preference: Preference) -> dict:
    """期限档对应的成本假设：短期限更严格，长期限允许更宽松。"""
    horizon = preference.horizon
    return {
        "slippage_extra_bps": float(horizon["cost_bps"]),
        "turnover_cap": float(horizon["turnover_cap"]),
        "impact_days": float(horizon["days"]),
    }


def iter_grid(horizons: Iterable[int] = (1, 2, 3), risk_levels: Iterable[float] = (1.0, 5.5, 10.0)) -> list[Preference]:
    return [resolve(risk, horizon) for horizon in horizons for risk in risk_levels]
