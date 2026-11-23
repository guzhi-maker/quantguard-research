"""长历史采集器：腾讯 proxy 分页接口（每股 4 页拿满 2500 日）。

数据源现状（2026-11 实测）：
- 东方财富主站与分片：批量抓取后返回 RemoteDisconnected（IP 级限流）
- 腾讯主站 web.ifzq.gtimg.cn：批量抓取后返回 HTTP 501
- 腾讯 proxy.finance.qq.com：可用，单页 640 行，支持 end 参数向前翻页
- 新浪：单次最多 1023 行，深度不够
- baostock：可用但单只约 20 秒，且并发登录会被临时封禁

因此本脚本用腾讯 proxy 分页，4 页覆盖 2500 个交易日。输出与 RawRunStore 一致。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_agent.store import CHINA_TZ, RawRunStore, now_iso  # noqa: E402
from webapp.market_client import normalize_symbol  # noqa: E402

PAGE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
PAGE_ROWS = 640
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
_LOCK = threading.Lock()


def _tencent_code(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _page(code: str, end_date: str = "") -> list[list]:
    url = f"{PAGE_URL}?param={code},day,,{end_date},{PAGE_ROWS},qfq"
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            data = payload.get("data") or {}
            stock = data.get(code) or {}
            return stock.get("qfqday") or stock.get("day") or []
        except Exception:  # noqa: BLE001
            time.sleep(0.6 * (attempt + 1) + random.random() * 0.4)
    return []


def _fetch(symbol: str, days: int) -> list[dict]:
    code = _tencent_code(symbol)
    collected: dict[str, dict] = {}
    end_date = ""
    for _ in range(8):
        page = _page(code, end_date)
        if not page:
            break
        earliest = None
        for parts in page:
            if not isinstance(parts, list) or len(parts) < 6:
                continue
            try:
                date, open_price, close, high, low, volume = parts[:6]
                open_price, close, high, low = (
                    float(open_price),
                    float(close),
                    float(high),
                    float(low),
                )
                volume = float(volume)
            except (TypeError, ValueError):
                continue
            if close <= 0 or open_price <= 0:
                continue
            collected[date] = {
                "date": date,
                "open": open_price,
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": round(volume * 100 * close, 2),
                "turnover": 0.0,
            }
            if earliest is None or date < earliest:
                earliest = date
        if earliest is None or len(collected) >= days + 20:
            break
        try:
            end_date = (
                datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=1)
            ).strftime("%Y-%m-%d")
        except ValueError:
            break
        time.sleep(0.12 + random.random() * 0.12)

    if not collected:
        raise ValueError("没有可用日线")
    ordered = [collected[key] for key in sorted(collected)]
    previous = None
    for row in ordered:
        if previous and previous["close"] > 0:
            row["change"] = round(row["close"] - previous["close"], 4)
            row["change_pct"] = round(row["close"] / previous["close"] - 1.0, 8)
            row["amplitude"] = round((row["high"] - row["low"]) / previous["close"], 8)
        else:
            row["change"] = 0.0
            row["change_pct"] = 0.0
            row["amplitude"] = 0.0
        previous = row
    return ordered[-days:] if days > 0 else ordered


def load_universe(args) -> list[dict]:
    if args.universe_jsonl:
        path = Path(args.universe_jsonl)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.from_snapshot:
        import csv

        path = ROOT / "data" / "raw" / args.from_snapshot / "universe.csv"
        with path.open(encoding="utf-8-sig") as handle:
            return [
                {"symbol": row["symbol"], "name": row.get("name", ""), "industry": row.get("industry", "A股")}
                for row in csv.DictReader(handle)
            ]
    from webapp import market_client

    items = market_client.get_liquid_universe(args.universe_size)
    return [
        {"symbol": normalize_symbol(row["symbol"]), "name": row.get("name", ""), "industry": row.get("industry", "A股")}
        for row in items
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="长历史采集（腾讯 proxy 分页）")
    parser.add_argument("--universe-jsonl", default="")
    parser.add_argument("--from-snapshot", default="")
    parser.add_argument("--universe-size", type=int, default=1000)
    parser.add_argument("--days", type=int, default=2500)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--staging", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    store = RawRunStore()
    if args.staging:
        staging = Path(args.staging)
    else:
        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
        staging = store.raw_dir / "_staging" / f"LONG-{stamp}-{uuid.uuid4().hex[:6]}"
    staging.mkdir(parents=True, exist_ok=True)
    daily_path = staging / "daily.jsonl"
    universe_path = staging / "universe.jsonl"

    universe = load_universe(args)
    if args.limit:
        universe = universe[: args.limit]
    universe_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in universe), encoding="utf-8"
    )
    print(f"股票池 {len(universe)} 只，目标 {args.days} 个交易日，{args.workers} 线程", flush=True)

    meta = {normalize_symbol(str(row["symbol"])): row for row in universe}
    fetched_at = now_iso()
    failures: list[dict] = []
    done: list[str] = []
    started = time.time()
    processed = 0

    def task(symbol: str):
        return symbol, _fetch(symbol, args.days)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool, daily_path.open(
        "w", encoding="utf-8"
    ) as handle:
        pairs = [(pool.submit(task, symbol), symbol) for symbol in meta]
        for future, symbol in pairs:
            try:
                symbol, rows = future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append({"symbol": symbol, "stage": "history", "error": str(exc)[:300]})
                processed += 1
                continue
            item = meta[symbol]
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            **row,
                            "symbol": symbol,
                            "name": str(item.get("name") or symbol),
                            "industry": str(item.get("industry") or "A股"),
                            "source": "腾讯证券前复权日行情（成交额估算）",
                            "adjustment": "qfq",
                            "fetched_at": fetched_at,
                        },
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")
            done.append(symbol)
            processed += 1
            if processed % 50 == 0:
                elapsed = time.time() - started
                print(
                    f"进度 {processed}/{len(meta)}　失败 {len(failures)}　"
                    f"{processed / max(elapsed, 1e-9) * 60:.0f} 只/分钟",
                    flush=True,
                )

    print(
        f"抓取结束：成功 {len(done)}，失败 {len(failures)}，用时 {(time.time() - started) / 60:.1f} 分钟",
        flush=True,
    )
    if args.dry_run:
        print(f"dry-run 完成，staging: {staging}", flush=True)
        return 0

    daily_rows = [
        json.loads(line) for line in daily_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not daily_rows:
        print("没有成功数据", file=sys.stderr)
        return 1
    manifest = store.write_run(
        request={
            "universe_size": len(universe),
            "history_days": args.days,
            "include_quotes": False,
            "symbols": [normalize_symbol(str(row["symbol"])) for row in universe],
            "query": "长历史采集（腾讯 proxy 分页）",
            "source_policy": "tencent proxy 分页前复权",
        },
        daily_rows=daily_rows,
        quote_rows=[],
        universe_rows=universe,
        failures=failures,
        trace=[{"time": now_iso(), "event": "long_collect", "message": f"staging={staging.name}"}],
        agent={"provider": "cli-long-tencent", "staging": str(staging)},
    )
    summary = manifest["summary"]
    print("\n采集完成")
    print(f"  快照编号: {manifest['run_id']}")
    print(f"  股票数量: {summary['symbols']}")
    print(f"  证券-日行数: {summary['daily_rows']}")
    print(f"  日期范围: {summary['date_min']} ~ {summary['date_max']}")
    print(f"  失败记录: {summary['failures']}")
    print(f"  manifest: {manifest['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
