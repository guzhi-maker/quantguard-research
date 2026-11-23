"""组合构建：CVaR 约束优化 + 可成交性 + 整手取整。

目标函数：最大化扣费后预期收益并惩罚换手；
约束：月 CVaR95 <= 风险预算、单票/行业上限、现金底线、换手上限。

设计口径见 docs/quant-research-v2-workflow.md 阶段 5。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from config.settings import CostConfig

LOT_SIZE = 100
MONTH_DAYS = 20
MIN_POSITION = 0.005
RISK_BETA = 0.95


@dataclass
class Portfolio:
    label: str
    risk_level: float
    weights: pd.Series
    lots: pd.Series
    expected_return: float
    cvar95: float
    volatility: float
    turnover: float
    cost_estimate: float
    cash_weight: float
    status: str = "ok"
    diagnostics: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "label": self.label,
            "risk_level": round(self.risk_level, 2),
            "status": self.status,
            "positions": int((self.weights > 0).sum()),
            "expected_return": _round(self.expected_return, 6),
            "cvar95": _round(self.cvar95, 6),
            "volatility": _round(self.volatility, 6),
            "turnover": _round(self.turnover, 4),
            "cost_estimate": _round(self.cost_estimate, 6),
            "cash_weight": _round(self.cash_weight, 4),
            **{
                key: (value if isinstance(value, bool) else _round(value, 4))
                for key, value in self.diagnostics.items()
            },
        }


def _round(value, digits: int = 4):
    if value is None:
        return None
    if isinstance(value, (int, np.integer)):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def monthly_scenarios(returns: pd.DataFrame, window: int = 500) -> pd.DataFrame:
    """把日收益转成 20 日复合收益情景库。"""
    gross = (1.0 + returns.fillna(0.0)).cumprod()
    monthly = gross / gross.shift(MONTH_DAYS) - 1.0
    monthly = monthly.iloc[MONTH_DAYS:].dropna(how="all")
    return monthly.tail(window)


def cvar(returns: np.ndarray, beta: float = RISK_BETA) -> float:
    """历史模拟法 CVaR：最差 (1-beta) 情形下的平均损失（正数表示亏损）。"""
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    losses = -np.sort(values)
    count = max(1, int(math.ceil((1.0 - beta) * losses.size)))
    return float(losses[:count].mean())


def excess_scenarios(scenarios: pd.DataFrame, baseline: pd.Series | None = None) -> pd.DataFrame:
    """把情景收益转成相对基准的超额收益。

    baseline 必须是"候选全集"的等权月收益；不传时才退回用当前列均值。
    注意：用当前已选中的列求均值会让等权组合的超额恒为 0，
    风险度量随之失效（等权模式会显示波动 0）。
    """
    if scenarios.empty:
        return scenarios
    if baseline is None:
        baseline = scenarios.mean(axis=1)
    aligned = pd.Series(baseline).reindex(scenarios.index)
    return scenarios.sub(aligned, axis=0)


def risk_aversion(preference) -> float:
    """风险厌恶系数：1 档 4.0，10 档 0.4，线性插值。

    CVaR 以惩罚项进入目标函数，因此档位越高越愿意承担下行风险，
    同时组合保持满仓（仅受现金底线约束），不会因硬约束而大面积持有现金。
    """
    span = max(1e-9, float(preference.risk_level) - 1.0)
    return float(max(0.4, 4.0 - 3.6 * span / 9.0))


def optimize_cvar(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    tradable: pd.Series | None = None,
    previous: pd.Series | None = None,
    costs: CostConfig | None = None,
    risk_aversion_override: float | None = None,
    industries: pd.Series | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    """单档 CVaR 约束组合优化。

    目标：最大化 预期收益 - λ·CVaR - 交易成本；约束：满仓（扣现金底）、单票与行业上限。
    λ 由风险档位给出，因此"风险偏好"通过 CVaR 惩罚强度影响权重。
    约束不可行时退回带约束的排序加权。
    """
    costs = costs or CostConfig()
    symbols = list(expected.index)
    if not symbols:
        return _empty_portfolio(preference)

    if tradable is not None:
        allowed = tradable.reindex(symbols).fillna(False).to_numpy(dtype=bool)
    else:
        allowed = np.ones(len(symbols), dtype=bool)
    if allowed.sum() == 0:
        return _empty_portfolio(preference, status="no_tradable")

    mu = expected.reindex(symbols).fillna(0.0).to_numpy(dtype=float)
    matrix = scenarios.reindex(columns=symbols).fillna(0.0).to_numpy(dtype=float)
    scenario_count = matrix.shape[0]
    if scenario_count < 20:
        matrix = np.vstack([matrix, np.zeros((20 - scenario_count, len(symbols)))])
        scenario_count = matrix.shape[0]

    cap = float(preference.risk["single_cap"])
    budget = float(1.0 - preference.risk["cash_floor"])
    industry_cap = float(preference.risk["industry_cap"])
    lam = (
        float(risk_aversion_override)
        if risk_aversion_override is not None
        else risk_aversion(preference)
    )

    n = len(symbols)
    alpha_index = n
    u_start = n + 1
    dpos_start = u_start + scenario_count
    dneg_start = dpos_start + n
    total_vars = dneg_start + n

    turnover_penalty = float(costs.round_trip_rate() * 0.5)
    c = np.zeros(total_vars)
    c[:n] = -mu
    c[alpha_index] = lam
    c[u_start : u_start + scenario_count] = lam / ((1.0 - RISK_BETA) * scenario_count)
    c[dpos_start : dpos_start + n] = turnover_penalty
    c[dneg_start : dneg_start + n] = turnover_penalty

    a_ub: list[np.ndarray] = []
    b_ub: list[float] = []

    # u_s + r_s·w + alpha >= 0  =>  -r_s·w - alpha - u_s <= 0
    for scenario in range(scenario_count):
        row = np.zeros(total_vars)
        row[:n] = matrix[scenario]
        row[alpha_index] = 1.0
        row[u_start + scenario] = 1.0
        a_ub.append(-row)
        b_ub.append(0.0)

    # 行业上限：分组过少时下限放宽到 1.2/组数，避免满仓约束不可行
    effective_industry_cap = industry_cap
    if industries is not None:
        groups = industries.reindex(symbols).fillna("未分类")
        group_count = max(1, int(groups.nunique()))
        floor_cap = 1.2 / group_count
        effective_industry_cap = max(industry_cap, floor_cap)
    else:
        groups = pd.Series(dtype=object)

    # 行业上限
    if industries is not None:
        for group in pd.unique(groups):
            members = (groups == group).to_numpy(dtype=float)
            if members.sum() == 0:
                continue
            row = np.zeros(total_vars)
            row[:n] = members
            a_ub.append(row)
            b_ub.append(effective_industry_cap)

    # 换手上限（仅在有上一期持仓时生效；首次建仓不做换手约束）
    previous_series = (
        (previous if previous is not None else pd.Series(dtype=float)).reindex(symbols).fillna(0.0)
    )
    has_previous = float(previous_series.abs().sum()) > 1e-12
    if has_previous:
        row = np.zeros(total_vars)
        row[dpos_start : dpos_start + n] = 1.0
        row[dneg_start : dneg_start + n] = 1.0
        a_ub.append(row)
        b_ub.append(float(preference.horizon["turnover_cap"]))

    # 单票上限（不可成交的股票直接设 0 上限）
    bounds: list[tuple[float, float]] = []
    for index in range(n):
        bounds.append((0.0, cap if allowed[index] else 0.0))
    bounds.append((None, None))  # alpha
    bounds.extend([(0.0, None)] * scenario_count)  # u >= 0
    bounds.extend([(0.0, None)] * (2 * n))  # d+, d- >= 0

    # 满仓 + 换手分解 d+ - d- = w - w_prev
    previous_vector = previous_series.to_numpy(dtype=float)
    a_eq: list[np.ndarray] = []
    b_eq: list[float] = []
    row = np.zeros(total_vars)
    row[:n] = 1.0
    a_eq.append(row)
    b_eq.append(budget)
    for index in range(n):
        row = np.zeros(total_vars)
        row[index] = 1.0
        row[dpos_start + index] = -1.0
        row[dneg_start + index] = 1.0
        a_eq.append(row)
        b_eq.append(float(previous_vector[index]))

    result = linprog(
        c,
        A_ub=np.vstack(a_ub),
        b_ub=np.asarray(b_ub),
        A_eq=np.vstack(a_eq),
        b_eq=np.asarray(b_eq),
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        return _fallback_portfolio(
            expected,
            scenarios,
            preference,
            costs,
            allowed,
            status="fallback",
            lambda_value=lam,
            baseline=baseline,
        )

    weights = pd.Series(result.x[:n], index=symbols).clip(lower=0.0)
    weights[weights < MIN_POSITION] = 0.0
    weights = _apply_risk_target(weights, scenarios, preference, baseline)
    portfolio = _finalize(
        weights,
        expected,
        scenarios,
        costs,
        preference,
        status="ok",
        previous=previous_series,
        baseline=baseline,
    )
    portfolio.diagnostics["lambda"] = lam
    portfolio.diagnostics["cvar_budget"] = float(preference.risk["cvar95"])
    portfolio.diagnostics["industry_cap_effective"] = round(effective_industry_cap, 4)
    portfolio.diagnostics["industry_cap_relaxed"] = bool(
        effective_industry_cap > industry_cap + 1e-12
    )
    return portfolio


def _apply_risk_target(
    weights: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    baseline: pd.Series | None = None,
) -> pd.Series:
    """按档位波动目标缩放总仓位，并把 CVaR 压回预算内。

    先由均值-风险优化决定"买什么"，再由风险预算决定"买多少"。
    这样风险偏好通过仓位（现金比例）体现，单调性由构造保证。
    """
    active = weights > 0
    if not active.any():
        return weights
    matrix = excess_scenarios(
        scenarios.reindex(columns=weights.index).fillna(0.0), baseline
    )
    vector = matrix.to_numpy(dtype=float) @ weights.to_numpy(dtype=float)
    volatility = float(np.std(vector, ddof=1) * math.sqrt(12.0)) if len(vector) > 2 else 0.0
    target = float(preference.risk["vol"])
    max_exposure = float(1.0 - preference.risk["cash_floor"])
    if volatility > 1e-9:
        exposure = min(max_exposure, target / volatility)
    else:
        exposure = max_exposure
    exposure = max(0.0, exposure)
    scaled = weights * exposure

    budget = float(preference.risk["cvar95"])
    portfolio_cvar = cvar(matrix.to_numpy(dtype=float) @ scaled.to_numpy(dtype=float))
    if portfolio_cvar > budget > 0:
        scaled = scaled * (budget / portfolio_cvar)
    return scaled


def _finalize(
    weights: pd.Series,
    expected: pd.Series,
    scenarios: pd.DataFrame,
    costs: CostConfig,
    preference,
    status: str = "ok",
    previous: pd.Series | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    matrix = excess_scenarios(
        scenarios.reindex(columns=weights.index).fillna(0.0), baseline
    )
    vector = matrix.to_numpy(dtype=float) @ weights.to_numpy(dtype=float)
    expected_return = float((expected.reindex(weights.index).fillna(0.0) * weights).sum())
    risk = cvar(vector)
    volatility = float(np.std(vector, ddof=1) * math.sqrt(12.0)) if len(vector) > 2 else float("nan")
    turnover_base = previous if previous is not None else pd.Series(dtype=float)
    turnover_base = turnover_base.reindex(weights.index).fillna(0.0)
    turnover = float((weights - turnover_base).abs().sum())
    cost_estimate = turnover * costs.round_trip_rate()
    invested = float(weights.sum())
    top = weights.sort_values(ascending=False)
    top3 = float(top.iloc[:3].sum()) if len(top) >= 3 else float(top.sum())
    hhi = float((weights.pow(2)).sum())

    return Portfolio(
        label=preference.risk["label"],
        risk_level=preference.risk_level,
        weights=weights[weights > 0].sort_values(ascending=False),
        lots=pd.Series(dtype=int),
        expected_return=expected_return - cost_estimate,
        cvar95=risk,
        volatility=volatility,
        turnover=turnover,
        cost_estimate=cost_estimate,
        cash_weight=max(0.0, 1.0 - invested),
        status=status,
        diagnostics={"top3_weight": top3, "hhi": hhi, "gross_exposure": invested},
    )


def _fallback_portfolio(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    costs: CostConfig,
    allowed: np.ndarray,
    status: str,
    lambda_value: float | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    """LP 不可行时的退路：按预期收益排序，逆波动加权并受单票上限约束。"""
    universe = expected[allowed].dropna()
    if universe.empty:
        return _empty_portfolio(preference, status="no_candidate")
    count = max(1, min(int(preference.risk["positions"]), len(universe)))
    chosen = universe.nlargest(count).index
    cap = float(preference.risk["single_cap"])
    weights = pd.Series(1.0 / len(chosen), index=chosen)
    for _ in range(20):
        over = weights > cap
        if not over.any():
            break
        excess = float((weights[over] - cap).sum())
        weights[over] = cap
        remaining = weights[~over]
        if remaining.empty:
            break
        weights[~over] = remaining + excess * remaining / remaining.sum()
    portfolio = _finalize(
        weights, expected, scenarios, costs, preference, status=status, baseline=baseline
    )
    portfolio.cash_weight = max(0.0, 1.0 - float(weights.sum()))
    if lambda_value is not None:
        portfolio.diagnostics["lambda"] = lambda_value
    return portfolio


def _empty_portfolio(preference, status: str = "empty") -> Portfolio:
    return Portfolio(
        label=preference.risk["label"],
        risk_level=preference.risk_level,
        weights=pd.Series(dtype=float),
        lots=pd.Series(dtype=int),
        expected_return=0.0,
        cvar95=0.0,
        volatility=0.0,
        turnover=0.0,
        cost_estimate=0.0,
        cash_weight=1.0,
        status=status,
    )


def round_to_lots(weights: pd.Series, prices: pd.Series, capital: float, lot_size: int = LOT_SIZE) -> pd.Series:
    """目标权重转整手股数，买不起或不足一手的标的不建仓。"""
    lots: dict[str, int] = {}
    if capital <= 0:
        return pd.Series(dtype=int)
    for symbol, weight in weights.items():
        try:
            price = float(prices.get(symbol, 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0 or weight <= 0:
            continue
        count = math.floor(float(weight) * capital / (price * lot_size) + 1e-9)
        if count >= 1:
            lots[symbol] = int(count)
    return pd.Series(lots, dtype=int).sort_values(ascending=False)


def select_rank_portfolio(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    tradable: pd.Series | None = None,
    prices: pd.Series | None = None,
    capital: float = 1_000_000.0,
    industries: pd.Series | None = None,
    costs: CostConfig | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    """排序选股 + 逆波动加权 + 风险目标缩放。

    先按预期收益取前 N 只，再在组内分配权重。相比全样本优化，
    这种做法不会被估计误差最大化的权重放大。
    """
    costs = costs or CostConfig()
    universe = expected.dropna()
    if tradable is not None:
        universe = universe[tradable.reindex(universe.index).fillna(False)]
    if universe.empty:
        return _empty_portfolio(preference, status="no_tradable")

    count = max(1, min(int(preference.risk["positions"]), len(universe)))
    chosen = universe.sort_values(ascending=False).head(count).index
    cap = float(preference.risk["single_cap"])
    industry_cap = float(preference.risk["industry_cap"])

    excess = excess_scenarios(scenarios.reindex(columns=chosen).fillna(0.0))
    vol = excess.std(ddof=1) * math.sqrt(12.0)
    inverse = 1.0 / vol.replace(0.0, np.nan).clip(lower=0.02)
    weights = inverse.fillna(inverse.median() if inverse.notna().any() else 1.0)
    if weights.sum() <= 0:
        weights = pd.Series(1.0, index=chosen)
    weights = weights / weights.sum()

    # 单票与行业上限：超限部分按比例回填，迭代收敛
    for _ in range(30):
        changed = False
        over = weights > cap + 1e-12
        if over.any():
            spare = float((weights[over] - cap).sum())
            weights[over] = cap
            rest = ~over
            if rest.any():
                weights[rest] += spare * weights[rest] / weights[rest].sum()
            changed = True
        if industries is not None:
            groups = industries.reindex(weights.index).fillna("未分类")
            exposure = weights.groupby(groups).sum()
            hot = exposure[exposure > industry_cap + 1e-12]
            if not hot.empty:
                for group in hot.index:
                    members = weights.index[groups == group]
                    scale = industry_cap / exposure[group]
                    freed = float(weights[members].sum() * (1.0 - scale))
                    weights[members] *= scale
                    cold = weights.index[groups != group]
                    if len(cold) and weights[cold].sum() > 0:
                        weights[cold] += freed * weights[cold] / weights[cold].sum()
                changed = True
        if not changed:
            break

    weights = _apply_risk_target(weights, scenarios, preference, baseline)
    portfolio = _finalize(
        weights, expected, scenarios, costs, preference, status="ok", baseline=baseline
    )
    if prices is not None:
        portfolio.lots = round_to_lots(portfolio.weights, prices, capital)
    portfolio.diagnostics["mode"] = "rank"
    return portfolio


def select_index_portfolio(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    tradable: pd.Series | None = None,
    prices: pd.Series | None = None,
    capital: float = 1_000_000.0,
    industries: pd.Series | None = None,
    costs: CostConfig | None = None,
    floor_positions: int = 30,
    baseline: pd.Series | None = None,
) -> Portfolio:
    """等权分散组合：当信号不足时用分散代替集中，避免把噪声放大成风险。

    取分数最高的 max(档位持仓数, floor_positions) 只等权持有，再按波动目标缩放。
    """
    costs = costs or CostConfig()
    universe = expected.dropna()
    if tradable is not None:
        universe = universe[tradable.reindex(universe.index).fillna(False)]
    if universe.empty:
        return _empty_portfolio(preference, status="no_tradable")
    count = max(int(preference.risk["positions"]), int(floor_positions))
    count = min(count, len(universe))
    chosen = universe.sort_values(ascending=False).head(count).index
    weights = pd.Series(1.0 / len(chosen), index=chosen)
    weights = _apply_risk_target(weights, scenarios, preference, baseline)
    portfolio = _finalize(
        weights, expected, scenarios, costs, preference, status="ok", baseline=baseline
    )
    if prices is not None:
        portfolio.lots = round_to_lots(portfolio.weights, prices, capital)
    portfolio.diagnostics["mode"] = "index"
    return portfolio


def build_portfolio(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preference,
    mode: str = "optimize",
    tradable: pd.Series | None = None,
    prices: pd.Series | None = None,
    capital: float = 1_000_000.0,
    industries: pd.Series | None = None,
    costs: CostConfig | None = None,
    previous: pd.Series | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    if mode == "index":
        portfolio = select_index_portfolio(
            expected,
            scenarios,
            preference,
            tradable=tradable,
            prices=prices,
            capital=capital,
            industries=industries,
            costs=costs,
            baseline=baseline,
        )
        if previous is not None and float(previous.abs().sum()) > 0:
            portfolio = _limit_turnover(
                portfolio, previous, scenarios, expected, preference, costs, baseline=baseline
            )
        return portfolio
    if mode == "rank":
        portfolio = select_rank_portfolio(
            expected,
            scenarios,
            preference,
            tradable=tradable,
            prices=prices,
            capital=capital,
            industries=industries,
            costs=costs,
            baseline=baseline,
        )
        if previous is not None and float(previous.abs().sum()) > 0:
            portfolio = _limit_turnover(
                portfolio, previous, scenarios, expected, preference, costs, baseline=baseline
            )
        return portfolio
    return optimize_cvar(
        expected,
        scenarios,
        preference,
        tradable=tradable,
        previous=previous,
        costs=costs,
        industries=industries,
        baseline=baseline,
    )


def _limit_turnover(
    portfolio: Portfolio,
    previous: pd.Series,
    scenarios: pd.DataFrame,
    expected: pd.Series,
    preference,
    costs: CostConfig | None = None,
    baseline: pd.Series | None = None,
) -> Portfolio:
    """排序模式下也用换手上限约束：按比例混合旧仓与新仓。"""
    costs = costs or CostConfig()
    universe = portfolio.weights.index.union(previous.index)
    target = portfolio.weights.reindex(universe).fillna(0.0)
    base = previous.reindex(universe).fillna(0.0)
    turnover = float((target - base).abs().sum())
    cap = float(preference.horizon["turnover_cap"])
    if turnover <= cap or turnover <= 1e-9:
        return portfolio
    alpha = max(0.0, min(1.0, cap / turnover))
    blended = base * (1.0 - alpha) + target * alpha
    blended = blended[blended > 0]
    updated = _finalize(
        blended, expected, scenarios, costs, preference, status="ok", previous=base, baseline=baseline
    )
    updated.diagnostics["turnover_limited"] = round(alpha, 4)
    updated.diagnostics["mode"] = "rank"
    return updated


def build_ladder(
    expected: pd.Series,
    scenarios: pd.DataFrame,
    preferences: list,
    tradable: pd.Series | None = None,
    capital: float = 1_000_000.0,
    prices: pd.Series | None = None,
    costs: CostConfig | None = None,
    industries: pd.Series | None = None,
) -> list[Portfolio]:
    """一次解出风险阶梯上的全部组合（各档独立求解，不互相约束）。"""
    costs = costs or CostConfig()
    portfolios: list[Portfolio] = []
    for preference in preferences:
        portfolio = optimize_cvar(
            expected,
            scenarios,
            preference,
            tradable=tradable,
            previous=None,
            costs=costs,
            industries=industries,
        )
        if prices is not None:
            portfolio.lots = round_to_lots(portfolio.weights, prices, capital)
        portfolios.append(portfolio)
    return portfolios


def ladder_table(portfolios: list[Portfolio]) -> pd.DataFrame:
    return pd.DataFrame([portfolio.summary() for portfolio in portfolios])


def enforce_monotonic_cvar(
    portfolios: list[Portfolio],
    scenarios: pd.DataFrame,
    expected: pd.Series,
    costs: CostConfig | None = None,
    baseline: pd.Series | None = None,
) -> list[Portfolio]:
    """保证 CVaR 随档位严格上升。

    组合构成不同时，低档位可能因选到高波动标的而比高档位更"危险"。
    这里从最高档往回检查，必要时按比例缩减低档位仓位，
    使其 CVaR 明显低于相邻更高档位。
    """
    costs = costs or CostConfig()
    if len(portfolios) < 2:
        return portfolios
    output = list(portfolios)
    for index in range(len(output) - 2, -1, -1):
        higher = output[index + 1]
        target = higher.cvar95 * 0.95
        current = output[index]
        weights = current.weights.copy()
        guard = 0
        while current.cvar95 >= target and weights.sum() > 1e-4 and guard < 40:
            weights = weights * 0.9
            current = _finalize(
            weights,
            expected,
            scenarios,
            costs,
            _preference_of(current),
            baseline=baseline,
        )
            guard += 1
        if guard:
            current.diagnostics["monotonic_adjusted"] = guard
        output[index] = current
    return output


def _preference_of(portfolio: Portfolio):
    """用组合自身的标签构造一个最小 Preference，供重新计算统计量。"""
    from research.preferences import Preference

    return Preference(
        risk_level=portfolio.risk_level,
        horizon_level=3,
        risk={
            "label": portfolio.label,
            "single_cap": 1.0,
            "industry_cap": 1.0,
            "cash_floor": 0.0,
            "cvar95": 1.0,
            "vol": 1.0,
            "positions": 999,
            "key": "custom",
        },
        horizon={"days": 20, "turnover_cap": 10.0, "band_pp": 2.5, "label": "中"},
    )


def monotonic_cvar(portfolios: list[Portfolio]) -> dict:
    """检查 CVaR 是否随档位恶化。"""
    values = [portfolio.cvar95 for portfolio in portfolios]
    increasing = all(
        later >= earlier - 1e-9 for earlier, later in zip(values, values[1:])
    )
    return {
        "cvar": [round(value, 6) for value in values],
        "monotonic": bool(increasing),
        "spread": round(values[-1] - values[0], 6) if len(values) > 1 else 0.0,
    }


def industry_exposure(weights: pd.Series, industries: pd.Series) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    groups = industries.reindex(weights.index).fillna("未分类")
    return weights.groupby(groups).sum().sort_values(ascending=False)
