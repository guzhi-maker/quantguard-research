"""Package a derived research export for course handoff.

The bundle contains coverage evidence, Parquet, DuckDB, the raw manifest and
the data documentation. Raw CSV files stay in the immutable snapshot and are
not duplicated here.

Usage:
    python tools/package_research_exports.py RAW-20261110-225213-E359DC
    python tools/package_research_exports.py RAW-20261110-225213-E359DC --compact
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
    compact: bool = False,
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
    inventory = """coverage.json                 覆盖度审计摘要与限制
coverage_by_symbol.csv        全部股票的历史长度、缺口和首末日期
coverage_by_year.csv          年度交易日与记录数
factors/factor_panel/         按年分区的 24 因子研究面板
factors/factors.duckdb        因子、财务、股本、分红和日线联合数据库
factors/factor_manifest.json  因子口径、覆盖与 SHA256
factors/factor_dictionary.csv 24 因子字典与使用建议
factors/*.parquet             财务、股本、分红、不复权价和指数
parquet/market_daily/         按年分区的日线 Parquet（zstd）
parquet/market_quotes.parquet 抓取时点最新行情
parquet/universe.parquet      抓取时点流动性股票池
quantguard.duckdb             可直接 SQL 查询的行情 DuckDB
query_examples.sql            示例查询
export_manifest.json          派生层文件清单与 SHA256
raw_manifest.json             原始快照的请求、质量、血缘与 SHA256
docs/                         数据集卡片与研究导出说明"""
    if compact:
        inventory = """coverage.json                 覆盖度审计摘要与限制
coverage_by_symbol.csv        全部股票的历史长度、缺口和首末日期
coverage_by_year.csv          年度交易日与记录数
factors/factors.duckdb        因子、财务、股本、分红和日线联合数据库
factors/factor_manifest.json  因子口径、覆盖与 SHA256
factors/factor_dictionary.csv 24 因子字典与使用建议
factors/*.parquet             财务、股本、分红、不复权价和指数
quantguard.duckdb             可直接 SQL 查询的行情 DuckDB
query_examples.sql            示例查询
export_manifest.json          派生层文件清单与 SHA256
raw_manifest.json             原始快照的请求、质量、血缘与 SHA256
docs/                         数据集卡片与研究导出说明"""
    parquet_note = (
        "精简版不包含因子面板和行情 Parquet 分区副本；对应数据可分别从"
        " `factors/factors.duckdb` 与 `quantguard.duckdb` 直接查询。"
        if compact
        else "Parquet 也可以直接被 pandas、Polars、PyArrow 或 DuckDB 读取。"
    )
    return f"""# QuantGuard 研究交接包

{'- 打包模式：精简版。为控制在课程传输限制内，省略与 DuckDB 重复的大体量 Parquet 分区；全部字段仍在 DuckDB 中。' if compact else '- 打包模式：完整版，同时包含 DuckDB 与按年 Parquet 副本。'}
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
{inventory}
```

## 使用

```powershell
python -m pip install duckdb pyarrow pandas
python -c "import duckdb; c=duckdb.connect('quantguard.duckdb', read_only=True); print(c.execute('select count(*) from market_daily').fetchone())"
```

{parquet_note}

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
    compact: bool = False,
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
    suffix = "_research_compact.zip" if compact else "_research.zip"
    target = target_dir / f"{run_id}{suffix}"
    temporary = target.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()

    files: list[tuple[Path, str]] = []
    rewritten: dict[str, str] = {}
    for path in sorted(source_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source_root)
            if compact and (
                relative.parts[0] == "parquet"
                or relative.as_posix().startswith("factors/factor_panel/")
            ):
                continue
            relative_text = relative.as_posix()
            if compact and relative_text == "export_manifest.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                parquet = payload.get("parquet") or {}
                parquet["available"] = False
                parquet["omitted_reason"] = "精简包使用 quantguard.duckdb，省略重复的 Parquet 分区"
                payload["parquet"] = parquet
                payload["bundle"] = {
                    "mode": "compact",
                    "omitted": [
                        "parquet/",
                        "factors/factor_panel/",
                    ],
                }
                rewritten[relative_text] = json.dumps(
                    payload, ensure_ascii=False, indent=2
                )
            elif compact and relative_text == "factors/factor_manifest.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                file_map = payload.get("files") or {}
                omitted = sorted(
                    key for key in file_map if key.startswith("factor_panel/")
                )
                payload["files"] = {
                    key: value
                    for key, value in file_map.items()
                    if key not in omitted
                }
                payload["bundle"] = {
                    "mode": "compact",
                    "omitted_files": omitted,
                    "note": "factor_panel 表仍保存在 factors.duckdb 中",
                }
                rewritten[relative_text] = json.dumps(
                    payload, ensure_ascii=False, indent=2
                )
            files.append((path, relative.as_posix()))
    files.append((raw_manifest, "raw_manifest.json"))
    for name in ("dataset-card.md", "research-exports.md", "factor-data-standard.md"):
        source = ROOT / "docs" / name
        if not source.is_file():
            raise FileNotFoundError(f"缺少交接文档: {source}")
        files.append((source, f"docs/{name}"))

    prefix = BUNDLE_ROOT.format(run_id=run_id)
    readme = _readme(run_id, exported, coverage, factor_manifest, compact=compact)
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
                if relative in rewritten:
                    archive.writestr(
                        f"{prefix}/{relative}",
                        rewritten[relative],
                        compress_type=zipfile.ZIP_DEFLATED,
                    )
                    continue
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
        "compact": compact,
        "coverage": coverage.get("summary") or {},
        "parquet_rows": exported["parquet"]["daily_rows"],
        "duckdb": exported["duckdb"].get("status"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 QuantGuard 研究交接 ZIP")
    parser.add_argument("run_id", help="原始快照编号")
    parser.add_argument("--derived-dir", default="", help="派生层目录，默认 data/derived")
    parser.add_argument("--exports-dir", default="", help="输出目录，默认 exports")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="省略与 DuckDB 重复的大体量 Parquet，生成小于完整版的研究包",
    )
    args = parser.parse_args(argv)
    result = build_research_bundle(
        args.run_id,
        derived_dir=Path(args.derived_dir) if args.derived_dir else None,
        exports_dir=Path(args.exports_dir) if args.exports_dir else None,
        compact=args.compact,
    )
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "path": result["path"],
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                "files": result["files"],
                "compact": result["compact"],
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
