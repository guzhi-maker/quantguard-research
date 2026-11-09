"""QuantGuard 本地 Web 服务。

仅使用 Python 标准库，提供股票搜索、行情快照、组合风控 API
和单页应用静态资源。
"""

from __future__ import annotations

import json
import mimetypes
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from config.settings import RiskConfig
from webapp import demo_data, market_client
from webapp.risk_advisor import analyze_portfolio
from webapp.quant_lab import (
    get_research_dataset,
    get_research_dataset_csv,
    run_research,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_HOLDINGS = 20
MAX_BODY_BYTES = 64 * 1024

SnapshotLoader = Callable[[str, int], dict]
SearchLoader = Callable[[str, int], list[dict]]
UniverseLoader = Callable[[int], list[dict]]
ResearchRunner = Callable[[dict], dict]


class BadRequest(ValueError):
    """请求参数不符合 API 约束。"""


def _validate_holdings(raw_holdings: Any) -> list[dict]:
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raise BadRequest("请至少提供一只持仓股票")
    if len(raw_holdings) > MAX_HOLDINGS:
        raise BadRequest(f"单次最多分析 {MAX_HOLDINGS} 只股票")

    cleaned = []
    seen = set()
    total = 0.0
    for item in raw_holdings:
        if not isinstance(item, dict):
            raise BadRequest("持仓格式不正确")
        try:
            symbol = market_client.normalize_symbol(item.get("symbol"))
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        if symbol in seen:
            raise BadRequest(f"股票代码重复: {symbol}")
        seen.add(symbol)
        try:
            weight = float(item.get("weight"))
        except (TypeError, ValueError) as exc:
            raise BadRequest(f"{symbol} 的权重必须是数字") from exc
        if not 0 < weight <= 100:
            raise BadRequest(f"{symbol} 的权重必须大于 0 且不超过 100")
        total += weight
        cleaned.append({"symbol": symbol, "weight": weight})
    if total > 100.001:
        raise BadRequest(f"总仓位为 {total:.1f}%，不能超过 100%")
    return cleaned


def _load_snapshots(
    holdings: list[dict],
    days: int,
    snapshot_loader: SnapshotLoader,
) -> dict[str, dict]:
    symbols = [item["symbol"] for item in holdings]
    snapshots: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as executor:
        futures = {
            executor.submit(snapshot_loader, symbol, days): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                snapshots[symbol] = future.result()
            except market_client.MarketDataError:
                raise
            except Exception as exc:
                raise market_client.MarketDataError(
                    f"{symbol} 行情处理失败: {exc}"
                ) from exc
    return snapshots


def analyze_payload(
    payload: Any,
    snapshot_loader: Optional[SnapshotLoader] = None,
    config: Optional[RiskConfig] = None,
) -> dict:
    """校验 API 请求并生成可 JSON 序列化的组合风险报告。"""
    if not isinstance(payload, dict):
        raise BadRequest("请求体必须是 JSON 对象")
    data_mode = str(payload.get("data_mode") or "live").lower()
    if data_mode not in {"live", "demo"}:
        raise BadRequest("data_mode 只能是 live 或 demo")
    holdings = _validate_holdings(payload.get("holdings"))
    try:
        days = int(payload.get("days", 252))
    except (TypeError, ValueError) as exc:
        raise BadRequest("历史数据天数必须是整数") from exc
    if not 30 <= days <= 500:
        raise BadRequest("历史数据天数必须在 30 到 500 之间")

    if data_mode == "demo":
        loader = demo_data.get_market_snapshot
    else:
        loader = snapshot_loader or market_client.get_market_snapshot
    snapshots = _load_snapshots(holdings, days, loader)
    try:
        result = analyze_portfolio(holdings, snapshots, config=config)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    try:
        capital = float(payload.get("capital", 50_000))
    except (TypeError, ValueError) as exc:
        raise BadRequest("账户资金必须是数字") from exc
    if not 1_000 <= capital <= 500_000_000_000:
        raise BadRequest("账户资金必须在 1000 元到 5000 亿元之间")
    account_holdings = []
    invested_amount = 0.0
    for row in result.get("holdings", []):
        target_amount = capital * float(row.get("weight_pct", 0.0)) / 100.0
        price = float(row.get("price") or 0.0)
        lots = int(target_amount // (price * 100.0)) if price > 0 else 0
        shares = lots * 100
        actual_amount = shares * price
        invested_amount += actual_amount
        row["target_amount"] = round(target_amount, 2)
        row["shares"] = shares
        row["actual_amount"] = round(actual_amount, 2)
        row["minimum_lot_value"] = round(price * 100.0, 2)
        account_holdings.append(row)
    result["account"] = {
        "capital": round(capital, 2),
        "invested_amount": round(invested_amount, 2),
        "cash_amount": round(max(0.0, capital - invested_amount), 2),
        "cash_weight": round(max(0.0, 1.0 - invested_amount / capital), 6),
        "holdings": account_holdings,
        "lot_size": 100,
        "note": "实际股数按 100 股整手向下取整，剩余资金保留为现金。",
    }
    result["metrics"]["capital"] = round(capital, 2)
    result["meta"] = {
        "holdings_count": len(holdings),
        "history_days": days,
        "data_mode": data_mode,
        "capital": round(capital, 2),
        "source": (
            "QuantGuard 内置确定性演示数据"
            if data_mode == "demo"
            else "东方财富公开行情"
        ),
    }
    return result


class QuantRequestHandler(BaseHTTPRequestHandler):
    server_version = "QuantGuard/1.0"
    protocol_version = "HTTP/1.1"

    def _send_headers(
        self,
        status: int,
        content_type: str,
        length: int,
        cache_control: str = "no-store",
        content_disposition: Optional[str] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache_control)
        if content_disposition:
            self.send_header("Content-Disposition", content_disposition)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self' data:; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _send_csv(self, payload: dict) -> None:
        # The UTF-8 BOM keeps Chinese headers readable in desktop spreadsheet apps.
        body = ("\ufeff" + str(payload["content"])).encode("utf-8")
        filename = str(payload["filename"]).replace('"', "")
        self._send_headers(
            HTTPStatus.OK,
            "text/csv; charset=utf-8",
            len(body),
            content_disposition=f'attachment; filename="{filename}"',
        )
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message, "status": int(status)}, status=status)

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise BadRequest("Content-Length 不正确") from exc
        if length <= 0:
            raise BadRequest("请求体不能为空")
        if length > MAX_BODY_BYTES:
            raise BadRequest("请求体过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest("请求体不是有效 JSON") from exc

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._send_json(
                    {
                        "status": "ok",
                        "service": "QuantGuard",
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                return
            if parsed.path == "/api/market/snapshot":
                parameters = parse_qs(parsed.query)
                try:
                    limit = int((parameters.get("limit") or ["12"])[0])
                except ValueError as exc:
                    raise BadRequest("limit 必须是整数") from exc
                if not 5 <= limit <= 30:
                    raise BadRequest("limit 必须在 5 到 30 之间")
                mode = (parameters.get("data_mode") or ["live"])[0].lower()
                if mode not in {"live", "demo"}:
                    raise BadRequest("data_mode 只能是 live 或 demo")
                loader = (
                    demo_data.get_liquid_universe
                    if mode == "demo"
                    else self.server.universe_loader  # type: ignore[attr-defined]
                )
                items = loader(limit)
                sources = sorted(
                    {
                        str(item.get("source"))
                        for item in items
                        if isinstance(item, dict) and item.get("source")
                    }
                )
                self._send_json(
                    {
                        "items": items,
                        "data_mode": mode,
                        "source": (
                            "QuantGuard 内置确定性演示数据"
                            if mode == "demo"
                            else " + ".join(sources) if sources else "东方财富公开行情"
                        ),
                        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                return
            if parsed.path == "/api/search":
                query = (parse_qs(parsed.query).get("q") or [""])[0].strip()
                if not query:
                    self._send_json({"items": []})
                    return
                if len(query) > 30:
                    raise BadRequest("搜索词不能超过 30 个字符")
                mode = (parse_qs(parsed.query).get("data_mode") or ["live"])[0].lower()
                if mode not in {"live", "demo"}:
                    raise BadRequest("data_mode 只能是 live 或 demo")
                loader = (
                    demo_data.search_stocks
                    if mode == "demo"
                    else self.server.search_loader  # type: ignore[attr-defined]
                )
                self._send_json({"items": loader(query, 8)})
                return
            if parsed.path.startswith("/api/dataset/"):
                parameters = parse_qs(parsed.query, keep_blank_values=True)
                tail = unquote(parsed.path.removeprefix("/api/dataset/")).strip("/")
                is_csv = tail.endswith("/csv")
                dataset_id = tail[:-4].strip("/") if is_csv else tail
                if not dataset_id:
                    raise BadRequest("dataset_id 不能为空")
                view = (parameters.get("view") or ["raw"])[0].lower()
                if is_csv:
                    try:
                        payload = get_research_dataset_csv(dataset_id, view=view)
                    except KeyError as exc:
                        self._send_error_json(HTTPStatus.NOT_FOUND, str(exc.args[0]))
                        return
                    self._send_csv(payload)
                    return

                try:
                    offset = int((parameters.get("offset") or ["0"])[0])
                    limit = int((parameters.get("limit") or ["50"])[0])
                except ValueError as exc:
                    raise BadRequest("offset 和 limit 必须是整数") from exc
                if offset < 0:
                    raise BadRequest("offset 必须是非负整数")
                if not 1 <= limit <= 500:
                    raise BadRequest("limit 必须在 1 到 500 之间")
                try:
                    payload = get_research_dataset(
                        dataset_id,
                        view=view,
                        offset=offset,
                        limit=limit,
                        query=(parameters.get("q") or [""])[0],
                        date_from=(parameters.get("date_from") or [""])[0],
                        date_to=(parameters.get("date_to") or [""])[0],
                    )
                except KeyError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc.args[0]))
                    return
                self._send_json(payload)
                return
            if parsed.path.startswith("/api/stock/"):
                symbol = unquote(parsed.path.removeprefix("/api/stock/"))
                try:
                    days = int((parse_qs(parsed.query).get("days") or ["252"])[0])
                except ValueError as exc:
                    raise BadRequest("历史数据天数必须是整数") from exc
                if not 30 <= days <= 500:
                    raise BadRequest("历史数据天数必须在 30 到 500 之间")
                mode = (parse_qs(parsed.query).get("data_mode") or ["live"])[0].lower()
                if mode not in {"live", "demo"}:
                    raise BadRequest("data_mode 只能是 live 或 demo")
                normalized = market_client.normalize_symbol(symbol)
                if mode == "demo":
                    self._send_json(demo_data.get_market_snapshot(normalized, days))
                else:
                    loader = self.server.snapshot_loader  # type: ignore[attr-defined]
                    self._send_json(loader(normalized, days))
                return
            if parsed.path.startswith("/api/"):
                self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在")
                return
            self._serve_static(parsed.path)
        except BadRequest as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except market_client.MarketDataError as exc:
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception as exc:
            self.log_error("unhandled GET error: %s", exc)
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "服务暂时不可用")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/risk", "/api/lab"}:
            self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在")
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/lab":
                runner = self.server.research_runner  # type: ignore[attr-defined]
                self._send_json(runner(payload))
            else:
                loader = self.server.snapshot_loader  # type: ignore[attr-defined]
                self._send_json(analyze_payload(payload, snapshot_loader=loader))
        except BadRequest as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except market_client.MarketDataError as exc:
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception as exc:
            self.log_error("unhandled POST error: %s", exc)
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "服务暂时不可用")

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._send_headers(HTTPStatus.OK, "application/json; charset=utf-8", 0)
            return
        self._serve_static(parsed.path, head_only=True)

    def _serve_static(self, request_path: str, head_only: bool = False) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        if relative.endswith("/"):
            relative += "index.html"
        root = STATIC_DIR.resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "页面不存在")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        data = candidate.read_bytes()
        cache = (
            "no-cache"
            if candidate.suffix in {".html", ".js", ".css"}
            else "public, max-age=3600"
        )
        self._send_headers(HTTPStatus.OK, content_type, len(data), cache_control=cache)
        if not head_only:
            self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}\n"
        )


class QuantServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        snapshot_loader: Optional[SnapshotLoader] = None,
        search_loader: Optional[SearchLoader] = None,
        research_runner: Optional[ResearchRunner] = None,
        universe_loader: Optional[UniverseLoader] = None,
    ) -> None:
        self.snapshot_loader = snapshot_loader or market_client.get_market_snapshot
        self.search_loader = search_loader or market_client.search_stocks
        self.universe_loader = universe_loader or market_client.get_liquid_universe
        self.research_runner = research_runner or run_research
        super().__init__(server_address, QuantRequestHandler)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    snapshot_loader: Optional[SnapshotLoader] = None,
    search_loader: Optional[SearchLoader] = None,
    research_runner: Optional[ResearchRunner] = None,
    universe_loader: Optional[UniverseLoader] = None,
) -> QuantServer:
    return QuantServer(
        (host, port),
        snapshot_loader,
        search_loader,
        research_runner,
        universe_loader,
    )


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = create_server(host, port)
    display_host = host if host not in {"0.0.0.0", "::"} else "127.0.0.1"
    print(f"QuantGuard 已启动: http://{display_host}:{server.server_port}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()
