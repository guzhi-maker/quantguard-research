"""Agent 编排：MVP 单策略流水线与阶段 2 反思迭代流水线。

LLM 层只负责调度与文本，所有数值一律来自工具执行结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import AppConfig, BacktestConfig, CostConfig, RiskConfig, get_config
from memory.strategy_store import StrategyStore
from memory.text_memory import TextMemoryStore
from tools import data_tool, factor_tool, performance_tool, reflection_tool, risk_tool, visual_tool
from tools.backtest_tool import run_backtest
from tools.report_tool import build_iteration_report, build_markdown_report


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


@dataclass
class PreparedData:
    constituents: pd.DataFrame
    panel: pd.DataFrame
    price_matrix: pd.DataFrame
    benchmark_close: pd.Series
    benchmark_nav: pd.Series
    sector_map: pd.Series
    financials: Optional[pd.DataFrame] = None


def _prepare_data(spec: TaskSpec, config: AppConfig) -> PreparedData:
    """加载并清洗一次数据，供多个回测变体复用。"""
    constituents = data_tool.get_index_constituents(
        spec.index_code,
        spec.universe_date,
        provider=spec.provider,
        strict_history=spec.strict_history,
        fallback_to_synthetic=True,
    )
    symbols = constituents["symbol"].tolist()
    panel = data_tool.get_daily_price(
        symbols,
        spec.start_date,
        spec.end_date,
        provider=spec.provider,
        fallback_to_synthetic=True,
    )
    panel = data_tool.clean_panel(panel, drop_zero_volume=False)
    panel = risk_tool.screen_universe(panel, config.risk)
    price_matrix = panel["close"].unstack("symbol").sort_index()
    benchmark_close = data_tool.get_daily_price(
        spec.index_code, spec.start_date, spec.end_date, provider=spec.provider
    )["close"].groupby(level="date").last()
    benchmark_close = benchmark_close.reindex(price_matrix.index).ffill().dropna()
    price_matrix = price_matrix.reindex(benchmark_close.index)
    benchmark_nav = benchmark_close / benchmark_close.iloc[0]
    sector_map = constituents.set_index("symbol")["sector"]

    financials = None
    if spec.provider == "synthetic" or spec.factor in {"pe", "pb", "roe"}:
        financials = data_tool.get_financials(
            symbols, spec.start_date, spec.end_date, provider="synthetic"
        )
    return PreparedData(
        constituents=constituents,
        panel=panel,
        price_matrix=price_matrix,
        benchmark_close=benchmark_close,
        benchmark_nav=benchmark_nav,
        sector_map=sector_map,
        financials=financials,
    )


def _evaluate_variant(
    prepared: PreparedData,
    spec: TaskSpec,
    config: AppConfig,
    factor: str,
    params: Optional[dict],
    top_n: int,
    rebalance_freq: str,
    name: str,
    strategy_name: str,
    output_dir: Optional[str] = None,
    save_report: bool = False,
    store: bool = True,
) -> dict:
    """用同一份行情数据执行单个策略变体，返回指标与风控结果。"""
    factor_matrix = factor_tool.compute_factor_matrix(
        prepared.panel,
        factor=factor,
        params=params,
        winsorize=3.0,
        financials=prepared.financials,
    )
    result = run_backtest(
        factor_matrix,
        prepared.price_matrix,
        benchmark_close=prepared.benchmark_close,
        top_n=top_n,
        rebalance_freq=rebalance_freq,
        weight_scheme=spec.weight_scheme,
        costs=config.cost,
        max_single_weight=config.backtest.max_single_weight,
        initial_capital=config.backtest.initial_capital,
    )
    annual_turnover = performance_tool.compute_annual_turnover(
        result.turnover, prepared.price_matrix.index
    )
    metrics = performance_tool.compute_metrics(
        result.nav,
        benchmark_nav=prepared.benchmark_nav,
        risk_free_rate=config.backtest.risk_free_rate,
        periods_per_year=config.backtest.periods_per_year,
        annual_turnover=annual_turnover,
    )
    metrics["benchmark_nav"] = prepared.benchmark_nav
    risk_report = risk_tool.evaluate_backtest(
        result.weights,
        metrics["max_drawdown"],
        annual_turnover,
        holdings_sector=prepared.sector_map,
        config=config.risk,
    )

    report_path = None
    if save_report:
        sector_exposure = risk_tool.compute_sector_exposure(
            result.weights, prepared.sector_map
        )
        style_exposure = risk_tool.compute_style_exposure(result.weights, factor_matrix)
        style_exposure = risk_tool.compute_style_exposure(result.weights, factor_matrix, factor_id=factor)
        report_path = build_markdown_report(
            result,
            metrics,
            strategy_name=strategy_name,
            factor_name=factor,
            benchmark_name="沪深300",
            universe_desc=f"{spec.index_code} 成分股",
            output_dir=output_dir or spec.output_dir,
            risk_summary=risk_report.summary(),
            sector_exposure=sector_exposure,
            style_exposure=style_exposure,
        )
    if store:
        store = StrategyStore()
        store_metrics = {k: v for k, v in metrics.items() if k != "benchmark_nav"}
        store.save_run(
            strategy_name=strategy_name,
            factor_combo=[factor],
            params=result.params,
            data_start=spec.start_date,
            data_end=spec.end_date,
            metrics=store_metrics,
            report_path=report_path,
        )
    return {
        "name": name,
        "factor": factor,
        "params": dict(params or {}),
        "top_n": top_n,
        "rebalance_freq": rebalance_freq,
        "result": result,
        "metrics": metrics,
        "risk_report": risk_report,
        "report_path": report_path,
    }


def run_mvp_pipeline(spec: TaskSpec | None = None, config: AppConfig | None = None) -> dict:
    """执行完整 MVP 流水线，返回结果字典与报告路径。"""
    spec = spec or TaskSpec()
    config = config or get_config()
    data_tool.ensure_dirs()
    prepared = _prepare_data(spec, config)
    trace = [f"成分股: {len(prepared.constituents)} 只 @ {spec.universe_date}"]

    outcome = _evaluate_variant(
        prepared,
        spec,
        config,
        factor=spec.factor,
        params=spec.factor_params,
        top_n=spec.top_n,
        rebalance_freq=spec.rebalance_freq,
        name=spec.strategy_name,
        strategy_name=spec.strategy_name,
        output_dir=spec.output_dir,
        save_report=True,
        store=True,
    )
    trace.append(f"行情面板: {prepared.panel.shape[0]} 行")
    trace.append(f"回测完成: {len(outcome['result'].rebalance_dates)} 个调仓日")
    trace.append(outcome["risk_report"].summary())
    trace.append(f"研报已生成: {outcome['report_path']}")

    return {
        "spec": spec,
        "result": outcome["result"],
        "metrics": outcome["metrics"],
        "risk_report": outcome["risk_report"],
        "report_path": outcome["report_path"],
        "trace": trace,
    }


def run_stage2_pipeline(
    spec: TaskSpec | None = None,
    config: AppConfig | None = None,
) -> dict:
    """阶段 2：初始回测 -> 反思诊断 -> 参数变体 -> 最优方案 -> 迭代报告。"""
    spec = spec or TaskSpec(
        strategy_name="20日动量策略（阶段2迭代）",
        output_dir="reports/output/stage2",
    )
    config = config or get_config()
    data_tool.ensure_dirs()
    prepared = _prepare_data(spec, config)
    trace = [f"成分股: {len(prepared.constituents)} 只 @ {spec.universe_date}"]

    base = _evaluate_variant(
        prepared,
        spec,
        config,
        factor=spec.factor,
        params=spec.factor_params,
        top_n=spec.top_n,
        rebalance_freq=spec.rebalance_freq,
        name=spec.strategy_name,
        strategy_name=spec.strategy_name,
        output_dir=f"{spec.output_dir}/base",
        save_report=True,
        store=True,
    )
    trace.append("初始回测完成")
    trace.append(base["risk_report"].summary())

    diagnostics = reflection_tool.diagnose_strategy(
        base["metrics"], risk_report=base["risk_report"], params=base["params"]
    )
    trace.extend(f"反思: {d.issue}" for d in diagnostics)

    variants = reflection_tool.propose_variants(
        spec.factor,
        spec.factor_params,
        top_n=spec.top_n,
        rebalance_freq=spec.rebalance_freq,
    )
    variant_outcomes = []
    for variant in variants:
        outcome = _evaluate_variant(
            prepared,
            spec,
            config,
            factor=variant["factor"],
            params=variant["params"],
            top_n=variant["top_n"],
            rebalance_freq=variant["rebalance_freq"],
            name=variant["name"],
            strategy_name=f"{variant['name']} 变体",
            store=True,
        )
        variant_outcomes.append(outcome)
    trace.append(f"自动迭代完成: {len(variant_outcomes)} 组参数变体")

    best = max(
        [base, *variant_outcomes],
        key=lambda item: reflection_tool.objective_score(item["metrics"]),
    )
    if best is not base:
        best = _evaluate_variant(
            prepared,
            spec,
            config,
            factor=best["factor"],
            params=best["params"],
            top_n=best["top_n"],
            rebalance_freq=best["rebalance_freq"],
            name=best["name"],
            strategy_name=f"{best['name']} 最优方案",
            output_dir=f"{spec.output_dir}/best",
            save_report=True,
            store=True,
        )
    trace.append(f"最优方案: {best['name']}")

    # 文本记忆：沉淀策略逻辑与复盘摘要
    text_memory = TextMemoryStore()
    for item in [base, best]:
        m = item["metrics"]
        text_memory.add_document(
            text=(
                f"策略 {item['name']}：因子 {item['factor']}，参数 {item['params']}，"
                f"年化收益 {m.get('annual_return'):.2%}，夏普 {m.get('sharpe_ratio'):.2f}，"
                f"最大回撤 {m.get('max_drawdown'):.2%}。"
            ),
            metadata={
                "type": "strategy_iteration",
                "factor": item["factor"],
                "rebalance_freq": item["rebalance_freq"],
            },
        )
    similar = text_memory.search(
        f"{spec.factor} {spec.rebalance_freq} 选股策略", k=3, threshold=0.0
    )
    trace.append(f"记忆检索: 找到 {len(similar)} 条历史同类方案")

    # 参数敏感性热力图
    heatmap_table = _build_heatmap_table(base, variant_outcomes, spec)
    heatmap_path = None
    if heatmap_table is not None and not heatmap_table.empty:
        heatmap_path = visual_tool.plot_parameter_heatmap(
            heatmap_table,
            save_path=f"{spec.output_dir}/parameter_heatmap.png",
            value_label="年化收益",
        )

    report_path = build_iteration_report(
        base=base,
        variants=variant_outcomes,
        best=best,
        diagnostics=diagnostics,
        output_dir=spec.output_dir,
        strategy_name=spec.strategy_name,
        heatmap_table=heatmap_table,
        heatmap_path=heatmap_path,
    )
    trace.append(f"迭代报告已生成: {report_path}")

    return {
        "spec": spec,
        "base": base,
        "variants": variant_outcomes,
        "best": best,
        "diagnostics": diagnostics,
        "report_path": report_path,
        "trace": trace,
    }


def _build_heatmap_table(
    base: dict,
    variants: list[dict],
    spec: TaskSpec,
) -> Optional[pd.DataFrame]:
    items = [base, *variants]
    if spec.factor.startswith(("momentum", "volatility")):
        rows = [
            {
                "window": int(item["params"].get("window", 0)),
                "rebalance": item["rebalance_freq"],
                "annual_return": item["metrics"].get("annual_return"),
            }
            for item in items
            if item["params"].get("window")
        ]
        if not rows:
            return None
        df = pd.DataFrame(rows).dropna(subset=["annual_return"])
        if df.empty:
            return None
        table = df.pivot_table(
            index="rebalance", columns="window", values="annual_return", aggfunc="mean"
        )
        table.index.name = "调仓频率"
        table.columns.name = "参数窗口"
        return table.sort_index(axis=1)
    rows = [
        {
            "top_n": item["top_n"],
            "rebalance": item["rebalance_freq"],
            "annual_return": item["metrics"].get("annual_return"),
        }
        for item in items
    ]
    df = pd.DataFrame(rows).dropna(subset=["annual_return"])
    if df.empty:
        return None
    table = df.pivot_table(
        index="rebalance", columns="top_n", values="annual_return", aggfunc="mean"
    )
    table.index.name = "调仓频率"
    table.columns.name = "持仓数量"
    return table.sort_index(axis=1)
