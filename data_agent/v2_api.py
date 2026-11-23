"""v2 研究流水线的 Web 接口。

不修改原有采集服务：这里只读 data/raw 的快照清单，把 v2 分析包装成异步任务。
前端页面 /v2.html 通过三个接口驱动：

  GET  /api/v2/snapshots     快照列表
  POST /api/v2/jobs          创建分析任务
  GET  /api/v2/jobs/<id>     查询任务状态与结果

任务在后台线程里串行执行（一次只跑一个），避免多任务抢 CPU 把机器拖垮。
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "v2"
for _path in (str(V2), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

MANIFEST_DIR = ROOT / "data" / "manifests"
OUT_DIR = V2 / "artifacts" / "web"

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_QUEUE: list[tuple[str, dict]] = []
_WORKER: threading.Thread | None = None
_MAX_JOBS = 60


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_snapshots(limit: int = 30, min_symbols: int = 50) -> list[dict]:
    items: list[dict] = []
    for path in MANIFEST_DIR.glob("RAW-*.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = manifest.get("summary") or {}
        if not summary.get("daily_rows"):
            continue
        if int(summary.get("symbols") or 0) < int(min_symbols):
            continue
        items.append(
            {
                "run_id": manifest.get("run_id") or path.stem,
                "created_at": manifest.get("created_at"),
                "symbols": summary.get("symbols"),
                "rows": summary.get("daily_rows"),
                "date_min": summary.get("date_min"),
                "date_max": summary.get("date_max"),
                "sources": summary.get("sources") or [],
                "failures": summary.get("failures", 0),
            }
        )
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items[:limit]


def _snapshot_exists(run_id: str) -> bool:
    return (MANIFEST_DIR / f"{run_id}.json").exists()


def parse_holdings(raw) -> dict[str, float]:
    """支持 "600519:0.2,300750:0.15" 或 {"600519": 0.2} 两种写法。"""
    if raw in (None, "", {}, []):
        return {}
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, str):
        pairs = []
        for chunk in raw.replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                raise ValueError("持仓格式应为 代码:权重，例如 600519:0.2")
            symbol, weight = chunk.split(":", 1)
            pairs.append((symbol.strip(), weight))
    else:
        raise ValueError("持仓参数格式不正确")
    holdings: dict[str, float] = {}
    for symbol, weight in pairs:
        code = str(symbol).strip()
        if not (code.isdigit() and len(code) == 6):
            raise ValueError(f"股票代码应为 6 位数字: {code}")
        value = float(weight)
        if value < 0 or value > 1:
            raise ValueError(f"{code} 的权重应在 0~1 之间")
        holdings[code] = value
    total = sum(holdings.values())
    if total > 1.5:
        raise ValueError("持仓权重合计不能超过 1.5")
    return holdings


def _validate(payload: dict) -> dict:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        snapshots = list_snapshots(limit=1)
        if not snapshots:
            raise ValueError("还没有可用快照，请先采集数据")
        run_id = snapshots[0]["run_id"]
    if not _snapshot_exists(run_id):
        raise ValueError(f"快照不存在: {run_id}")
    risk = float(payload.get("risk", 5.5))
    if not 1.0 <= risk <= 10.0:
        raise ValueError("风险档位应在 1~10 之间")
    horizon = int(payload.get("horizon", 2))
    if not 1 <= horizon <= 3:
        raise ValueError("期限档位应在 1~3 之间（5/20/60 日）")
    capital = float(payload.get("capital", 1_000_000))
    if not 10_000 <= capital <= 1e9:
        raise ValueError("本金应在 1 万~10 亿之间")
    seeds = int(payload.get("seeds", 3))
    if seeds not in {1, 3, 5}:
        raise ValueError("seeds 只能是 1、3 或 5")
    positions = int(payload.get("positions", 0) or 0)
    if positions and not 3 <= positions <= 50:
        raise ValueError("持仓数量应在 3~50 之间，0 表示按档位")
    holdings = parse_holdings(payload.get("holdings"))
    return {
        "run_id": run_id,
        "risk": risk,
        "horizon": horizon,
        "capital": capital,
        "holdings": holdings,
        "seeds": seeds,
        "positions": positions or None,
        "with_report": bool(payload.get("with_report")),
    }


def start_job(payload: dict) -> dict:
    params = _validate(payload)
    job_id = "v2-" + uuid.uuid4().hex[:10]
    job = {
        "job_id": job_id,
        "status": "queued",
        "stage": "排队中",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "params": params,
        "error": None,
        "result": None,
        "queue_position": len(_QUEUE) + (1 if _WORKER and _WORKER.is_alive() else 0),
    }
    with _LOCK:
        _JOBS[job_id] = job
        _QUEUE.append((job_id, params))
        running = {pair[0] for pair in _QUEUE}
        overflow = len(_JOBS) - _MAX_JOBS
        if overflow > 0:
            finished = [key for key in _JOBS if key not in running]
            for old_id in finished[:overflow]:
                _JOBS.pop(old_id, None)
        _ensure_worker()
    return {"job_id": job_id, "status": "queued", "queue_position": job["queue_position"]}


def _ensure_worker() -> None:
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    _WORKER = threading.Thread(target=_worker_loop, name="v2-worker", daemon=True)
    _WORKER.start()


def _worker_loop() -> None:
    while True:
        with _LOCK:
            if not _QUEUE:
                return
            job_id, params = _QUEUE.pop(0)
            job = _JOBS.get(job_id)
        if job is None:
            continue
        _run_job(job, params)


def _run_job(job: dict, params: dict) -> None:
    job["status"] = "running"
    job["started_at"] = _now()
    job["stage"] = "加载数据"

    def progress(stage: str) -> None:
        job["stage"] = stage

    try:
        from research import pipeline as pipeline_mod

        result = pipeline_mod.run_research(
            run_id=params["run_id"],
            risk=params["risk"],
            horizon=params["horizon"],
            holdings=params["holdings"] or None,
            capital=params["capital"],
            with_report=params["with_report"],
            variant=pipeline_mod.make_variant(seeds=params["seeds"]),
            progress=progress,
            positions=params.get("positions"),
        )
        payload = result.as_dict()
        job["result"] = payload
        job["status"] = "done"
        job["stage"] = "完成"
        job["finished_at"] = _now()
        _persist(job)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["stage"] = "失败"
        job["error"] = str(exc)[:500]
        job["finished_at"] = _now()
        job["traceback"] = traceback.format_exc()[-2000:]
        _persist(job)


def _persist(job: dict) -> None:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"{job['job_id']}.json"
        payload = {key: value for key, value in job.items() if key != "traceback"}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def get_job(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        path = OUT_DIR / f"{job_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"任务不存在: {job_id}")
    view = dict(job)
    view.pop("traceback", None)
    if view.get("started_at") and not view.get("finished_at"):
        try:
            started = datetime.fromisoformat(view["started_at"])
            view["elapsed_seconds"] = round(
                (datetime.now(started.tzinfo) - started).total_seconds(), 1
            )
        except ValueError:
            view["elapsed_seconds"] = None
    if view.get("started_at") and view.get("finished_at"):
        try:
            started = datetime.fromisoformat(view["started_at"])
            finished = datetime.fromisoformat(view["finished_at"])
            view["elapsed_seconds"] = round((finished - started).total_seconds(), 1)
        except ValueError:
            pass
    return view


def list_jobs(limit: int = 12) -> list[dict]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda item: item["created_at"], reverse=True)
    return [
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "stage": job["stage"],
            "created_at": job["created_at"],
            "elapsed_seconds": job.get("elapsed_seconds"),
            "params": {key: job["params"].get(key) for key in ("run_id", "risk", "horizon", "capital")},
            "error": job.get("error"),
        }
        for job in jobs[:limit]
    ]
