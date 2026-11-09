"""量化投研 Agent 程序入口。

用法：
  python main.py demo --provider synthetic --factor momentum_20
  python main.py stage2 --factor momentum_20
  python main.py research --factors momentum_20,momentum_60
  python main.py web --port 8787
  python main.py repl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from agents.agent import TaskSpec, run_mvp_pipeline, run_stage2_pipeline, _prepare_data
from config.settings import get_config
from tools import data_tool, factor_research_tool, factor_tool
from tools.performance_tool import metrics_table
from memory.strategy_store import StrategyStore


def _print_metrics(metrics: dict) -> None:
    table = metrics_table(metrics)
    for _, row in table.iterrows():
        value = row["数值"]
        if value is None or (isinstance(value, float) and pd.isna(value)):
            display = "-"
        elif row["指标"] in {"夏普比率", "卡玛比率", "信息比率", "年化换手率"}:
            display = f"{value:.2f}" if row["指标"] != "年化换手率" else f"{value:.0%}"
        else:
            display = f"{value:.2%}"
        print(f"  {row['指标']:<10} {display}")


def cmd_demo(args: argparse.Namespace) -> int:
    spec = TaskSpec(
        index_code=args.index,
        universe_date=args.universe_date,
        start_date=args.start,
        end_date=args.end,
        factor=args.factor,
        top_n=args.top_n,
        rebalance_freq=args.rebalance,
        weight_scheme=args.weight,
        provider=args.provider,
        strict_history=args.strict_history,
        strategy_name=f"{args.factor} 选股策略",
        output_dir=args.output_dir,
    )
    try:
        outcome = run_mvp_pipeline(spec, get_config())
    except Exception as exc:  # 对演示场景给出可读错误
        print(f"流程失败: {exc}", file=sys.stderr)
        return 1

    print("\n== 执行轨迹 ==")
    for step in outcome["trace"]:
        print(f"  - {step}")

    print("\n== 核心绩效指标 ==")
    _print_metrics(outcome["metrics"])
    print(f"\n研报: {outcome['report_path']}")
    return 0


def cmd_stage2(args: argparse.Namespace) -> int:
    spec = TaskSpec(
        index_code=args.index,
        universe_date=args.universe_date,
        start_date=args.start,
        end_date=args.end,
        factor=args.factor,
        top_n=args.top_n,
        rebalance_freq=args.rebalance,
        weight_scheme=args.weight,
        provider=args.provider,
        strict_history=args.strict_history,
        strategy_name=f"{args.factor} 策略迭代优化",
        output_dir=args.output_dir,
    )
    try:
        outcome = run_stage2_pipeline(spec, get_config())
    except Exception as exc:
        print(f"流程失败: {exc}", file=sys.stderr)
        return 1

    print("\n== 执行轨迹 ==")
    for step in outcome["trace"]:
        print(f"  - {step}")

    print("\n== 反思诊断 ==")
    for diag in outcome["diagnostics"]:
        print(f"  - [{diag.dimension}/{diag.severity}] {diag.issue}")

    print("\n== 最优方案 ==")
    best = outcome["best"]
    _print_metrics(best["metrics"])
    print(f"\n迭代报告: {outcome['report_path']}")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    spec = TaskSpec(
        index_code=args.index,
        universe_date=args.universe_date,
        start_date=args.start,
        end_date=args.end,
        factor=args.factor or "momentum_20",
        provider=args.provider,
        strict_history=args.strict_history,
    )
    try:
        prepared = _prepare_data(spec, get_config())
        factor_ids = (
            [f.strip() for f in args.factors.split(",") if f.strip()]
            if args.factors
            else None
        )
        result = factor_research_tool.evaluate_factors(
            prepared.panel,
            prepared.price_matrix,
            factor_ids=factor_ids,
            horizons=(1, 5, 20),
            n_quantiles=args.quantiles,
            sample_ratio=args.sample_ratio,
            financials=prepared.financials,
        )
        report_path = factor_research_tool.build_factor_research_report(
            result, output_dir=args.output_dir
        )
    except Exception as exc:
        print(f"流程失败: {exc}", file=sys.stderr)
        return 1

    store = StrategyStore()
    for _, row in result.summary.iterrows():
        if row.get("skipped_reason"):
            continue
        store.upsert_factor_stats(
            factor_id=row["factor_id"],
            factor_name=row["factor_name"],
            factor_type=row["factor_type"],
            ic_mean=row.get("ic_mean"),
            ic_ir=row.get("ic_ir"),
            half_life_days=row.get("half_life_days"),
            ic_win_rate=row.get("ic_win_rate"),
            in_sample_ic=row.get("in_sample_ic"),
            out_sample_ic=row.get("out_sample_ic"),
            monotonicity=row.get("monotonicity"),
            quantile_spread=row.get("quantile_spread"),
        )

    print("\n== 因子有效性排名 ==")
    if result.summary.empty:
        print("  无有效因子")
    else:
        display = result.summary[
            [
                c
                for c in [
                    "factor_id", "factor_name", "ic_mean", "ic_ir",
                    "ic_win_rate", "half_life_days", "in_sample_ic",
                    "out_sample_ic", "monotonicity", "quantile_spread", "skipped_reason",
                ]
                if c in result.summary.columns
            ]
        ]
        print(display.to_string(index=False))
    print(f"\n因子研究报告: {report_path}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from webapp.server import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_repl() -> int:
    print("量化投研 Agent (命令式交互)")
    print("输入 help 查看命令，exit 退出。")
    while True:
        try:
            line = input("quant> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            return 0
        if not line:
            continue
        cmd, *rest = line.split()
        if cmd in {"exit", "quit"}:
            print("再见")
            return 0
        if cmd in {"help", "?"}:
            print(
                "可用命令:\n"
                "  demo                    运行完整回测研报流水线\n"
                "  stage2                  运行反思迭代优化流水线\n"
                "  research                批量因子有效性检验\n"
                "  data <code> <start> <end>  查看行情面板（synthetic 演示数据）\n"
                "  factors [start] [end]      查看因子覆盖情况\n"
                "  history                 查看历史策略记录\n"
                "  exit                    退出"
            )
        elif cmd == "demo":
            spec = TaskSpec(
                provider="synthetic",
                strict_history=False,
                strategy_name="20日动量选股策略（REPL）",
            )
            outcome = run_mvp_pipeline(spec, get_config())
            for step in outcome["trace"]:
                print(f"  - {step}")
            _print_metrics(outcome["metrics"])
            print(f"研报: {outcome['report_path']}")
        elif cmd == "stage2":
            spec = TaskSpec(
                provider="synthetic",
                strict_history=False,
                strategy_name="20日动量策略迭代（REPL）",
                output_dir="reports/output/stage2",
            )
            outcome = run_stage2_pipeline(spec, get_config())
            for step in outcome["trace"]:
                print(f"  - {step}")
            _print_metrics(outcome["best"]["metrics"])
            print(f"迭代报告: {outcome['report_path']}")
        elif cmd == "research":
            prepared = _prepare_data(
                TaskSpec(provider="synthetic", strict_history=False), get_config()
            )
            result = factor_research_tool.evaluate_factors(
                prepared.panel,
                prepared.price_matrix,
                financials=prepared.financials,
            )
            report_path = factor_research_tool.build_factor_research_report(result)
            print(result.summary.to_string(index=False))
            print(f"因子研究报告: {report_path}")
        elif cmd == "data" and len(rest) >= 3:
            code, start, end = rest[0], rest[1], rest[2]
            panel = data_tool.get_daily_price(code, start, end, provider="synthetic")
            print(panel.head(5))
            print(f"... 共 {len(panel)} 行")
        elif cmd == "factors":
            start = rest[0] if rest else "2023-01-01"
            end = rest[1] if len(rest) > 1 else "2023-12-31"
            panel = data_tool.get_daily_price("000300", start, end, provider="synthetic")
            print(factor_tool.list_factors().to_string(index=False))
            matrix = factor_tool.compute_factor_matrix(panel, factor="momentum_20")
            print(f"momentum_20 矩阵: {matrix.shape[0]} 日 x {matrix.shape[1]} 标的")
        elif cmd == "history":
            df = StrategyStore().list_runs()
            print(df[["strategy_id", "strategy_name", "data_start", "data_end"]].to_string(index=False) if not df.empty else "暂无历史记录")
        else:
            print(f"未知命令: {cmd}，输入 help 查看帮助")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="量化投研 Agent")
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="运行完整 MVP 回测研报流水线")
    demo.add_argument("--provider", default="synthetic", choices=["auto", "akshare", "synthetic"])
    demo.add_argument("--index", default="000300")
    demo.add_argument("--universe-date", default="2019-12-31")
    demo.add_argument("--start", default="2018-01-01")
    demo.add_argument("--end", default="2023-12-31")
    demo.add_argument("--factor", default="momentum_20")
    demo.add_argument("--top-n", type=int, default=20)
    demo.add_argument("--rebalance", default="ME", choices=["D", "W", "ME"])
    demo.add_argument("--weight", default="equal", choices=["equal", "market_cap"])
    demo.add_argument("--strict-history", action=argparse.BooleanOptionalAction, default=True)
    demo.add_argument("--output-dir", default="reports/output/mvp_demo")

    stage2 = sub.add_parser("stage2", help="阶段 2：反思迭代优化")
    stage2.add_argument("--provider", default="synthetic", choices=["auto", "akshare", "synthetic"])
    stage2.add_argument("--index", default="000300")
    stage2.add_argument("--universe-date", default="2019-12-31")
    stage2.add_argument("--start", default="2018-01-01")
    stage2.add_argument("--end", default="2023-12-31")
    stage2.add_argument("--factor", default="momentum_20")
    stage2.add_argument("--top-n", type=int, default=20)
    stage2.add_argument("--rebalance", default="ME", choices=["D", "W", "ME"])
    stage2.add_argument("--weight", default="equal", choices=["equal", "market_cap"])
    stage2.add_argument("--strict-history", action=argparse.BooleanOptionalAction, default=True)
    stage2.add_argument("--output-dir", default="reports/output/stage2")

    research = sub.add_parser("research", help="阶段 2：批量因子有效性检验")
    research.add_argument("--provider", default="synthetic", choices=["auto", "akshare", "synthetic"])
    research.add_argument("--index", default="000300")
    research.add_argument("--universe-date", default="2019-12-31")
    research.add_argument("--start", default="2018-01-01")
    research.add_argument("--end", default="2023-12-31")
    research.add_argument("--factor", default="momentum_20", help="因子库种子因子，仅用于数据准备")
    research.add_argument("--factors", default="", help="逗号分隔的待检验因子；留空则检验全部")
    research.add_argument("--quantiles", type=int, default=5)
    research.add_argument("--sample-ratio", type=float, default=0.7)
    research.add_argument("--strict-history", action=argparse.BooleanOptionalAction, default=True)
    research.add_argument("--output-dir", default="reports/output/factor_research")

    sub.add_parser("repl", help="命令式交互模式")

    web = sub.add_parser("web", help="启动股票组合风控 Web 工作台")
    web.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    web.add_argument("--port", type=int, default=8787, help="监听端口")

    args = parser.parse_args(argv)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "stage2":
        return cmd_stage2(args)
    if args.command == "research":
        return cmd_research(args)
    if args.command == "repl":
        return cmd_repl()
    if args.command == "web":
        return cmd_web(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
