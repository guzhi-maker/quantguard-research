"""Verify a saved market-data snapshot against its manifest.

Usage:
    python tools/verify_snapshot.py RAW-20261110-124549-780A2D
    python tools/verify_snapshot.py data/manifests/RAW-20261110-124549-780A2D.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "data" / "manifests"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_manifest(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    run_id = candidate.name
    path = MANIFEST_DIR / f"{run_id}.json"
    if path.is_file():
        return path
    raise FileNotFoundError(f"找不到 manifest: {value}")


def read_csv_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def as_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def verify_manifest(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("status", manifest.get("status") == "complete", str(manifest.get("status")))
    files = manifest.get("files") or {}
    datasets = {}
    for key in ("daily", "quotes", "universe"):
        meta = files.get(key) or {}
        relative = meta.get("path")
        path = ROOT / relative if relative else None
        exists = bool(path and path.is_file())
        check(f"{key}_exists", exists, str(relative or "missing"))
        if not exists:
            continue
        actual_hash = sha256(path)
        check(
            f"{key}_sha256",
            actual_hash == meta.get("sha256"),
            f"{actual_hash} vs {meta.get('sha256')}",
        )
        columns, rows = read_csv_rows(path)
        datasets[key] = {"columns": columns, "rows": rows, "path": path}
        check(
            f"{key}_row_count",
            len(rows) == int(meta.get("rows") or -1),
            f"{len(rows)} vs {meta.get('rows')}",
        )

    daily = datasets.get("daily")
    if daily:
        rows = daily["rows"]
        keys = [(row.get("date"), row.get("symbol")) for row in rows]
        duplicates = sum(count - 1 for count in Counter(keys).values() if count > 1)
        invalid_prices = 0
        date_values = []
        symbols = set()
        for row in rows:
            date_values.append(str(row.get("date") or ""))
            symbols.add(str(row.get("symbol") or ""))
            prices = [
                as_number(row.get("open")),
                as_number(row.get("high")),
                as_number(row.get("low")),
                as_number(row.get("close")),
            ]
            high = prices[1]
            low = prices[2]
            if any(value is None or value <= 0 for value in prices):
                invalid_prices += 1
            elif high is not None and low is not None and high < low:
                invalid_prices += 1
        summary = manifest.get("summary") or {}
        quality = manifest.get("quality") or {}
        actual_dates = [value for value in date_values if value]
        check(
            "daily_symbols",
            len(symbols) == int(summary.get("symbols") or -1),
            f"{len(symbols)} vs {summary.get('symbols')}",
        )
        check(
            "duplicate_keys",
            duplicates == int(quality.get("duplicate_keys") or 0),
            f"actual={duplicates}, manifest={quality.get('duplicate_keys')}",
        )
        check(
            "invalid_prices",
            invalid_prices == int(quality.get("invalid_price_rows") or 0),
            f"actual={invalid_prices}, manifest={quality.get('invalid_price_rows')}",
        )
        check(
            "date_range",
            (min(actual_dates), max(actual_dates))
            == (summary.get("date_min"), summary.get("date_max")),
            f"{min(actual_dates)}..{max(actual_dates)} vs "
            f"{summary.get('date_min')}..{summary.get('date_max')}",
        )
        check(
            "at_least_1000_symbols",
            len(symbols) >= 1000,
            f"{len(symbols)} symbols",
        )

    failures = manifest.get("failures") or []
    check(
        "failure_count",
        len(failures) == int((manifest.get("summary") or {}).get("failures") or 0),
        f"{len(failures)} records",
    )
    return {
        "manifest": str(manifest_path),
        "run_id": manifest.get("run_id"),
        "summary": manifest.get("summary"),
        "quality": manifest.get("quality"),
        "failures": failures,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核验 QuantGuard 原始数据快照")
    parser.add_argument("manifest", help="run_id 或 manifest 文件路径")
    args = parser.parse_args(argv)
    report = verify_manifest(resolve_manifest(args.manifest))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
