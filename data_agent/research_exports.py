"""Build reproducible research exports without modifying immutable raw snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import uuid
from array import array
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from config.settings import DATA_DIR, PROJECT_ROOT
from data_agent.store import CHINA_TZ, RawRunStore

COVERAGE_VERSION = 1
EXPORT_VERSION = 1
DAILY_FLOAT_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "amplitude",
    "change_pct",
    "change",
    "turnover",
}
DAILY_TEXT_COLUMNS = {
    "symbol",
    "name",
    "industry",
    "source",
    "adjustment",
    "fetched_at",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[int | float]) -> dict:
    numbers = sorted(float(value) for value in values)
    if not numbers:
        return {
            "count": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(numbers),
        "min": numbers[0],
        "p10": _quantile(numbers, 0.10),
        "p25": _quantile(numbers, 0.25),
        "median": _quantile(numbers, 0.50),
        "p75": _quantile(numbers, 0.75),
        "p90": _quantile(numbers, 0.90),
        "max": numbers[-1],
        "mean": sum(numbers) / len(numbers),
    }


def _coverage_bucket(row_count: int) -> str:
    if row_count < 250:
        return "0-249"
    if row_count < 500:
        return "250-499"
    if row_count < 750:
        return "500-749"
    if row_count < 1000:
        return "750-999"
    return "1000+"


def _missing_ranges(
    positions: list[int],
    dates: list[str],
    limit: int = 5,
) -> list[str]:
    ranges = []
    for left, right in zip(positions, positions[1:]):
        if right <= left + 1:
            continue
        start = dates[left + 1]
        end = dates[right - 1]
        ranges.append(start if start == end else f"{start}~{end}")
        if len(ranges) >= limit:
            break
    return ranges


def build_coverage_report(
    daily_path: Path,
    manifest: dict,
    output_dir: Path,
) -> dict:
    """Stream the daily CSV twice and produce symbol/year coverage evidence."""
    dates: set[str] = set()
    symbol_meta: dict[str, dict] = {}
    symbol_rows: Counter[str] = Counter()
    session_rows: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    adjustments: Counter[str] = Counter()

    with daily_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            date = str(row.get("date") or "")
            symbol = str(row.get("symbol") or "")
            if not date or not symbol:
                continue
            dates.add(date)
            session_rows[date] += 1
            symbol_rows[symbol] += 1
            if symbol not in symbol_meta:
                symbol_meta[symbol] = {
                    "name": str(row.get("name") or symbol),
                    "industry": str(row.get("industry") or "A股"),
                }
            source = str(row.get("source") or "unknown")
            adjustment = str(row.get("adjustment") or "unknown")
            sources[source] += 1
            adjustments[adjustment] += 1

    ordered_dates = sorted(dates)
    date_index = {date: index for index, date in enumerate(ordered_dates)}
    positions: dict[str, array] = defaultdict(lambda: array("I"))

    with daily_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            date = str(row.get("date") or "")
            symbol = str(row.get("symbol") or "")
            index = date_index.get(date)
            if index is not None and symbol:
                positions[symbol].append(index)

    symbol_coverage: list[dict] = []
    for symbol in sorted(symbol_rows):
        ordered_positions = sorted(set(positions[symbol]))
        if not ordered_positions:
            continue
        first_position = ordered_positions[0]
        last_position = ordered_positions[-1]
        expected_sessions = last_position - first_position + 1
        missing_sessions = max(0, expected_sessions - len(ordered_positions))
        max_gap = max(
            (
                right - left - 1
                for left, right in zip(ordered_positions, ordered_positions[1:])
            ),
            default=0,
        )
        first_date = ordered_dates[first_position]
        last_date = ordered_dates[last_position]
        rows = int(symbol_rows[symbol])
        symbol_coverage.append(
            {
                "symbol": symbol,
                **symbol_meta.get(symbol, {"name": symbol, "industry": "A股"}),
                "rows": rows,
                "first_date": first_date,
                "last_date": last_date,
                "start_year": first_date[:4],
                "expected_sessions": expected_sessions,
                "missing_sessions": missing_sessions,
                "max_gap_sessions": max_gap,
                "lifetime_completeness": round(
                    len(ordered_positions) / max(expected_sessions, 1),
                    6,
                ),
                "coverage_bucket": _coverage_bucket(rows),
                "missing_ranges": _missing_ranges(ordered_positions, ordered_dates),
            }
        )

    year_sessions: dict[str, set[str]] = defaultdict(set)
    year_rows: Counter[str] = Counter()
    for date, rows in session_rows.items():
        year_sessions[date[:4]].add(date)
        year_rows[date[:4]] += rows
    year_report = [
        {
            "year": year,
            "trading_sessions": len(year_sessions[year]),
            "daily_rows": int(year_rows[year]),
        }
        for year in sorted(year_sessions)
    ]

    rows_per_symbol = [item["rows"] for item in symbol_coverage]
    lifetime_expected = sum(item["expected_sessions"] for item in symbol_coverage)
    lifetime_observed = sum(item["rows"] for item in symbol_coverage)
    missing_symbols = [item for item in symbol_coverage if item["missing_sessions"] > 0]
    shortest = sorted(symbol_coverage, key=lambda item: (item["rows"], item["symbol"]))
    largest_gaps = sorted(
        symbol_coverage,
        key=lambda item: (
            -item["missing_sessions"],
            -item["max_gap_sessions"],
            item["symbol"],
        ),
    )
    requested_days = int((manifest.get("request") or {}).get("history_days") or 0)
    coverage_buckets = Counter(item["coverage_bucket"] for item in symbol_coverage)
    start_years = Counter(item["start_year"] for item in symbol_coverage)

    report = {
        "schema_version": COVERAGE_VERSION,
        "run_id": manifest.get("run_id"),
        "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "method": (
            "按快照内全部证券-日并集构造会话索引；仅在每只股票自身首末日期之间"
            "统计缺失会话。缺失会话可能来自停牌或接口缺行，不等同于网络采集失败。"
        ),
        "summary": {
            "symbols": len(symbol_coverage),
            "daily_rows": lifetime_observed,
            "trading_sessions": len(ordered_dates),
            "date_min": ordered_dates[0] if ordered_dates else None,
            "date_max": ordered_dates[-1] if ordered_dates else None,
            "requested_history_days": requested_days,
            "rows_per_symbol": _distribution(rows_per_symbol),
            "lifetime_completeness": round(
                lifetime_observed / max(lifetime_expected, 1),
                6,
            ),
            "symbols_without_internal_gaps": len(symbol_coverage) - len(missing_symbols),
            "symbols_with_internal_gaps": len(missing_symbols),
            "symbols_meeting_requested_history": sum(
                1 for value in rows_per_symbol if requested_days and value >= requested_days
            ),
            "symbols_below_950_rows": sum(
                1 for value in rows_per_symbol if value < 950
            ),
            "symbols_below_500_rows": sum(
                1 for value in rows_per_symbol if value < 500
            ),
            "internal_missing_sessions": sum(
                item["missing_sessions"] for item in symbol_coverage
            ),
        },
        "coverage_buckets": [
            {"bucket": bucket, "symbols": coverage_buckets.get(bucket, 0)}
            for bucket in ("0-249", "250-499", "500-749", "750-999", "1000+")
        ],
        "start_year": [
            {"year": year, "symbols": count}
            for year, count in sorted(start_years.items())
        ],
        "by_year": year_report,
        "sources": dict(sorted(sources.items())),
        "adjustments": dict(sorted(adjustments.items())),
        "shortest_history": shortest[:12],
        "largest_internal_gaps": largest_gaps[:12],
        "limitations": [
            "股票池是快照抓取时点的流动性样本，不是历史时点成分股，存在幸存者偏差。",
            "内部缺失会话只能提示可能停牌或缺行，不能替代交易所停牌状态数据。",
            "历史价格全部为抓取时点的前复权口径，未来分红送股可能改变历史复权价格。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = output_dir / "coverage.json"
    _json_write(coverage_path, report)

    per_symbol_path = output_dir / "coverage_by_symbol.csv"
    per_symbol_fields = [
        "symbol",
        "name",
        "industry",
        "rows",
        "first_date",
        "last_date",
        "start_year",
        "expected_sessions",
        "missing_sessions",
        "max_gap_sessions",
        "lifetime_completeness",
        "coverage_bucket",
        "missing_ranges",
    ]
    with per_symbol_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=per_symbol_fields)
        writer.writeheader()
        for item in symbol_coverage:
            writer.writerow(
                {
                    **item,
                    "missing_ranges": ";".join(item["missing_ranges"]),
                }
            )

    by_year_path = output_dir / "coverage_by_year.csv"
    with by_year_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["year", "trading_sessions", "daily_rows"],
        )
        writer.writeheader()
        writer.writerows(year_report)

    report["files"] = {
        "coverage": {
            "path": coverage_path.name,
            "rows": 1,
            "bytes": coverage_path.stat().st_size,
            "sha256": _sha256(coverage_path),
        },
        "coverage_by_symbol": {
            "path": per_symbol_path.name,
            "rows": len(symbol_coverage),
            "bytes": per_symbol_path.stat().st_size,
            "sha256": _sha256(per_symbol_path),
        },
        "coverage_by_year": {
            "path": by_year_path.name,
            "rows": len(year_report),
            "bytes": by_year_path.stat().st_size,
            "sha256": _sha256(by_year_path),
        },
    }
    _json_write(coverage_path, report)
    return report


def _daily_convert_options() -> pacsv.ConvertOptions:
    column_types: dict[str, pa.DataType] = {
        "date": pa.date32(),
        **{column: pa.string() for column in DAILY_TEXT_COLUMNS},
        **{column: pa.float64() for column in DAILY_FLOAT_COLUMNS},
    }
    return pacsv.ConvertOptions(column_types=column_types)


def _write_daily_parquet(daily_path: Path, root: Path) -> tuple[list[dict], int]:
    files: list[dict] = []
    writers: dict[str, pq.ParquetWriter] = {}
    rows_by_year: Counter[str] = Counter()
    reader = pacsv.open_csv(
        daily_path,
        read_options=pacsv.ReadOptions(block_size=16 * 1024 * 1024),
        convert_options=_daily_convert_options(),
    )
    try:
        for batch in reader:
            table = pa.Table.from_batches([batch])
            years = pc.year(table["date"])
            for year in sorted(set(years.to_pylist())):
                partition = table.filter(pc.equal(years, year))
                year_text = str(year)
                if year_text not in writers:
                    target = root / f"year={year_text}" / "part-00000.parquet"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    writers[year_text] = pq.ParquetWriter(
                        target,
                        partition.schema,
                        compression="zstd",
                    )
                writers[year_text].write_table(partition)
                rows_by_year[year_text] += partition.num_rows
    finally:
        for writer in writers.values():
            writer.close()

    for year, row_count in sorted(rows_by_year.items()):
        path = root / f"year={year}" / "part-00000.parquet"
        files.append(
            {
                "year": year,
                "path": path.relative_to(root).as_posix(),
                "rows": row_count,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files, sum(rows_by_year.values())


def _write_flat_parquet(
    source: Path,
    target: Path,
    column_types: dict[str, pa.DataType] | None = None,
) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    options = pacsv.ConvertOptions(column_types=column_types) if column_types else None
    table = pacsv.read_csv(source, convert_options=options)
    pq.write_table(table, target, compression="zstd")
    return {
        "path": target.name,
        "rows": table.num_rows,
        "columns": table.column_names,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _write_duckdb(parquet_dir: Path, target: Path, run_id: str) -> dict:
    try:
        import duckdb
    except ImportError:
        return {
            "status": "unavailable",
            "reason": "未安装 duckdb；Parquet 仍可直接供 pandas、Polars、PyArrow 或 DuckDB 使用。",
        }

    daily_glob = str((parquet_dir / "market_daily" / "year=*" / "*.parquet").resolve())
    quotes_path = str((parquet_dir / "market_quotes.parquet").resolve())
    universe_path = str((parquet_dir / "universe.parquet").resolve())
    connection = duckdb.connect(str(target))
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE market_daily AS "
            "SELECT * FROM read_parquet(?, hive_partitioning = true)",
            [daily_glob],
        )
        connection.execute(
            "CREATE OR REPLACE TABLE market_quotes AS "
            "SELECT * FROM read_parquet(?)",
            [quotes_path],
        )
        connection.execute(
            "CREATE OR REPLACE TABLE universe AS SELECT * FROM read_parquet(?)",
            [universe_path],
        )
        connection.execute(
            "CREATE OR REPLACE TABLE coverage_by_symbol AS "
            "SELECT * FROM read_csv_auto(?, header = true)",
            [str((parquet_dir.parent / "coverage_by_symbol.csv").resolve())],
        )
        connection.execute(
            "CREATE OR REPLACE TABLE research_metadata "
            "(dataset VARCHAR, run_id VARCHAR, generated_at VARCHAR)"
        )
        connection.execute(
            "INSERT INTO research_metadata VALUES (?, ?, ?)",
            [
                "QuantGuard research export",
                run_id,
                datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
            ],
        )
    finally:
        connection.close()
    return {
        "status": "ready",
        "path": target.name,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "tables": [
            "market_daily",
            "market_quotes",
            "universe",
            "coverage_by_symbol",
            "research_metadata",
        ],
    }


def build_research_exports(
    run_id: str,
    store: RawRunStore | None = None,
    derived_dir: Path | None = None,
    rebuild: bool = False,
) -> dict:
    """Build coverage, partitioned Parquet, DuckDB, and a derived manifest."""
    store = store or RawRunStore()
    manifest = store.load_run(run_id)
    output_root = Path(derived_dir or DATA_DIR / "derived")
    target = output_root / run_id
    manifest_path = target / "export_manifest.json"
    if manifest_path.exists() and not rebuild:
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{run_id}-{uuid.uuid4().hex[:8]}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        coverage = build_coverage_report(
            store.file_for_view(run_id, "daily"),
            manifest,
            staging,
        )
        parquet_root = staging / "parquet"
        daily_files, daily_rows = _write_daily_parquet(
            store.file_for_view(run_id, "daily"),
            parquet_root / "market_daily",
        )
        quotes = _write_flat_parquet(
            store.file_for_view(run_id, "quotes"),
            parquet_root / "market_quotes.parquet",
        )
        universe = _write_flat_parquet(
            store.file_for_view(run_id, "universe"),
            parquet_root / "universe.parquet",
        )
        duckdb_info = _write_duckdb(
            parquet_root,
            staging / "quantguard.duckdb",
            run_id,
        )
        query_examples = staging / "query_examples.sql"
        query_examples.write_text(
            "SELECT date, symbol, close, volume\n"
            "FROM market_daily\n"
            "WHERE symbol = '600519'\n"
            "ORDER BY date DESC\n"
            "LIMIT 20;\n\n"
            "SELECT symbol, rows, first_date, last_date, missing_sessions\n"
            "FROM coverage_by_symbol\n"
            "ORDER BY missing_sessions DESC, rows ASC\n"
            "LIMIT 50;\n",
            encoding="utf-8",
        )

        source_files = {
            key: {
                "rows": (manifest.get("files") or {}).get(key, {}).get("rows"),
                "sha256": (manifest.get("files") or {}).get(key, {}).get("sha256"),
            }
            for key in ("daily", "quotes", "universe")
        }
        payload = {
            "schema_version": EXPORT_VERSION,
            "run_id": run_id,
            "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
            "raw_snapshot": source_files,
            "coverage": {"summary": coverage["summary"], **coverage["files"]},
            "parquet": {
                "daily_rows": daily_rows,
                "market_daily": {
                    "partitioning": "year",
                    "compression": "zstd",
                    "files": daily_files,
                },
                "market_quotes": quotes,
                "universe": universe,
            },
            "duckdb": duckdb_info,
            "query_examples": {
                "path": query_examples.name,
                "bytes": query_examples.stat().st_size,
                "sha256": _sha256(query_examples),
            },
            "commands": {
                "build": f"python tools/build_research_exports.py {run_id}",
                "verify_raw": f"python tools/verify_snapshot.py {run_id}",
            },
            "limitations": coverage["limitations"],
        }
        _json_write(staging / "export_manifest.json", payload)
        if target.exists():
            if not rebuild:
                raise FileExistsError(f"研究导出目录已存在: {target}")
            previous = output_root / f".{run_id}-previous-{uuid.uuid4().hex[:8]}"
            target.rename(previous)
            try:
                staging.rename(target)
            finally:
                shutil.rmtree(previous, ignore_errors=True)
        else:
            staging.rename(target)
        return payload
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_research_exports(
    run_id: str,
    derived_dir: Path | None = None,
) -> dict:
    path = Path(derived_dir or DATA_DIR / "derived") / run_id / "export_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到研究导出: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_coverage(
    run_id: str,
    derived_dir: Path | None = None,
) -> dict:
    path = Path(derived_dir or DATA_DIR / "derived") / run_id / "coverage.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到覆盖度审计: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def derived_file(
    run_id: str,
    relative_path: str,
    derived_dir: Path | None = None,
) -> Path:
    root = (Path(derived_dir or DATA_DIR / "derived") / run_id).resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise ValueError("研究导出路径越界")
    if not path.is_file():
        raise FileNotFoundError(f"研究导出文件不存在: {relative_path}")
    return path
