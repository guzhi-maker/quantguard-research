"""Package a derived research export for course handoff.

The bundle contains coverage evidence, Parquet, DuckDB, the raw manifest and
the data documentation. Raw CSV files stay in the immutable snapshot and are
not duplicated here.

Usage:
    python tools/package_research_exports.py RAW-20261110-225213-E359DC
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_agent.research_exports import load_research_exports  # noqa: E402
from data_agent.factor_panel import load_factor_manifest  # noqa: E402
from data_agent.store import CHINA_TZ, RawRunStore  # noqa: E402

BUNDLE_ROOT = "RESEARCH-{run_id}"
TEXT_SUFFIXES = {".json", ".csv", ".md", ".sql", ".txt"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readme(
    run_id: str,
    manifest: dict,
    coverage: dict,
    factor_manifest: dict | None = None,
) -> str:
    summary = coverage.get("summary") or {}
    parquet = (manifest.get("parquet") or {}).get("daily_rows")
    duckdb = (manifest.get("duckdb") or {}).get("status")
    factor_summary = (factor_manifest or {}).get("summary") or {}
    factor_note = ""
    factor_quality_note = ""
    if factor_manifest:
        factor_note = (
            f"- 多因子面板：{factor_summary.get('symbols', 0):,} 只股票、"
            f"{factor_summary.get('rows', 0):,} 行；24 个正式因子可用率 "
            f"{factor_summary.get('factor_ready_ratio', 0) * 100:.1f}%\n"
        )
        quality = factor_manifest.get("quality") or {}
        sources = (
            (factor_manifest.get("source_summary") or {})
            .get("unadjusted_price_rows_by_source")
            or {}
        )
        source_text = " / ".join(
            f"{name} {int(rows):,} 行" for name, rows in sources.items()
        )
        factor_quality_note = (
            "- 因子质量门控：剔除 "
            f"{quality.get('financial_rows_without_ann_date_excluded', 0)} 条无公告日财务记录；"
            f"金融模板规则为“{quality.get('financial_template_rule', '未记录')}”；"
            f"不复权价来源 {source_text or '未记录'}\n"
        )
    return f"""# QuantGuard 研究交接包

- 快照编号：`{run_id}`
- 覆盖区间：`{summary.get("date_min")}` 至 `{summary.get("date_max")}`
- 股票数：{summary.get("symbols"):,}
- 证券-日记录：{summary.get("daily_rows"):,}
- 快照交易日：{summary.get("trading_sessions"):,}
- 每股记录中位数：{summary.get("rows_per_symbol", {}).get("median")}
- 生命周期完整度：{summary.get("lifetime_completeness")}
- Parquet 行数：{parquet:,}（DuckDB 状态：{duckdb}）
{factor_note}
{factor_quality_note}

## 目录

```text
coverage.json                 覆盖度审计摘要与限制
coverage_by_symbol.csv        全部股票的历史长度、缺口和首末日期
coverage_by_year.csv          年度交易日与记录数
factors/factor_panel/         按年分区的 24 因子研究面板
factors/factors.duckdb        因子、财务、股本、分红和日线联合数据库
factors/factor_manifest.json  因子口径、覆盖与 SHA256
factors/factor_dictionary.csv 24 因子字典与使用建议
parquet/market_daily/         按年分区的日线 Parquet（zstd）
parquet/market_quotes.parquet 抓取时点最新行情
parquet/universe.parquet      抓取时点流动性股票池
quantguard.duckdb             可直接 SQL 查询的 DuckDB 数据库
query_examples.sql            示例查询
export_manifest.json          派生层文件清单与 SHA256
raw_manifest.json             原始快照的请求、质量、血缘与 SHA256
docs/                         数据集卡片与研究导出说明
```

## 使用

```powershell
python -m pip install duckdb pyarrow pandas
python -c "import duckdb; c=duckdb.connect('quantguard.duckdb', read_only=True); print(c.execute('select count(*) from market_daily').fetchone())"
```

Parquet 也可以直接被 pandas、Polars、PyArrow 或 DuckDB 的
`read_parquet(..., hive_partitioning = true)` 读取。

## 边界

- `missing_sessions` 只表示股票首末记录之间的会话缺口，可能来自停牌或接口缺行。
- 股票池是快照时点的流动性样本，不是历史时点成分股，存在幸存者偏差。
- 价格口径为抓取时点前复权，后续公司行动可能改变历史复权价格。
- 原始 CSV 不在此包内，仍以原始快照、manifest 和 SHA256 为唯一证据。
"""


def build_research_bundle(
    run_id: str,
    store: RawRunStore | None = None,
    derived_dir: Path | None = None,
    exports_dir: Path | None = None,
) -> dict:
    """Create a deterministic handoff ZIP and a SHA256 sidecar."""
    store = store or RawRunStore()
    exported = load_research_exports(run_id, derived_dir=derived_dir)
    coverage_path = (
        Path(derived_dir or ROOT / "data" / "derived") / run_id / "coverage.json"
    )
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    try:
        factor_manifest = load_factor_manifest(run_id, derived_dir=derived_dir)
    except FileNotFoundError:
        factor_manifest = None
    source_root = Path(derived_dir or ROOT / "data" / "derived") / run_id
    raw_manifest = store.manifest_dir / f"{run_id}.json"
    if not raw_manifest.is_file():
        raise FileNotFoundError(f"未找到原始 manifest: {raw_manifest}")

    target_dir = Path(exports_dir or ROOT / "exports")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{run_id}_research.zip"
    temporary = target.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()

    files: list[tuple[Path, str]] = []
    for path in sorted(source_root.rglob("*")):
        if path.is_file():
            files.append((path, path.relative_to(source_root).as_posix()))
    files.append((raw_manifest, "raw_manifest.json"))
    for name in ("dataset-card.md", "research-exports.md", "factor-data-standard.md"):
        source = ROOT / "docs" / name
        if not source.is_file():
            raise FileNotFoundError(f"缺少交接文档: {source}")
        files.append((source, f"docs/{name}"))

    prefix = BUNDLE_ROOT.format(run_id=run_id)
    readme = _readme(run_id, exported, coverage, factor_manifest)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr(f"{prefix}/README.md", readme)
            for source, relative in files:
                method = (
                    zipfile.ZIP_DEFLATED
                    if source.suffix.lower() in TEXT_SUFFIXES
                    else zipfile.ZIP_STORED
                )
                archive.write(source, f"{prefix}/{relative}", compress_type=method)
        temporary.replace(target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    digest = _sha256(target)
    sidecar = target.with_suffix(".zip.sha256")
    sidecar.write_text(
        f"{digest}  {target.name}\n",
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "path": target.as_posix(),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "files": len(files) + 1,
        "coverage": coverage.get("summary") or {},
        "parquet_rows": exported["parquet"]["daily_rows"],
        "duckdb": exported["duckdb"].get("status"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 QuantGuard 研究交接 ZIP")
    parser.add_argument("run_id", help="原始快照编号")
    parser.add_argument("--derived-dir", default="", help="派生层目录，默认 data/derived")
    parser.add_argument("--exports-dir", default="", help="输出目录，默认 exports")
    args = parser.parse_args(argv)
    result = build_research_bundle(
        args.run_id,
        derived_dir=Path(args.derived_dir) if args.derived_dir else None,
        exports_dir=Path(args.exports_dir) if args.exports_dir else None,
    )
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "path": result["path"],
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                "files": result["files"],
                "parquet_rows": result["parquet_rows"],
                "duckdb": result["duckdb"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
