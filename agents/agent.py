"""单 Agent MVP 编排：数据 -> 因子 -> 回测 -> 绩效 -> 风控 -> 报告。

LLM 层只负责调度与文本，所有数值一律来自工具执行结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import BacktestConfig, CostConfig, RiskConfig, get_config
from memory.strategy_store import StrategyStore
from tools import data_tool, factor_tool, performance_tool, risk_tool
from tools.backtest_tool import run_backtest
from tools.report_tool import build_markdown_report


@dataclass
class TaskSpec:
    index_code: str = "000300"
    universe_date: str = "2019-12-31"
    start_date: str = "2018-01-01"
    end_date: str = "2023-12-31"
    factor: str = "momentum_20"
    factor_params: Optional[dict] = None
    top_n: int = 20
    rebalance_freq: str = "ME"
    weight_scheme: str = "equal"
    provider: str = "synthetic"
    strict_history: bool = True
    strategy_name: str = "20日动量选股策略"
    output_dir: str = "reports/output/mvp_demo"


def run_mvp_pipeline(spec: TaskSpec | None = None, config=None) -> dict:
    """执行完整 MVP 流水线，返回结果字典与报告路径。"""
    spec = spec or TaskSpec()
    config = config or get_config()
    data_tool.ensure_dirs()

    trace = []

    # 1. 股票池（历史时点成分）
    constituents = data_tool.get_index_constituents(
        spec.index_code,
        spec.universe_date,
        provider=spec.provider,
        strict_history=spec.strict_history,
        fallback_to_synthetic=True,
    )
    symbols = constituents["symbol"].tolist()
    trace.append(f"成分股: {len(symbols)} 只 @ {spec.universe_date}")

    # 2. 行情与清洗
    panel = data_tool.get_daily_price(
        symbols,
        spec.start_date,
        spec.end_date,
        provider=spec.provider,
        fallback_to_synthetic=True,
    )
    panel = data_tool.clean_panel(panel, drop_zero_volume=False)
    panel = risk_tool.screen_universe(panel, config.risk)
    trace.append(f"行情面板: {panel.shape[0]} 行")

    # 3. 因子矩阵
    factor_matrix = factor_tool.compute_factor_matrix(
        panel, factor=spec.factor, params=spec.factor_params, winsorize=3.0
    )
    trace.append(f"因子矩阵: {factor_matrix.shape[0]} 日 x {factor_matrix.shape[1]} 标的")

    # 4. 基准
    bench_panel = data_tool.get_daily_price(
        spec.index_code, spec.start_date, spec.end_date, provider=spec.provider
    )
    benchmark_close = bench_panel["close"].groupby(level="date").last()

    # 5. 回测
    price_matrix = panel["close"].unstack("symbol")
    result = run_backtest(
        factor_matrix,
        price_matrix,
        benchmark_close=benchmark_close,
        top_n=spec.top_n,
        rebalance_freq=spec.rebalance_freq,
        weight_scheme=spec.weight_scheme,
        costs=config.cost,
        max_single_weight=config.backtest.max_single_weight,
        initial_capital=config.backtest.initial_capital,
    )
    trace.append(f"回测完成: {len(result.rebalance_dates)} 个调仓日")

    # 6. 绩效
    benchmark_nav = benchmark_close / benchmark_close.iloc[0]
    annual_turnover = performance_tool.compute_annual_turnover(
        result.turnover, price_matrix.index
    )
    metrics = performance_tool.compute_metrics(
        result.nav,
        benchmark_nav=benchmark_nav,
        risk_free_rate=config.backtest.risk_free_rate,
        periods_per_year=config.backtest.periods_per_year,
        annual_turnover=annual_turnover,
    )
    metrics["benchmark_nav"] = benchmark_nav

    # 7. 风控
    sector_map = constituents.set_index("symbol")["sector"]
    risk_report = risk_tool.evaluate_backtest(
        result.weights,
        metrics["max_drawdown"],
        annual_turnover,
        holdings_sector=sector_map,
        config=config.risk,
    )
    trace.append(risk_report.summary())

    # 8. 报告与记忆
    report_path = build_markdown_report(
        result,
        metrics,
        strategy_name=spec.strategy_name,
        factor_name=spec.factor,
        benchmark_name="沪深300",
        universe_desc=f"{spec.index_code} 成分股",
        output_dir=spec.output_dir,
        risk_summary=risk_report.summary(),
    )
    store = StrategyStore()
    store.save_run(
        strategy_name=spec.strategy_name,
        factor_combo=[spec.factor],
        params=result.params,
        data_start=spec.start_date,
        data_end=spec.end_date,
        metrics=metrics,
        report_path=report_path,
    )
    trace.append(f"研报已生成: {report_path}")

    return {
        "spec": spec,
        "result": result,
        "metrics": metrics,
        "risk_report": risk_report,
        "report_path": report_path,
        "trace": trace,
    }
