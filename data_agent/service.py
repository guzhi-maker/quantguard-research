"""Collection orchestration and asynchronous jobs."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from data_agent.catalog import get_source, get_source_catalog
from config.settings import DATA_DIR
from data_agent.store import CHINA_TZ, RawRunStore, now_iso
from webapp import market_client

MAX_UNIVERSE_SIZE = market_client.MAX_UNIVERSE_SIZE
MAX_HISTORY_DAYS = market_client.MAX_HISTORY_DAYS


def _elapsed_ms(started: float) -> int:
    import time

    return int((time.monotonic() - started) * 1000)


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否"}


def _symbols(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [
            part.strip()
            for part in value.replace("，", ",").replace(" ", ",").split(",")
            if part.strip()
        ]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise ValueError("symbols 必须是股票代码列表或逗号分隔字符串")
    result = []
    seen = set()
    for part in parts:
        symbol = market_client.normalize_symbol(part)
        if symbol not in seen:
            result.append(symbol)
            seen.add(symbol)
    if len(result) > MAX_UNIVERSE_SIZE:
        raise ValueError(f"单次采集最多支持 {MAX_UNIVERSE_SIZE} 只股票")
    return result


@dataclass
class CollectionRequest:
    universe_size: int = 80
    history_days: int = 1000
    include_quotes: bool = True
    symbols: list[str] = field(default_factory=list)
    query: str = ""

    @classmethod
    def from_payload(cls, payload: dict | None) -> "CollectionRequest":
        data = payload or {}
        try:
            universe_size = int(data.get("universe_size", 80))
            history_days = int(data.get("history_days", 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError("股票池数量和历史天数必须是整数") from exc
        if not 10 <= universe_size <= MAX_UNIVERSE_SIZE:
            raise ValueError(
                f"股票池数量必须在 10 到 {MAX_UNIVERSE_SIZE} 之间"
            )
        if not 30 <= history_days <= MAX_HISTORY_DAYS:
            raise ValueError(f"历史天数必须在 30 到 {MAX_HISTORY_DAYS} 之间")
        query = str(data.get("query") or "").strip()
        if len(query) > 500:
            raise ValueError("任务描述不能超过 500 个字符")
        return cls(
            universe_size=universe_size,
            history_days=history_days,
            include_quotes=_as_bool(data.get("include_quotes"), True),
            symbols=_symbols(data.get("symbols")),
            query=query,
        )

    def to_dict(self) -> dict:
        return {
            "universe_size": self.universe_size,
            "history_days": self.history_days,
            "include_quotes": self.include_quotes,
            "symbols": list(self.symbols),
            "query": self.query,
        }


class MarketGateway:
    """Small injectable boundary around live public market connectors."""

    def search(self, keyword: str, limit: int = 8) -> list[dict]:
        return market_client.search_stocks(keyword, limit=limit)

    def universe(self, limit: int) -> list[dict]:
        return market_client.get_liquid_universe(limit)

    def quote(self, symbol: str) -> dict:
        return market_client.get_quote(symbol)

    def history(self, symbol: str, days: int) -> list[dict]:
        return market_client.get_history(symbol, days=days)

    def probe(self, source_id: str) -> dict:
        source = get_source(source_id)
        started = __import__("time").monotonic()
        try:
            if source_id == "eastmoney":
                data = market_client._get_eastmoney_quote("600519")
                sample = {
                    "symbol": data.get("symbol"),
                    "name": data.get("name"),
                    "price": data.get("price"),
                }
            elif source_id == "tencent":
                data = market_client._get_tencent_quote("600519")
                sample = {
                    "symbol": data.get("symbol"),
                    "name": data.get("name"),
                    "price": data.get("price"),
                }
            elif source_id == "sina":
                data = market_client._get_sina_history("600519", 30)
                sample = {
                    "rows": len(data),
                    "first_date": data[0].get("date") if data else None,
                    "last_date": data[-1].get("date") if data else None,
                }
            else:  # pragma: no cover - catalog is closed
                raise ValueError(f"未知数据源: {source_id}")
        except Exception as exc:
            return {
                "id": source_id,
                "name": source["name"],
                "status": "error",
                "latency_ms": _elapsed_ms(started),
                "error": str(exc),
            }
        return {
            "id": source_id,
            "name": source["name"],
            "status": "ok",
            "latency_ms": _elapsed_ms(started),
            "sample": sample,
        }


class CollectorService:
    def __init__(
        self,
        store: RawRunStore | None = None,
        gateway: MarketGateway | None = None,
        agent_factory: Optional[Callable[["CollectorService"], Any]] = None,
        derived_dir: Path | None = None,
    ):
        self.store = store or RawRunStore()
        self.gateway = gateway or MarketGateway()
        self.derived_dir = Path(derived_dir or DATA_DIR / "derived")
        self.agent_factory = agent_factory
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()

    def catalog(self) -> list[dict]:
        return get_source_catalog()

    def probe_sources(self, source_ids: list[str] | None = None) -> list[dict]:
        selected = source_ids or [item["id"] for item in get_source_catalog()]
        results = []
        for source_id in selected:
            results.append(self.gateway.probe(str(source_id)))
        return results

    def search(self, keyword: str, limit: int = 8) -> list[dict]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []
        return self.gateway.search(keyword, max(1, min(int(limit), 20)))

    def _collect(
        self,
        request: CollectionRequest,
        emit: Callable[..., None],
        agent: dict | None = None,
    ) -> dict:
        trace: list[dict] = []

        def record(event: str, message: str, **extra: Any) -> None:
            item = {"time": now_iso(), "event": event, "message": message, **extra}
            trace.append(item)
            emit(event, message, **extra)

        requested = list(request.symbols)
        universe_rows: list[dict] = []
        metadata: dict[str, dict] = {}
        record("universe", "开始确定股票池")
        if requested:
            items = []
            for symbol in requested:
                try:
                    matches = self.gateway.search(symbol, 1)
                    match = next(
                        (row for row in matches if str(row.get("symbol")) == symbol),
                        {},
                    )
                except Exception:
                    match = {}
                metadata[symbol] = {
                    "symbol": symbol,
                    "name": str(match.get("name") or symbol),
                    "industry": str(match.get("industry") or "A股"),
                    "source": "证券搜索元数据",
                }
            record("universe", f"使用指定股票池: {len(requested)} 只")
        else:
            items = self.gateway.universe(request.universe_size)
            if len(items) < 10:
                raise ValueError("公开行情返回的可用股票数量不足")
            for item in items:
                symbol = market_client.normalize_symbol(str(item.get("symbol")))
                metadata[symbol] = dict(item)
            requested = list(metadata)
            record(
                "universe",
                f"获取当前高流动性股票池: {len(requested)} 只",
                sources=sorted({str(item.get("source")) for item in items}),
            )

        for item in metadata.values():
            universe_rows.append(
                {
                    **item,
                    "symbol": str(item.get("symbol")),
                    "name": str(item.get("name") or item.get("symbol")),
                    "industry": str(item.get("industry") or "A股"),
                    "fetched_at": now_iso(),
                }
            )

        total = len(requested)
        daily_rows: list[dict] = []
        quote_rows: list[dict] = []
        failures: list[dict] = []
        fetched_at = now_iso()
        for index, symbol in enumerate(requested, start=1):
            meta = metadata.get(symbol, {})
            record(
                "fetch",
                f"抓取 {symbol} 历史行情",
                current=index - 1,
                total=total,
                percent=round((index - 1) / max(total, 1) * 100),
            )
            quote = None
            if request.include_quotes:
                try:
                    quote = self.gateway.quote(symbol)
                    quote_rows.append({**quote, "fetched_at": fetched_at})
                except Exception as exc:
                    failures.append(
                        {
                            "symbol": symbol,
                            "stage": "quote",
                            "error": str(exc),
                        }
                    )
            try:
                history = self.gateway.history(symbol, request.history_days)
            except Exception as exc:
                failures.append(
                    {"symbol": symbol, "stage": "history", "error": str(exc)}
                )
                record(
                    "error",
                    f"{symbol} 历史行情抓取失败",
                    current=index,
                    total=total,
                    percent=round(index / max(total, 1) * 100),
                    error=str(exc),
                )
                continue
            for row in history:
                source = str(row.get("source") or "unknown")
                adjustment = "qfq" if "前复权" in source else "unverified"
                daily_rows.append(
                    {
                        **row,
                        "symbol": symbol,
                        "name": str(
                            (quote or {}).get("name")
                            or meta.get("name")
                            or symbol
                        ),
                        "industry": str(
                            (quote or {}).get("industry")
                            or meta.get("industry")
                            or "A股"
                        ),
                        "adjustment": adjustment,
                        "fetched_at": fetched_at,
                    }
                )
            record(
                "fetch",
                f"{symbol} 完成: {len(history)} 行",
                current=index,
                total=total,
                percent=round(index / max(total, 1) * 100),
            )

        if not daily_rows:
            raise ValueError("所有股票的历史行情均抓取失败，未生成原始快照")

        request_payload = {
            **request.to_dict(),
            "symbols": requested,
            "source_policy": "auto: 东方财富优先，腾讯与新浪按接口回退",
        }
        manifest = self.store.write_run(
            request=request_payload,
            daily_rows=daily_rows,
            quote_rows=quote_rows,
            universe_rows=universe_rows,
            failures=failures,
            trace=trace,
            agent=agent,
        )
        emit(
            "complete",
            f"原始快照已保存: {manifest['run_id']}",
            current=total,
            total=total,
            percent=100,
            run_id=manifest["run_id"],
        )
        return {
            "run_id": manifest["run_id"],
            "status": manifest["status"],
            "summary": manifest["summary"],
            "quality": manifest["quality"],
            "failures": manifest["failures"],
            "manifest_path": manifest["manifest_path"],
        }

    def collect(self, request: CollectionRequest, emit=None, agent=None) -> dict:
        emit_fn = emit or (lambda *_args, **_kwargs: None)
        return self._collect(request, emit_fn, agent=agent)

    def _update_job(self, job_id: str, **changes: Any) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(changes)

    def _append_event(self, job_id: str, event: str, message: str, **extra: Any) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["stage"] = event
            job["message"] = message
            if "percent" in extra:
                job["progress"] = extra["percent"]
            job["events"].append(
                {"time": now_iso(), "event": event, "message": message, **extra}
            )
            job["events"] = job["events"][-120:]

    def _run_job(self, job_id: str, payload: dict) -> None:
        self._update_job(job_id, status="running", started_at=now_iso())
        try:
            request = CollectionRequest.from_payload(payload)
            mode = str(payload.get("mode") or "agent").lower()
            if mode not in {"agent", "fixed", "risk"}:
                raise ValueError("任务模式只能是 agent、fixed 或 risk")

            def emit(event: str, message: str, **extra: Any) -> None:
                self._append_event(job_id, event, message, **extra)

            if mode == "risk":
                from data_agent.risk_service import RiskAnalysisService

                risk_service = getattr(self, "risk_service", None) or RiskAnalysisService(
                    collector=self
                )
                self.risk_service = risk_service
                result = risk_service.analyze(request, emit=emit)
            elif mode == "agent":
                factory = self.agent_factory
                if factory is None:
                    from data_agent.agent import DeepSeekAgent, DeepSeekClient

                    client = DeepSeekClient()
                    if not client.configured:
                        raise ValueError(
                            "未配置 DEEPSEEK_API_KEY，无法运行智能体。"
                            "可改用固定流程采集，或把密钥写入本地 .env。"
                        )
                    factory = lambda service: DeepSeekAgent(service, client=client)
                agent = factory(self)
                result = agent.run(request, emit=emit)
            else:
                result = self.collect(request, emit=emit, agent={"provider": "fixed"})
            self._update_job(
                job_id,
                status="complete",
                progress=100,
                stage="complete",
                message="采集完成",
                result=result,
                finished_at=now_iso(),
            )
        except Exception as exc:
            self._append_event(job_id, "failed", str(exc))
            self._update_job(
                job_id,
                status="failed",
                error=str(exc),
                finished_at=now_iso(),
            )

    def create_job(self, payload: dict, background: bool = True) -> dict:
        job_id = f"JOB-{datetime.now(CHINA_TZ).strftime('%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        job = {
            "id": job_id,
            "status": "queued",
            "mode": str(payload.get("mode") or "agent"),
            "stage": "queued",
            "message": "等待执行",
            "progress": 0,
            "events": [],
            "result": None,
            "error": None,
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
            if len(self._jobs) > 40:
                oldest = sorted(
                    self._jobs.values(), key=lambda item: item["created_at"]
                )[: len(self._jobs) - 40]
                for item in oldest:
                    self._jobs.pop(item["id"], None)
        if background:
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, dict(payload)),
                daemon=True,
                name=f"collector-{job_id}",
            )
            job["_thread"] = thread
            thread.start()
        else:
            self._run_job(job_id, dict(payload))
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict:
        with self._jobs_lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise FileNotFoundError(f"未找到任务 {job_id}")
            return {
                key: value
                for key, value in job.items()
                if not key.startswith("_") and key != "result"
            } | ({"result": job.get("result")} if job.get("result") else {})
