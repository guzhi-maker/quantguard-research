"""Build the multi-factor research panel for one raw snapshot.

Usage:
    python tools/build_factor_panel.py RAW-20261110-225213-E359DC
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_agent.factor_panel import build_factor_panel  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 QuantGuard 多因子研究面板")
    parser.add_argument("run_id", help="原始行情快照编号")
    parser.add_argument("--fund-id", default="", help="可选：指定基本面快照编号")
    parser.add_argument("--derived-dir", default="", help="可选：派生层目录")
    parser.add_argument("--symbols", default="", help="可选：只生成指定股票，逗号分隔")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="重建已有因子面板；原始快照不会修改",
    )
    args = parser.parse_args(argv)
    symbols = [
        part.strip()
        for part in args.symbols.replace("，", ",").split(",")
        if part.strip()
    ]
    result = build_factor_panel(
        args.run_id,
        fund_id=args.fund_id,
        derived_dir=Path(args.derived_dir) if args.derived_dir else None,
        rebuild=args.rebuild,
        symbols=symbols or None,
    )
    print(
        json.dumps(
            {
                "raw_run_id": result["raw_run_id"],
                "fund_id": result["fund_id"],
                "summary": result["summary"],
                "by_year": result["by_year"],
                "duckdb": result.get("duckdb"),
                "manifest": str(
                    Path(result.get("manifest_path", ""))
                    .resolve()
                    .relative_to(ROOT)
                    .as_posix()
                )
                if result.get("manifest_path")
                else "",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
