"""Local HTTP server for the data collection workbench."""

from __future__ import annotations

import json
import mimetypes
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from data_agent.agent import DeepSeekClient
from data_agent.factor_panel import (
    factor_file,
    factor_sample,
    factor_summary_for_api,
)
from data_agent.fundamentals import (
    fundamental_file,
    latest_fundamental_for_run,
    load_fundamental_manifest,
)
from data_agent.research_exports import (
    build_research_exports,
    derived_file,
    load_coverage,
    load_research_exports,
)
from data_agent.risk_service import RiskAnalysisService
from data_agent.service import CollectorService
from data_agent.store import VIEWS


class CollectorRequestHandler(BaseHTTPRequestHandler):
    server_version = "QuantGuardCollector/1.0"
    protocol_version = "HTTP/1.1"

    def _service(self) -> CollectorService:
        return self.server.collector_service  # type: ignore[attr-defined]

    def _risk_service(self) -> RiskAnalysisService:
        service = self._service()
        risk_service = getattr(service, "risk_service", None)
        if risk_service is None:
            risk_service = RiskAnalysisService(collector=service)
            service.risk_service = risk_service
        return risk_service

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                self.wfile.write(chunk)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message, "status": status}, status=status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体必须是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                self._send_json({"status": "ok", "service": "QuantGuard Data Agent"})
                return
            if parsed.path == "/api/overview":
                client = DeepSeekClient()
                runs = self._service().store.list_run_summaries(limit=12)
                self._send_json(
                    {
                        "agent": {
                            "provider": "deepseek",
                            "configured": client.configured,
                            "model": client.model,
                        },
                        "sources": self._service().catalog(),
                        "recent_runs": runs,
                        "recent_audits": self._risk_service().list_audits(limit=8),
                    }
                )
                return
            if parsed.path == "/api/sources":
                self._send_json({"items": self._service().catalog()})
                return
            if parsed.path == "/api/runs":
                limit = int((query.get("limit") or ["30"])[0])
                self._send_json({"items": self._service().store.list_run_summaries(limit)})
                return
            if parsed.path == "/api/audits":
                limit = int((query.get("limit") or ["20"])[0])
                self._send_json({"items": self._risk_service().list_audits(limit)})
                return
            if parsed.path.startswith("/api/audits/"):
                audit_id = urllib.parse.unquote(
                    parsed.path.removeprefix("/api/audits/").strip("/")
                )
                self._send_json(self._risk_service().load_audit(audit_id))
                return
            if parsed.path.startswith("/api/runs/"):
                tail = parsed.path.removeprefix("/api/runs/").strip("/")
                parts = tail.split("/")
                run_id = urllib.parse.unquote(parts[0])
                if len(parts) == 1:
                    manifest = self._service().store.load_run(run_id)
                    manifest["manifest_path"] = (
                        self._service()
                        .store.manifest_dir.joinpath(f"{run_id}.json")
                        .relative_to(self._service().store.root)
                        .as_posix()
                    )
                    self._send_json(manifest)
                    return
                if len(parts) == 2 and parts[1] == "fundamentals":
                    manifest = latest_fundamental_for_run(run_id)
                    self._send_json(
                        {
                            "available": manifest is not None,
                            "raw_run_id": run_id,
                            "snapshot": manifest,
                        }
                    )
                    return
                if len(parts) == 2 and parts[1] == "fundamentals-file":
                    relative_path = (query.get("path") or [""])[0]
                    fund_id = (query.get("fund_id") or [""])[0]
                    if not relative_path or not fund_id:
                        raise ValueError("缺少基本面快照或文件路径")
                    manifest = load_fundamental_manifest(fund_id)
                    if manifest.get("source_run_id") != run_id:
                        raise ValueError("基本面快照不属于当前行情快照")
                    path = fundamental_file(fund_id, relative_path)
                    self._send_file(
                        path,
                        path.name,
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    )
                    return
                if len(parts) == 2 and parts[1] == "factors":
                    try:
                        result = factor_summary_for_api(
                            run_id, derived_dir=self._service().derived_dir
                        )
                        self._send_json({"available": True, **result})
                    except FileNotFoundError:
                        self._send_json(
                            {"available": False, "raw_run_id": run_id}
                        )
                    return
                if len(parts) == 2 and parts[1] == "factor-sample":
                    self._send_json(
                        factor_sample(
                            run_id,
                            limit=int((query.get("limit") or ["20"])[0]),
                            query=(query.get("q") or [""])[0],
                            derived_dir=self._service().derived_dir,
                        )
                    )
                    return
                if len(parts) == 2 and parts[1] == "factor-file":
                    relative_path = (query.get("path") or [""])[0]
                    if not relative_path:
                        raise ValueError("缺少因子文件路径")
                    path = factor_file(
                        run_id,
                        relative_path,
                        derived_dir=self._service().derived_dir,
                    )
                    self._send_file(
                        path,
                        path.name,
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    )
                    return
                if len(parts) == 2 and parts[1] == "coverage":
                    self._send_json(
                        load_coverage(
                            run_id,
                            derived_dir=self._service().derived_dir,
                        )
                    )
                    return
                if len(parts) == 2 and parts[1] == "derived":
                    self._send_json(
                        load_research_exports(
                            run_id,
                            derived_dir=self._service().derived_dir,
                        )
                    )
                    return
                if len(parts) == 2 and parts[1] == "derived-file":
                    relative_path = (query.get("path") or [""])[0]
                    if not relative_path:
                        raise ValueError("缺少研究导出文件路径")
                    path = derived_file(
                        run_id,
                        relative_path,
                        derived_dir=self._service().derived_dir,
                    )
                    self._send_file(
                        path,
                        path.name,
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    )
                    return
                if len(parts) == 2 and parts[1] == "csv":
                    view = (query.get("view") or ["daily"])[0]
                    if view not in VIEWS:
                        raise ValueError("数据视图只能是 daily、quotes 或 universe")
                    path = self._service().store.file_for_view(run_id, view)
                    self._send_file(
                        path,
                        f"{run_id}_{view}.csv",
                        "text/csv; charset=utf-8",
                    )
                    return
                if len(parts) == 2 and parts[1] == "rows":
                    view = (query.get("view") or ["daily"])[0]
                    offset = int((query.get("offset") or ["0"])[0])
                    limit = int((query.get("limit") or ["50"])[0])
                    text = (query.get("q") or [""])[0]
                    result = self._service().store.read_rows(
                        run_id, view, offset=offset, limit=limit, query=text
                    )
                    self._send_json(result)
                    return
            if parsed.path.startswith("/api/jobs/"):
                job_id = urllib.parse.unquote(
                    parsed.path.removeprefix("/api/jobs/").strip("/")
                )
                self._send_json(self._service().get_job(job_id))
                return
            if parsed.path.startswith("/api/"):
                self._send_error_json("未知 API", 404)
                return
            self._serve_static(parsed.path)
        except FileNotFoundError as exc:
            self._send_error_json(str(exc), 404)
        except (ValueError, TypeError) as exc:
            self._send_error_json(str(exc), 400)
        except Exception as exc:
            self.log_error("collector GET error: %s", exc)
            self._send_error_json(f"服务异常: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/exports"):
                run_id = urllib.parse.unquote(
                    parsed.path.removeprefix("/api/runs/")
                    .removesuffix("/exports")
                    .strip("/")
                )
                result = build_research_exports(
                    run_id,
                    store=self._service().store,
                    derived_dir=self._service().derived_dir,
                    rebuild=bool(payload.get("rebuild")),
                )
                self._send_json(
                    {
                        "run_id": result["run_id"],
                        "coverage": result["coverage"]["summary"],
                        "parquet_rows": result["parquet"]["daily_rows"],
                        "duckdb": result["duckdb"],
                    }
                )
                return
            if parsed.path == "/api/jobs":
                job = self._service().create_job(payload, background=True)
                self._send_json(job, status=202)
                return
            if parsed.path == "/api/sources/probe":
                source_ids = payload.get("source_ids")
                if source_ids is not None and not isinstance(source_ids, list):
                    raise ValueError("source_ids 必须是数组")
                self._send_json(
                    {"items": self._service().probe_sources(source_ids)}
                )
                return
            if parsed.path == "/api/search":
                keyword = str(payload.get("keyword") or "").strip()
                limit = int(payload.get("limit", 8))
                self._send_json(
                    {"items": self._service().search(keyword, limit=limit)}
                )
                return
            self._send_error_json("未知 API", 404)
        except (ValueError, TypeError) as exc:
            self._send_error_json(str(exc), 400)
        except Exception as exc:
            self.log_error("collector POST error: %s", exc)
            self._send_error_json(f"服务异常: {exc}", 500)

    def _serve_static(self, path: str) -> None:
        static_root = Path(__file__).resolve().parent / "static"
        if path in {"", "/"}:
            target = static_root / "index.html"
        elif path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            file_name = urllib.parse.unquote(path.lstrip("/"))
            if file_name not in {"app.js", "styles.css", "index.html"}:
                raise FileNotFoundError("页面资源不存在")
            target = static_root / file_name
        if not target.exists():
            raise FileNotFoundError("页面资源不存在")
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".js", ".css"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[collector] {self.address_string()} {fmt % args}", flush=True)


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, service: CollectorService):
        super().__init__(address, handler)
        self.collector_service = service


def create_server(
    host: str = "127.0.0.1",
    port: int = 0,
    service: CollectorService | None = None,
) -> CollectorServer:
    return CollectorServer((host, int(port)), CollectorRequestHandler, service or CollectorService())


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = create_server(host, port)
    print(f"QuantGuard 数据采集工作台: http://{host}:{server.server_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
