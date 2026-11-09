"""DeepSeek function-calling agent for reproducible public data collection."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from config.settings import PROJECT_ROOT
from data_agent.catalog import get_source_catalog
from data_agent.service import CollectionRequest, CollectorService
from data_agent.store import now_iso


def _load_dotenv(path: Path | None = None) -> None:
    """Load simple KEY=VALUE lines without overriding process environment."""
    env_path = Path(path or PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class DeepSeekError(RuntimeError):
    """DeepSeek API request failed."""


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transport: Callable[[str, dict, dict, int], dict] | None = None,
    ):
        _load_dotenv()
        self.api_key = str(api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.base_url = str(
            base_url
            or os.getenv("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = str(model or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat")
        self.transport = transport or self._urllib_transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _urllib_transport(
        self,
        url: str,
        headers: dict,
        payload: dict,
        timeout: int,
    ) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise DeepSeekError(
                f"DeepSeek 请求失败（HTTP {exc.code}）: {detail}"
            ) from exc
        except Exception as exc:
            raise DeepSeekError(f"无法连接 DeepSeek API: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DeepSeekError("DeepSeek 返回内容不是有效 JSON") from exc

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = self.transport(self.endpoint, headers, payload, 60)
        if not isinstance(response, dict) or not response.get("choices"):
            message = response.get("error", {}).get("message") if isinstance(response, dict) else None
            raise DeepSeekError(message or "DeepSeek 未返回可用结果")
        return response


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_data_sources",
            "description": "列出已注册的公开数据源、字段、复权口径和限制。不要自行编造数据源。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "probe_data_sources",
            "description": "实际请求数据源，检查当前网络环境下的可用性和延迟。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["eastmoney", "tencent", "sina"]},
                        "description": "要探测的数据源；留空表示全部。",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_security",
            "description": "按股票代码或名称搜索 A 股证券，只返回公开搜索接口的真实结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "股票代码或名称"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_market_data",
            "description": "调用真实公开行情接口，抓取股票池与日线，写入不可覆盖的原始 CSV 和 manifest。",
            "parameters": {
                "type": "object",
                "properties": {
                    "universe_size": {
                        "type": "integer",
                        "minimum": 10,
                        "maximum": 200,
                        "description": "按成交额筛选的股票数量；指定 symbols 时忽略。",
                    },
                    "history_days": {
                        "type": "integer",
                        "minimum": 30,
                        "maximum": 500,
                        "description": "每只股票请求的日线长度。",
                    },
                    "include_quotes": {
                        "type": "boolean",
                        "description": "是否同时保存当前盘口快照。",
                    },
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选的自定义 6 位股票代码列表。",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]


class AgentToolbox:
    def __init__(self, service: CollectorService, emit: Callable[..., None]):
        self.service = service
        self.emit = emit
        self.run_id: str | None = None
        self.tool_trace: list[dict] = []

    def _record(self, name: str, arguments: dict, result: Any) -> None:
        summary = result
        if isinstance(result, dict) and "rows" in result and isinstance(result["rows"], list):
            summary = {**result, "rows": f"{len(result['rows'])} rows"}
        self.tool_trace.append(
            {
                "time": now_iso(),
                "tool": name,
                "arguments": arguments,
                "result": summary,
            }
        )

    def execute(self, name: str, arguments: dict) -> dict:
        try:
            result = self._execute(name, arguments)
            self._record(name, arguments, result)
            return {"ok": True, "data": result}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            self._record(name, arguments, result)
            return result

    def _execute(self, name: str, arguments: dict) -> Any:
        if name == "list_data_sources":
            return get_source_catalog()
        if name == "probe_data_sources":
            source_ids = arguments.get("source_ids") or None
            return self.service.probe_sources(source_ids)
        if name == "search_security":
            return self.service.search(
                str(arguments.get("keyword") or ""),
                int(arguments.get("limit") or 8),
            )
        if name == "collect_market_data":
            if self.run_id:
                return {"already_collected": True, "run_id": self.run_id}
            request = CollectionRequest.from_payload(
                {
                    **arguments,
                    "query": "DeepSeek 智能体采集",
                }
            )
            result = self.service.collect(
                request,
                emit=self.emit,
                agent={
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "enabled": True,
                    "tool_trace": self.tool_trace,
                },
            )
            self.run_id = result["run_id"]
            return result
        raise ValueError(f"未知工具: {name}")


class DeepSeekAgent:
    def __init__(
        self,
        service: CollectorService,
        client: DeepSeekClient | None = None,
        max_steps: int = 6,
    ):
        self.service = service
        self.client = client or DeepSeekClient()
        self.max_steps = max(2, min(int(max_steps), 10))

    def run(self, request: CollectionRequest, emit: Callable[..., None] | None = None) -> dict:
        emit_fn = emit or (lambda *_args, **_kwargs: None)
        if not self.client.configured:
            raise ValueError("未配置 DEEPSEEK_API_KEY，无法运行智能体")
        toolbox = AgentToolbox(self.service, emit_fn)
        system_prompt = (
            "你是 QuantGuard 的 A 股公开数据采集智能体，只负责数据发现、接口探测、"
            "证券搜索和原始数据采集。所有行情必须来自工具返回值，禁止编造价格、"
            "日期、来源或行数。优先探测数据源；信息足够时必须调用 "
            "collect_market_data 完成一次真实采集，成功后停止继续采集。默认参数由"
            "用户消息提供，用户明确要求可覆盖默认值。最终回答要给出 run_id、"
            "股票数、数据行数、实际来源和失败数，并明确这是原始数据而非投资建议。"
        )
        user_prompt = (
            f"采集任务：{request.query or '采集高流动性 A 股原始行情'}\n"
            f"默认参数：{json.dumps(request.to_dict(), ensure_ascii=False)}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        final_text = ""
        emit_fn("agent", "DeepSeek 开始分析采集任务")
        for step in range(self.max_steps):
            response = self.client.chat(messages, TOOLS)
            message = response["choices"][0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    raw_arguments = function.get("arguments") or "{}"
                    try:
                        arguments = (
                            json.loads(raw_arguments)
                            if isinstance(raw_arguments, str)
                            else dict(raw_arguments)
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        tool_result = {
                            "ok": False,
                            "error": f"工具参数不是有效 JSON: {exc}",
                        }
                        toolbox.tool_trace.append(
                            {
                                "time": now_iso(),
                                "tool": name,
                                "arguments": {"raw": str(raw_arguments)[:500]},
                                "result": tool_result,
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id") or f"call-{step}",
                                "content": json.dumps(tool_result, ensure_ascii=False),
                            }
                        )
                        continue
                    emit_fn("agent_tool", f"调用工具 {name}", tool=name)
                    tool_result = toolbox.execute(name, arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"call-{step}",
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )
                emit_fn(
                    "agent",
                    f"DeepSeek 完成第 {step + 1} 步工具调用",
                    percent=min(92, 12 + (step + 1) * 12),
                )
                continue
            final_text = str(message.get("content") or "").strip()
            if toolbox.run_id:
                break
            messages.append({"role": "assistant", "content": final_text})
            messages.append(
                {
                    "role": "user",
                    "content": "尚未完成真实数据采集。请继续调用 collect_market_data。",
                }
            )

        if not toolbox.run_id:
            raise ValueError("DeepSeek 未完成真实数据采集，任务已终止")
        manifest = self.service.store.load_run(toolbox.run_id)
        agent_payload = {
            "provider": "deepseek",
            "model": self.client.model,
            "enabled": True,
            "final_response": final_text,
            "tool_trace": toolbox.tool_trace,
        }
        attached = self.service.store.update_agent(toolbox.run_id, agent_payload)
        emit_fn("agent", "DeepSeek 已提交数据来源与采集结论", percent=98)
        return {
            "run_id": toolbox.run_id,
            "status": attached.get("status", manifest.get("status")),
            "summary": attached.get("summary", manifest.get("summary")),
            "quality": attached.get("quality", manifest.get("quality")),
            "failures": attached.get("failures", manifest.get("failures")),
            "agent": agent_payload,
            "manifest_path": attached.get("manifest_path"),
        }
