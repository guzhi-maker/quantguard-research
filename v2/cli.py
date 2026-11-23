"""QuantGuard v2 研究流水线命令行。

用法：
  python v2/cli.py run --risk 5.5 --horizon 2
  python v2/cli.py run --holdings "600519:0.15,300750:0.10" --risk 7
  python v2/cli.py ladder --horizon 2
  python v2/cli.py grid --horizons 1,2,3 --risks 1,5.5,10

只读取 data/raw 下的原始快照，不修改采集数据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from research import pipeline as pipeline_mod
from research import preferences as preferences_mod
from research import validation as validation_mod


def _parse_holdings(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    holdings: dict[str, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            symbol, weight = chunk.split(":", 1)
        elif "=" in chunk:
            symbol, weight = chunk.split("=", 1)
        else:
            raise SystemExit(f"持仓格式应为 代码:权重，收到 {chunk}")
        holdings[str(symbol).strip().zfill(6)] = float(weight)
    total = sum(holdings.values())
    if total > 1.0001:
        raise SystemExit(f"持仓权重合计 {total:.2%} 超过 100%")
    return holdings


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def cmd_run(args: argparse.Namespace) -> int:
    holdings = _parse_holdings(args.holdings)
    result = pipeline_mod.run_research(
        run_id=args.run_id,
        risk=args.risk,
        horizon=args.horizon,
        holdings=holdings,
        capital=args.capital,
        with_report=args.report,
    )
    payload = result.as_dict()

    print(f"快照 {payload['run_id']}　截止 {payload['as_of']}　偏好 {payload['preference_label']}")
    print(f"模型 {payload['model']['version']}　训练 {payload['model']['train_rows']} 行")
    metrics = payload["metrics"]
    coverage = metrics["coverage"]
    print(
        "分位覆盖率 P10 {:.1%} / P90 {:.1%}　RankIC {}　Top 超额 {}　元模型 AUC {}".format(
            coverage.get("p10_breach") or 0,
            coverage.get("p90_breach") or 0,
            _fmt(metrics["rank_ic"].get("ic_mean"), 3),
            _fmt(metrics["top_bucket"].get("top_excess"), 4),
            _fmt(metrics["meta"].get("auc"), 3),
        )
    )
    print(f"有效独立样本 {metrics['effective_sample']}（{payload['preference']['horizon']['days']} 日标签）")
    print(f"验证方式 {metrics.get('validation_mode')}　抽样折 {len(metrics.get('folds', []))}")

    print("\n风险阶梯：")
    for row in payload["ladder"]:
        print(
            "  {:<4} CVaR {:>7} 预期 {:>8} 波动 {:>7} 仓位 {:>5} 持仓 {:>3} 现金 {:>6}".format(
                row["label"],
                _fmt(row["cvar95"], 3),
                _fmt(row["expected_return"], 3),
                _fmt(row["volatility"], 3),
                _fmt(row["gross_exposure"], 2),
                row["positions"],
                _fmt(row["cash_weight"], 2),
            )
        )
    mono = payload["monotonic"]
    print(f"  CVaR 单调：{mono['monotonic']}　区间 {mono['cvar'][0]} → {mono['cvar'][-1]}")

    selected = payload["selected"]
    print(f"\n选定档位 {selected['label']}：{len(selected['weights'])} 只，现金 {selected['cash_weight']:.1%}")
    top = sorted(selected["weights"].items(), key=lambda item: item[1], reverse=True)[:5]
    for symbol, weight in top:
        lots = selected["lots"].get(symbol, 0)
        print(f"  {symbol} 权重 {weight:.1%}　{lots} 手")

    if holdings:
        diagnosis = payload["diagnosis"]
        print(f"\n持仓诊断：{diagnosis['headline']}")
        for item in diagnosis["items"]:
            if item["status"] != "green":
                print(f"  [{item['status_label']}] {item['label']}：{item['evidence']}")
        advice = payload["advice"]
        print(
            f"建议：安全 {advice['safety_count']} 条，优化 {advice['optimization_count']} 条，"
            f"折叠 {advice['folded_count']} 条（预算 {advice['budget']}）"
        )
        for action in advice["actions"][:8]:
            print(f"  {action['symbol']} {action['action']} Δ{action['delta']:+.1%}（{action['kind']}）")
        if not advice["post_check"].get("feasible", True):
            print(f"  交易包触发约束：{advice['post_check']}")

    print("\n验收：")
    for key, value in payload["acceptance"].items():
        print(f"  {key}: {value}")
    if payload.get("warnings"):
        print("\n提示：")
        for item in payload["warnings"]:
            print(f"  - {item}")

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n结果已写入 {target}")
    return 0


def cmd_ladder(args: argparse.Namespace) -> int:
    result = pipeline_mod.run_research(
        run_id=args.run_id,
        risk=args.risk,
        horizon=args.horizon,
        positions=args.positions or None,
    )
    payload = result.as_dict()
    print(f"快照 {payload['run_id']}　{payload['preference']['horizon']['days']} 日标签")
    for row in payload["ladder"]:
        print(json.dumps(row, ensure_ascii=False))
    print("CVaR 单调：", payload["monotonic"])
    return 0


def cmd_grid(args: argparse.Namespace) -> int:
    horizons = [int(value) for value in args.horizons.split(",")]
    risks = [float(value) for value in args.risks.split(",")]
    rows = []
    for horizon in horizons:
        for risk in risks:
            try:
                result = pipeline_mod.run_research(
                    run_id=args.run_id, risk=risk, horizon=horizon
                ).as_dict()
            except Exception as exc:  # noqa: BLE001
                rows.append({"horizon": horizon, "risk": risk, "error": str(exc)})
                continue
            metrics = result["metrics"]
            rows.append(
                {
                    "horizon": horizon,
                    "risk": risk,
                    "ic_mean": metrics["rank_ic"].get("ic_mean"),
                    "top_excess": metrics["top_bucket"].get("top_excess"),
                    "p10_breach": metrics["coverage"].get("p10_breach"),
                    "meta_auc": metrics["meta"].get("auc"),
                    "cvar_monotonic": result["monotonic"]["monotonic"],
                    "cvar_range": [result["monotonic"]["cvar"][0], result["monotonic"]["cvar"][-1]],
                    "acceptance": result["acceptance"],
                }
            )
            print(json.dumps(rows[-1], ensure_ascii=False))

    valid = [row for row in rows if "error" not in row and row.get("ic_mean") is not None]
    if valid:
        best = max(valid, key=lambda row: row.get("ic_mean") or -9)
        trials = max(1, len(rows))
        observations = max(3, len(valid))
        dsr = validation_mod.deflated_sharpe(
            sharpe=float(best.get("ic_mean") or 0.0) * 3.0,
            observations=observations,
            trials=trials,
        )
        print("\n最优配置：", json.dumps(best, ensure_ascii=False))
        print("DSR（按真实尝试次数惩罚）：", json.dumps(dsr, ensure_ascii=False))
        print("提示：DSR 的夏普由 IC 粗略换算，仅用于横向比较，不能当作策略夏普。")
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"网格结果已写入 {target}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    import pandas as pd

    from research import backtest as backtest_mod
    from research import scoring as scoring_mod

    inputs = pipeline_mod.build_inputs(
        run_id=args.run_id,
        horizon=args.horizon,
        variant=pipeline_mod.make_variant(
            seeds=args.seeds, feature_mode=args.feature_mode or "auto"
        ),
    )
    panels = inputs["panels"]
    cache_path = Path(args.score_cache) if args.score_cache else None
    if cache_path and cache_path.exists():
        scores = pd.read_csv(cache_path, dtype={"symbol": str})
        scores["date"] = pd.to_datetime(scores["date"])
        print(f"复用分数缓存 {cache_path}")
    else:
        scores = scoring_mod.rolling_scores(
            inputs["dataset"],
            inputs["representatives"],
            refit_every=args.refit_every,
            seeds=args.seeds,
            train_window=args.train_window or None,
            predict_period="M",
        )
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            scores.to_csv(cache_path, index=False)
            print(f"分数缓存写入 {cache_path}")
    coverage = scoring_mod.score_coverage(scores)
    print(f"打分面板：{coverage}")
    if scores.empty:
        print("没有可用分数，无法回测")
        return 1

    risks = [float(value) for value in args.risks.split(",")]
    modes = tuple(value.strip() for value in args.modes.split(","))
    result = backtest_mod.run_ladder_backtest(
        scores,
        panels.close,
        risks,
        args.horizon,
        tradable=inputs["tradable"],
        industries=inputs["industry"],
        modes=modes,
    )
    table = result["table"]
    columns = [
        column
        for column in (
            "mode",
            "risk",
            "tier",
            "annual_return",
            "annual_volatility",
            "sharpe",
            "max_drawdown",
            "benchmark_annual_return",
            "excess_annual_return",
            "annual_turnover",
            "cost_drag_annual",
            "rebalances",
            "avg_positions",
        )
        if column in table.columns
    ]
    print(table[columns].to_string(index=False))

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "horizon": args.horizon,
            "coverage": coverage,
            "table": table.to_dict(orient="records"),
            "curves": {
                name: {
                    str(pd.Timestamp(index).date()): round(float(value), 6)
                    for index, value in series.items()
                }
                for name, series in result["curves"].items()
            },
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"\n回测结果已写入 {target}")
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    from research import experiments as experiments_mod

    include = [name.strip() for name in args.variants.split(",")] if args.variants else None
    result = experiments_mod.run_variants(
        run_id=args.run_id,
        horizon=args.horizon,
        risk=args.risk,
        include=include,
    )
    print(experiments_mod.format_table(result))
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "horizon": result["horizon"],
            "risk": result["risk"],
            "run_id": result["run_id"],
            "table": result["table"].to_dict(orient="records"),
            "selected": result["selected"],
            "audit": result["audit"],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n实验记录已写入 {target}")
    return 0


def cmd_preferences(args: argparse.Namespace) -> int:
    for item in preferences_mod.risk_tier_table():
        print(json.dumps(item, ensure_ascii=False))
    for item in preferences_mod.horizon_options():
        print(json.dumps(item, ensure_ascii=False))
    print("推荐档位示例（能接受单月亏 12%）：", json.dumps(preferences_mod.suggest_tier(0.12), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QuantGuard v2 研究流水线")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="跑一次完整研究")
    run.add_argument("--run-id", default=None)
    run.add_argument("--risk", type=float, default=5.5, help="风险偏好 1-10")
    run.add_argument("--horizon", type=int, default=2, choices=[1, 2, 3])
    run.add_argument("--positions", type=int, default=0, help="覆盖档位默认持仓数，0 表示按档位")
    run.add_argument("--holdings", default=None, help="持仓，形如 600519:0.15,300750:0.10")
    run.add_argument("--capital", type=float, default=1_000_000.0)
    run.add_argument("--report", action="store_true", help="调用 AI 生成简报（未配置密钥时回退模板）")
    run.add_argument("--out", default=None)

    ladder = sub.add_parser("ladder", help="只输出五档风险阶梯")
    ladder.add_argument("--run-id", default=None)
    ladder.add_argument("--risk", type=float, default=5.5)
    ladder.add_argument("--horizon", type=int, default=2, choices=[1, 2, 3])
    ladder.add_argument("--positions", type=int, default=0, help="覆盖档位默认持仓数，0 表示按档位")

    grid = sub.add_parser("grid", help="期限 × 风险网格与 DSR 审计")
    grid.add_argument("--run-id", default=None)
    grid.add_argument("--horizons", default="1,2,3")
    grid.add_argument("--risks", default="1,5.5,10")
    grid.add_argument("--out", default=None)

    experiment = sub.add_parser("experiment", help="变体对比与 DSR/PBO 审计")
    experiment.add_argument("--run-id", default=None)
    experiment.add_argument("--horizon", type=int, default=2, choices=[1, 2, 3])
    experiment.add_argument("--risk", type=float, default=5.5)
    experiment.add_argument("--variants", default=None, help="逗号分隔的变体名，留空表示全部")
    experiment.add_argument("--out", default=None)

    backtest = sub.add_parser("backtest", help="组合层事后回测（净值为准）")
    backtest.add_argument("--run-id", default=None)
    backtest.add_argument("--horizon", type=int, default=2, choices=[1, 2, 3])
    backtest.add_argument("--risks", default="1,5.5,10")
    backtest.add_argument("--modes", default="optimize,rank")
    backtest.add_argument("--seeds", type=int, default=1)
    backtest.add_argument("--refit-every", type=int, default=20)
    backtest.add_argument("--train-window", type=int, default=0, help="滚动训练窗口（交易日），0 表示用全历史")
    backtest.add_argument("--score-cache", default="", help="分数面板缓存路径，存在则直接复用")
    backtest.add_argument("--feature-mode", default=None)
    backtest.add_argument("--out", default=None)

    sub.add_parser("preferences", help="打印偏好参数表")

    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "ladder":
        return cmd_ladder(args)
    if args.command == "grid":
        return cmd_grid(args)
    if args.command == "experiment":
        return cmd_experiment(args)
    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "preferences":
        return cmd_preferences(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
