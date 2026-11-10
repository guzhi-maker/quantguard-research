"""Collect a reproducible research snapshot from the command line.

Example:
    python data_agent/collect_dataset.py --universe-size 3000 --history-days 250

Rows are appended to a staging folder as each symbol finishes. If the network
drops, rerun with --staging <folder> to resume. A final immutable snapshot
with CSV files, hashes and a quality manifest is written under data/raw/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_agent.service import MarketGateway  # noqa: E402
from data_agent.store import CHINA_TZ, RawRunStore, now_iso  # noqa: E402
from webapp import market_client  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QuantGuard 研究数据池采集器：抓取公开 A 股行情并生成可追溯快照。"
    )
    parser.add_argument(
        "--universe-size", type=int, default=3000,
        help=(
            f"股票池数量（10-{market_client.MAX_UNIVERSE_SIZE}），默认 3000"
        ),
    )
    parser.add_argument(
        "--history-days", type=int, default=250,
        help="每只股票的历史交易日数（30-500），默认 250",
    )
    parser.add_argument(
        "--symbols", type=str, default="",
        help="可选：指定股票代码，逗号分隔",
    )
    parser.add_argument(
        "--no-quotes", action="store_true",
        help="只采集历史日线，不抓实时行情快照",
    )
    parser.add_argument(
        "--staging", type=str, default="",
        help="续采已有的 staging 目录",
    )
    return parser


def _retry(call, attempts: int = 3, delay: float = 0.8):
    last_error = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - public endpoints fail in many ways
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay * (attempt + 1))
    raise last_error  # type: ignore[misc]


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_state(path: Path, state: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = RawRunStore()
    gateway = MarketGateway()

    if args.staging:
        staging = Path(args.staging)
    else:
        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
        staging = store.raw_dir / "_staging" / f"STAGE-{stamp}-{uuid.uuid4().hex[:6]}"
    staging.mkdir(parents=True, exist_ok=True)

    state_path = staging / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"done": [], "failures": [], "started_at": now_iso()}
    universe_path = staging / "universe.jsonl"
    daily_path = staging / "daily.jsonl"
    quotes_path = staging / "quotes.jsonl"

    if not universe_path.exists():
        print(
            f"[   0%] universe: 正在获取股票池（最多 {args.universe_size} 只）",
            flush=True,
        )
        codes = [
            part.strip()
            for part in args.symbols.replace("，", ",").split(",")
            if part.strip()
        ]
        universe_rows = []
        if codes:
            for symbol in codes:
                code = market_client.normalize_symbol(symbol)
                try:
                    matches = _retry(lambda code=code: gateway.search(code, 1))
                except Exception:
                    matches = []
                match = next(
                    (row for row in matches if str(row.get("symbol")) == code), {}
                )
                universe_rows.append(
                    {
                        "symbol": code,
                        "name": str(match.get("name") or code),
                        "industry": str(match.get("industry") or "A股"),
                        "source": "证券搜索元数据",
                        "fetched_at": now_iso(),
                    }
                )
        else:
            items = _retry(
                lambda: market_client.get_liquid_universe(args.universe_size)
            )
            universe_rows = [
                {
                    **row,
                    "symbol": market_client.normalize_symbol(row["symbol"]),
                    "fetched_at": now_iso(),
                }
                for row in items
            ]
        _append_jsonl(universe_path, universe_rows)

    universe_rows = _read_jsonl(universe_path)
    if len(universe_rows) < 10:
        print("股票池不足 10 只，终止采集。", file=sys.stderr)
        return 1
    symbols = [str(row["symbol"]) for row in universe_rows]
    metadata = {str(row["symbol"]): row for row in universe_rows}
    done = set(state.get("done") or [])
    failures: list[dict] = list(state.get("failures") or [])
    last_percent = -1
    fetched_at = now_iso()
    total = len(symbols)

    for index, symbol in enumerate(symbols, start=1):
        percent = int((index - 1) / max(total, 1) * 100)
        if symbol in done:
            if percent >= last_percent + 10:
                last_percent = percent
                print(
                    f"[{percent:3d}%] resume: 已完成 {len(done)} / {total}",
                    flush=True,
                )
            continue
        print(f"[{percent:3d}%] fetch: {index}/{total} {symbol}", flush=True)
        quote = None
        if not args.no_quotes:
            try:
                quote = _retry(lambda symbol=symbol: gateway.quote(symbol))
                _append_jsonl(quotes_path, [{**quote, "fetched_at": fetched_at}])
            except Exception as exc:
                failures.append(
                    {"symbol": symbol, "stage": "quote", "error": str(exc)}
                )
        try:
            history = _retry(
                lambda symbol=symbol: gateway.history(symbol, args.history_days)
            )
        except Exception as exc:
            failures.append(
                {"symbol": symbol, "stage": "history", "error": str(exc)}
            )
            state["failures"] = failures
            _write_state(state_path, state)
            continue
        meta = metadata.get(symbol, {})
        rows = []
        for row in history:
            source = str(row.get("source") or "unknown")
            rows.append(
                {
                    **row,
                    "symbol": symbol,
                    "name": str(
                        (quote or {}).get("name") or meta.get("name") or symbol
                    ),
                    "industry": str(
                        (quote or {}).get("industry")
                        or meta.get("industry")
                        or "A股"
                    ),
                    "adjustment": "qfq" if "前复权" in source else "unverified",
                    "fetched_at": fetched_at,
                }
            )
        _append_jsonl(daily_path, rows)
        done.add(symbol)
        state["done"] = sorted(done)
        state["failures"] = failures
        _write_state(state_path, state)

    daily_rows = _read_jsonl(daily_path)
    quote_rows = _read_jsonl(quotes_path)
    if not daily_rows:
        print("没有成功抓取任何日线数据，staging 已保留。", file=sys.stderr)
        print(f"staging: {staging}", file=sys.stderr)
        return 1

    manifest = store.write_run(
        request={
            "universe_size": len(symbols),
            "history_days": args.history_days,
            "include_quotes": not args.no_quotes,
            "symbols": symbols,
            "query": "命令行研究数据池采集",
            "source_policy": "auto: 东方财富优先，腾讯与新浪按接口回退",
        },
        daily_rows=daily_rows,
        quote_rows=quote_rows,
        universe_rows=universe_rows,
        failures=failures,
        trace=[
            {
                "time": now_iso(),
                "event": "cli_collect",
                "message": f"命令行采集完成，staging={staging.name}",
            }
        ],
        agent={"provider": "cli-fixed", "staging": str(staging)},
    )
    (staging / "complete.json").write_text(
        json.dumps({"run_id": manifest["run_id"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = manifest["summary"]
    quality = manifest["quality"]
    print("\n采集完成")
    print(f"  快照编号: {manifest['run_id']}")
    print(f"  股票数量: {summary['symbols']}")
    print(f"  证券-日行数: {summary['daily_rows']}")
    print(f"  实时行情行数: {summary['quote_rows']}")
    print(f"  日期范围: {summary['date_min']} ~ {summary['date_max']}")
    print(f"  失败记录: {summary['failures']}")
    print(f"  重复键: {quality['duplicate_keys']}")
    print(f"  非法价格行: {quality['invalid_price_rows']}")
    print(f"  manifest: {manifest['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
