"""Append-only raw snapshot storage and manifest generation."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from config.settings import DATA_DIR, PROJECT_ROOT


CHINA_TZ = timezone(timedelta(hours=8))
RUN_ID_RE = re.compile(r"^RAW-[0-9]{8}-[0-9]{6}-[A-F0-9]{6}$")

DAILY_FIELDS = [
    "date",
    "symbol",
    "name",
    "industry",
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
    "source",
    "adjustment",
    "fetched_at",
]

QUOTE_FIELDS = [
    "symbol",
    "name",
    "industry",
    "price",
    "open",
    "high",
    "low",
    "prev_close",
    "change",
    "change_pct",
    "turnover_rate",
    "pe",
    "pb",
    "volume",
    "amount",
    "market_cap",
    "float_market_cap",
    "source",
    "updated_at",
    "fetched_at",
]

UNIVERSE_FIELDS = [
    "symbol",
    "name",
    "industry",
    "price",
    "change_pct",
    "volume",
    "amount",
    "turnover_rate",
    "pe",
    "market_cap",
    "source",
    "fetched_at",
]

VIEWS = {
    "daily": "market_daily.csv",
    "quotes": "market_quotes.csv",
    "universe": "universe.csv",
}


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def _json_default(value: Any):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"无法序列化 {type(value).__name__}")


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    temp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Iterable[dict], fields: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
            count += 1
    return count


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _quality_summary(daily_rows: list[dict], quote_rows: list[dict]) -> dict:
    keys: set[tuple[str, str]] = set()
    duplicates = 0
    invalid_prices = 0
    missing_close = 0
    missing_amount = 0
    missing_volume = 0
    dates = []
    symbols = set()
    sources: dict[str, int] = {}
    adjustments: dict[str, int] = {}
    for row in daily_rows:
        symbol = str(row.get("symbol") or "")
        day = str(row.get("date") or "")
        key = (day, symbol)
        if key in keys:
            duplicates += 1
        else:
            keys.add(key)
        if day:
            dates.append(day)
        if symbol:
            symbols.add(symbol)
        close = row.get("close")
        if not _finite_number(close) or float(close) <= 0:
            invalid_prices += 1
        if close in (None, ""):
            missing_close += 1
        if row.get("amount") in (None, ""):
            missing_amount += 1
        if row.get("volume") in (None, ""):
            missing_volume += 1
        source = str(row.get("source") or "unknown")
        sources[source] = sources.get(source, 0) + 1
        adjustment = str(row.get("adjustment") or "unknown")
        adjustments[adjustment] = adjustments.get(adjustment, 0) + 1

    quote_symbols = {str(row.get("symbol")) for row in quote_rows if row.get("symbol")}
    return {
        "symbol_count": len(symbols),
        "quote_symbol_count": len(quote_symbols),
        "duplicate_keys": duplicates,
        "invalid_price_rows": invalid_prices,
        "missing_close": missing_close,
        "missing_amount": missing_amount,
        "missing_volume": missing_volume,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "rows_by_source": sources,
        "rows_by_adjustment": adjustments,
        "symbols_missing_quotes": sorted(symbols - quote_symbols),
    }


class RawRunStore:
    """Persist immutable raw snapshots under data/raw and manifests under data/manifests."""

    def __init__(
        self,
        root: Path | None = None,
        raw_dir: Path | None = None,
        manifest_dir: Path | None = None,
    ):
        self.root = Path(root or PROJECT_ROOT)
        self.raw_dir = Path(raw_dir or DATA_DIR / "raw")
        self.manifest_dir = Path(manifest_dir or DATA_DIR / "manifests")

    def _new_run_id(self) -> str:
        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
        return f"RAW-{stamp}-{uuid.uuid4().hex[:6].upper()}"

    def write_run(
        self,
        request: dict,
        daily_rows: list[dict],
        quote_rows: list[dict],
        universe_rows: list[dict],
        failures: list[dict],
        trace: list[dict],
        agent: dict | None = None,
    ) -> dict:
        run_id = self._new_run_id()
        run_dir = self.raw_dir / run_id
        while run_dir.exists():
            run_id = self._new_run_id()
            run_dir = self.raw_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)

        payloads = {
            "daily": (DAILY_FIELDS, daily_rows),
            "quotes": (QUOTE_FIELDS, quote_rows),
            "universe": (UNIVERSE_FIELDS, universe_rows),
        }
        files: dict[str, dict] = {}
        for view, (fields, rows) in payloads.items():
            path = run_dir / VIEWS[view]
            row_count = _write_csv(path, rows, fields)
            files[view] = {
                "path": path.relative_to(self.root).as_posix(),
                "filename": path.name,
                "rows": row_count,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "columns": fields,
            }

        quality = _quality_summary(daily_rows, quote_rows)
        sources = sorted(quality["rows_by_source"])
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": now_iso(),
            "status": "complete" if daily_rows else "failed",
            "request": request,
            "agent": agent or {"provider": None, "enabled": False},
            "summary": {
                "symbols": quality["symbol_count"],
                "requested_symbols": len(request.get("symbols") or []),
                "daily_rows": len(daily_rows),
                "quote_rows": len(quote_rows),
                "failures": len(failures),
                "date_min": quality["date_min"],
                "date_max": quality["date_max"],
                "sources": sources,
            },
            "quality": quality,
            "failures": failures,
            "files": files,
            "lineage": [
                {
                    "source": source,
                    "rows": rows,
                    "adjustment": sorted(
                        {
                            str(row.get("adjustment") or "unknown")
                            for row in daily_rows
                            if str(row.get("source") or "unknown") == source
                        }
                    ),
                }
                for source, rows in quality["rows_by_source"].items()
            ],
            "limitations": [
                "股票池来自抓取时点的公开流动性快照，不是历史时点成分股。",
                "公开接口可能延迟、限流或临时调整字段，失败记录保留在 failures 中。",
                "腾讯和新浪的成交额可能由成交量与收盘价估算，具体以每行 source 为准。",
                "原始数据未做后续研究清洗，缺失值、停牌和异常值需由数据治理环节复核。",
            ],
            "trace": trace,
        }
        manifest_path = self.manifest_dir / f"{run_id}.json"
        _atomic_json(manifest_path, manifest)
        manifest["manifest_path"] = manifest_path.relative_to(self.root).as_posix()
        return manifest

    def list_runs(self, limit: int = 30) -> list[dict]:
        if not self.manifest_dir.exists():
            return []
        manifests = []
        for path in self.manifest_dir.glob("RAW-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            manifests.append(payload)
        manifests.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return manifests[: max(1, min(int(limit), 100))]

    def list_run_summaries(self, limit: int = 30) -> list[dict]:
        """Return recent manifests without large trace blocks."""
        summaries = []
        for manifest in self.list_runs(limit):
            request = deepcopy(manifest.get("request") or {})
            request.pop("symbols", None)
            summaries.append(
                {
                    key: deepcopy(manifest.get(key))
                    for key in (
                        "schema_version",
                        "run_id",
                        "created_at",
                        "status",
                        "request",
                        "agent",
                        "summary",
                        "quality",
                        "failures",
                        "files",
                        "lineage",
                        "limitations",
                    )
                }
            )
            summaries[-1]["request"] = request
            manifest_path = self.manifest_dir / f"{manifest.get('run_id')}.json"
            summaries[-1]["manifest_path"] = manifest_path.relative_to(
                self.root
            ).as_posix()
        return summaries

    def load_run(self, run_id: str) -> dict:
        if not RUN_ID_RE.fullmatch(str(run_id)):
            raise ValueError("采集编号格式无效")
        path = self.manifest_dir / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到采集记录 {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_agent(self, run_id: str, agent: dict) -> dict:
        manifest = self.load_run(run_id)
        manifest["agent"] = agent
        path = self.manifest_dir / f"{run_id}.json"
        _atomic_json(path, manifest)
        manifest["manifest_path"] = path.relative_to(self.root).as_posix()
        return manifest

    def file_for_view(self, run_id: str, view: str) -> Path:
        manifest = self.load_run(run_id)
        if view not in VIEWS:
            raise ValueError("数据视图只能是 daily、quotes 或 universe")
        info = manifest.get("files", {}).get(view)
        if not info:
            raise FileNotFoundError(f"采集记录缺少 {view} 文件")
        path = (self.root / info["path"]).resolve()
        raw_root = self.raw_dir.resolve()
        if raw_root not in path.parents:
            raise ValueError("采集文件路径越界")
        if not path.exists():
            raise FileNotFoundError(f"采集文件不存在: {path.name}")
        return path

    def read_rows(
        self,
        run_id: str,
        view: str,
        offset: int = 0,
        limit: int = 50,
        query: str = "",
    ) -> dict:
        path = self.file_for_view(run_id, view)
        needle = str(query or "").strip().lower()
        matched: list[dict] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if needle and not any(needle in str(value).lower() for value in row.values()):
                    continue
                matched.append(row)
        start = max(0, int(offset))
        page_size = max(1, min(int(limit), 500))
        return {
            "run_id": run_id,
            "view": view,
            "total": len(matched),
            "offset": start,
            "limit": page_size,
            "rows": matched[start : start + page_size],
        }

    def all_rows(self, run_id: str, view: str) -> list[dict]:
        """Read an entire raw view for deterministic local analysis."""
        path = self.file_for_view(run_id, view)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
