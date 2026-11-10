"""Build coverage, Parquet, and DuckDB research exports for a raw snapshot.

Usage:
    python tools/build_research_exports.py RAW-20261110-225213-E359DC
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_agent.research_exports import build_research_exports  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 QuantGuard 研究数据派生层")
    parser.add_argument("run_id", help="原始快照编号")
    parser.add_argument(
        "--derived-dir",
        default="",
        help="可选：派生层输出目录，默认 data/derived",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="重建已有派生层；原始快照不会修改",
    )
    args = parser.parse_args(argv)
    payload = build_research_exports(
        args.run_id,
        derived_dir=Path(args.derived_dir) if args.derived_dir else None,
        rebuild=args.rebuild,
    )
    print(
        json.dumps(
            {
                "run_id": payload["run_id"],
                "coverage": payload["coverage"]["summary"],
                "parquet_rows": payload["parquet"]["daily_rows"],
                "parquet_files": len(payload["parquet"]["market_daily"]["files"]),
                "duckdb": payload["duckdb"],
                "manifest": f"data/derived/{payload['run_id']}/export_manifest.json",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
