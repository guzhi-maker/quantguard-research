"""全局配置：目录、默认回测参数、交易成本与风控参数。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORT_DIR = PROJECT_ROOT / "reports"
CACHE_DIR = DATA_DIR / "cache"
CONSTITUENTS_DIR = DATA_DIR / "constituents"


@dataclass(frozen=True)
class CostConfig:
    """A 股交易成本模型，全部按单边比例配置。

    min_commission 为单笔最低佣金，向量化回测中按“每笔成交金额”近似处理。
    """

    commission_rate: float = 0.0002      # 佣金：万 2
    min_commission: float = 5.0          # 单笔最低佣金 5 元
    stamp_tax_rate: float = 0.001        # 印花税：千 1，仅卖出
    transfer_fee_rate: float = 0.00001   # 过户费：十万 1，双向
    slippage_rate: float = 0.001         # 滑点：千 1，双向

    def round_trip_rate(self, sell_share: float = 0.5) -> float:
        """估算一次调仓的合计成本率。

        sell_share 表示换手量中卖出占比，默认 0.5，即买卖各半。
        """
        buy_rate = self.commission_rate + self.transfer_fee_rate + self.slippage_rate
        sell_rate = (
            self.commission_rate
            + self.transfer_fee_rate
            + self.slippage_rate
            + self.stamp_tax_rate
        )
        return buy_rate + sell_share * sell_rate


@dataclass(frozen=True)
class BacktestConfig:
    rebalance_freq: str = "ME"           # ME=月末；W=周频；D=日频
    weight_scheme: str = "equal"         # equal / market_cap
    top_n: int = 20
    initial_capital: float = 1_000_000.0
    max_single_weight: float = 0.10      # 单票仓位上限
    benchmark_code: str = "000300"
    risk_free_rate: float = 0.02
    periods_per_year: int = 252


@dataclass(frozen=True)
class RiskConfig:
    max_single_position: float = 0.10
    max_industry_exposure: float = 0.30
    max_annual_turnover: float = 2.00
    max_drawdown_warn: float = 0.20
    max_drawdown_circuit: float = 0.30
    excluded_name_keywords: tuple = ("ST", "退")


@dataclass
class AppConfig:
    cost: CostConfig = field(default_factory=CostConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


def get_config() -> AppConfig:
    return AppConfig()
