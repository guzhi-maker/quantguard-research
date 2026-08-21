"""LangChain 工具注册（第3周目标）。

未安装 langchain 时自动降级为普通函数，保证核心流程不依赖 LLM 也能运行。
"""

from __future__ import annotations

from typing import Optional

try:  # pragma: no cover - 取决于环境
    from langchain_core.tools import tool as _langchain_tool
except ImportError:  # pragma: no cover
    _langchain_tool = None


def _register(name: str, description: str):
    def decorator(fn):
        if _langchain_tool is None:
            return fn
        return _langchain_tool(name, description=description)(fn)

    return decorator


@_register("get_daily_price", "获取股票/指数日线行情，返回标准 DataFrame")
def get_daily_price_tool(pool, start_date, end_date, provider="auto", adjust="qfq"):
    from tools.data_tool import get_daily_price

    return get_daily_price(pool, start_date, end_date, provider=provider, adjust=adjust)


@_register("get_index_constituents", "获取指数成分股，严格模式下拒绝用当前成分回测历史")
def get_index_constituents_tool(index_code, date, strict_history=True):
    from tools.data_tool import get_index_constituents

    return get_index_constituents(index_code, date, strict_history=strict_history)


@_register("compute_factor", "计算因子矩阵，自动做截面去极值与 Z-Score 标准化")
def compute_factor_tool(panel, factor="momentum_20", winsorize=3.0):
    from tools.factor_tool import compute_factor_matrix

    return compute_factor_matrix(panel, factor=factor, winsorize=winsorize)


@_register("run_backtest", "执行选股回测，内置交易成本与 T+1 约束")
def run_backtest_tool(factor_matrix, price_matrix, top_n=20, rebalance_freq="ME", weight_scheme="equal"):
    from tools.backtest_tool import run_backtest

    return run_backtest(
        factor_matrix,
        price_matrix,
        top_n=top_n,
        rebalance_freq=rebalance_freq,
        weight_scheme=weight_scheme,
    )


@_register("compute_metrics", "计算回测绩效指标，禁止 LLM 自行计算金融指标")
def compute_metrics_tool(nav, benchmark_nav=None, risk_free_rate=0.02):
    from tools.performance_tool import compute_metrics

    return compute_metrics(nav, benchmark_nav, risk_free_rate=risk_free_rate)
