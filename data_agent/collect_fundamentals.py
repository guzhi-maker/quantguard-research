"""Collect fundamental data for an existing raw universe.

Example:
    python data_agent/collect_fundamentals.py \
      --from-run RAW-20261110-225213-E359DC --workers 8

The collector writes a separate immutable ``FUND-*`` snapshot under
``data/fundamentals/``. It never modifies the original daily snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_agent.fundamentals import (  # noqa: E402
    collect_fundamentals,
    resolve_source_run,
)
from data_agent.store import RawRunStore  # noqa: E402
from webapp.market_client import normalize_symbol  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QuantGuard 基本面、分红、股本与不复权价格采集器"
    )
    parser.add_argument(
        "--from-run",
        default="",
        help="原始行情快照编号；省略时使用最新快照",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="可选：逗号分隔股票代码，默认使用快照股票池",
    )
    parser.add_argument("--limit", type=int, default=0, help="可选：只采集前 N 只")
    parser.add_argument("--workers", type=int, default=8, help="并发请求数，默认 8")
    parser.add_argument(
        "--price-workers",
        type=int,
        default=2,
        help="不复权日线并发上限，默认 2", 
    )
    parser.add_argument("--start-date", default="2019-01-01", help="财报起始报告期")
    parser.add_argument("--history-days", type=int, default=1200, help="不复权价格窗口")
    parser.add_argument(
        "--skip-prices",
        action="store_true",
        help="不采集不复权日线；此时无法计算历史总市值",
    )
    parser.add_argument(
        "--staging",
        default="",
        help="可选：续采已有的基本面 staging 目录",
    )
    return parser


def _symbols_from_manifest(store: RawRunStore, run_id: str) -> list[str]:
    manifest = store.load_run(run_id)
    return [
        normalize_symbol(symbol)
        for symbol in (manifest.get("request") or {}).get("symbols") or []
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = RawRunStore()
    source = resolve_source_run(store, args.from_run)
    run_id = str(source["run_id"])
    if args.symbols.strip():
        symbols = [
            normalize_symbol(part.strip())
            for part in args.symbols.replace("，", ",").split(",")
            if part.strip()
        ]
    else:
        symbols = _symbols_from_manifest(store, run_id)
    if args.limit:
        symbols = symbols[: max(1, int(args.limit))]
    if not symbols:
        print("没有可采集的股票。", file=sys.stderr)
        return 1

    def progress(item: dict) -> None:
        if item["done"] == item["total"] or item["done"] % 10 == 0:
            print(
                f"[fundamentals] {item['done']}/{item['total']} "
                f"{item['symbol']} failures={item['failures']}",
                flush=True,
            )

    manifest = collect_fundamentals(
        symbols,
        source_run_id=run_id,
        workers=args.workers,
        price_workers=args.price_workers,
        start_date=args.start_date,
        history_days=args.history_days,
        include_prices=not args.skip_prices,
        staging=Path(args.staging) if args.staging else None,
        progress=progress,
    )
    print(
        json.dumps(
            {
                "fund_id": manifest["fund_id"],
                "source_run_id": manifest["source_run_id"],
                "summary": manifest["summary"],
                "quality": manifest["quality"],
                "manifest": f"data/fundamentals/{manifest['fund_id']}/manifest.json",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
