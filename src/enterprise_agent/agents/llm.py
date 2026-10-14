"""Bounded DeepSeek Chat Completions transport; it never executes a tool.

Protocol tests inject an HTTP transport. They do not represent online model quality.
Retries belong to the LangGraph workflow so a request is never silently billed twice.
"""

import asyncio
import json
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError

_MAX_BODY_BYTES = 256 * 1024
_MAX_ARGUMENT_BYTES = 16 * 1024
_MAX_TOOL_CALLS = 4
_WORKFLOW_TOOL_NAMES = frozenset(
    {"route_task", "rewrite_query", "select_knowledge_quotes", "plan_analytics_sql"}
)


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatReply:
    content: str
    tool_calls: list[ModelToolCall]
    message: dict[str, Any]
    usage: dict[str, int]
    elapsed_ms: float


class ChatModel(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatReply: ...


class ModelCallError(BusinessError):
    """Safe failure with provider-reported usage, when the response supplied it.

    Unknown consumption is None. The workflow may store this separately from known
    token totals; missing usage must never become a made-up zero-token request.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 503,
        *,
        usage: dict[str, int] | None = None,
        elapsed_ms: float = 0,
    ) -> None:
        super().__init__(code, message, status_code)
        self.usage = usage
        self.elapsed_ms = elapsed_ms


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)


class _Function(_ProtocolModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    arguments: str = Field(max_length=_MAX_ARGUMENT_BYTES)


class _ToolCall(_ProtocolModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    type: Literal["function"]
    function: _Function


class _Message(_ProtocolModel):
    role: Literal["assistant"]
    content: str | None
    tool_calls: list[_ToolCall] | None = Field(default=None, max_length=_MAX_TOOL_CALLS)


class _Choice(_ProtocolModel):
    index: int = Field(ge=0, le=0)
    finish_reason: Literal[
        "stop", "length", "content_filter", "tool_calls", "insufficient_system_resource", "aborted"
    ]
    message: _Message


class _Usage(_ProtocolModel):
    prompt_tokens: int = Field(ge=0, le=10_000_000)
    completion_tokens: int = Field(ge=0, le=10_000_000)
    total_tokens: int = Field(ge=0, le=20_000_000)

    @model_validator(mode="after")
    def total_is_consistent(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Inconsistent provider usage")
        return self


class _Completion(_ProtocolModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)
    usage: _Usage


class _RequestMessage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[_ToolCall] | None = Field(default=None, max_length=_MAX_TOOL_CALLS)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def fields_match_role(self) -> Self:
        if self.role == "tool":
            if self.content is None or self.tool_call_id is None or self.tool_calls is not None:
                raise ValueError("Invalid tool message")
        elif self.tool_call_id is not None:
            raise ValueError("Only tool messages carry a call ID")
        elif self.role != "assistant":
            if self.content is None or self.tool_calls is not None:
                raise ValueError("Invalid text message")
        elif self.content is None and not self.tool_calls:
            raise ValueError("Empty assistant message")
        return self


class _ToolDefinitionFunction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str
    parameters: dict[str, Any]


class _ToolDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    type: Literal["function"]
    function: _ToolDefinitionFunction


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _load_json(value: bytes | str) -> Any:
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("Arguments nested too deeply")
    if isinstance(value, dict):
        if len(value) > 256:
            raise ValueError("Too many argument keys")
        for key, item in value.items():
            key.encode("utf-8")
            _validate_json_value(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 256:
            raise ValueError("Too many argument values")
        for item in value:
            _validate_json_value(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite argument number")
    elif isinstance(value, str):
        value.encode("utf-8")


class DeepSeekChatModel:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.llm_enabled or settings.deepseek_api_key is None:
            raise ModelCallError("agent_model_misconfigured", "模型服务尚未启用或缺少配置。")
        self._settings = settings
        self._url = str(settings.deepseek_base_url).rstrip("/") + "/chat/completions"
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.deepseek_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(settings.agent_model_timeout_seconds, connect=10),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> tuple[dict[str, Any], set[str]]:
        try:
            if not isinstance(messages, list) or not 1 <= len(messages) <= 80:
                raise ValueError("Invalid conversation length")
            prepared = [
                _RequestMessage.model_validate(message).model_dump(exclude_none=True)
                for message in messages
            ]
            definitions = [_ToolDefinition.model_validate(tool) for tool in (tools or [])]
            names = {tool.function.name for tool in definitions}
            if len(names) != len(definitions) or len(definitions) > 32:
                raise ValueError("Invalid tool definitions")
            if any(tool.function.parameters.get("type") != "object" for tool in definitions):
                raise ValueError("Tool arguments must use an object schema")
            payload = {
                "model": self._settings.deepseek_model,
                "messages": prepared,
                "stream": False,
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": self._settings.agent_model_max_tokens,
            }
            if definitions:
                payload["tools"] = [tool.model_dump() for tool in definitions]
                if len(definitions) == 1 and definitions[0].function.name in _WORKFLOW_TOOL_NAMES:
                    # These control nodes require a structured selection even
                    # when history already contains a human-readable answer.
                    # Named choices are supported because Thinking is disabled.
                    payload["tool_choice"] = {
                        "type": "function",
                        "function": {"name": definitions[0].function.name},
                    }
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > _MAX_BODY_BYTES:
                raise ValueError("Conversation exceeds the request budget")
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise ModelCallError("agent_model_misconfigured", "模型请求结构不符合约束。") from None
        return payload, names

    @staticmethod
    def _parse(body: bytes, offered_names: set[str], elapsed_ms: float) -> ChatReply:
        usage = None
        try:
            raw = _load_json(body)
            if not isinstance(raw, dict):
                raise ValueError("Completion must be an object")
            # Account for reported usage even when the rest of the response is unsafe.
            usage = _Usage.model_validate(raw.get("usage")).model_dump()
            reply = _Completion.model_validate(raw)
            choice = reply.choices[0]
            if choice.finish_reason not in {"stop", "tool_calls"}:
                raise ValueError("Incomplete or interrupted generation")
            calls = choice.message.tool_calls or []
            if bool(calls) != (choice.finish_reason == "tool_calls"):
                raise ValueError("Inconsistent tool call finish reason")
            content = choice.message.content or ""
            content.encode("utf-8")
            if not calls and not content.strip():
                raise ValueError("Empty response")
            seen: set[str] = set()
            parsed_calls = []
            canonical_calls = []
            for call in calls:
                if call.id in seen or call.function.name not in offered_names:
                    raise ValueError("Duplicate call ID or unoffered tool")
                seen.add(call.id)
                if len(call.function.arguments.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
                    raise ValueError("Arguments exceed byte budget")
                arguments = _load_json(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                _validate_json_value(arguments)
                parsed_calls.append(ModelToolCall(call.id, call.function.name, arguments))
                canonical_calls.append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": json.dumps(arguments, ensure_ascii=False, allow_nan=False),
                        },
                    }
                )
            message: dict[str, Any] = {"role": "assistant", "content": content or None}
            if canonical_calls:
                message["tool_calls"] = canonical_calls
            return ChatReply(content, parsed_calls, message, usage, elapsed_ms)
        except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError):
            raise ModelCallError(
                "agent_model_invalid_response",
                "模型返回了无法安全执行的响应。",
                502,
                usage=usage,
                elapsed_ms=elapsed_ms,
            ) from None

    async def complete(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatReply:
        payload, offered_names = self._request(messages, tools)
        started = perf_counter()
        try:
            async with asyncio.timeout(self._settings.agent_model_timeout_seconds):
                async with self._client.stream("POST", self._url, json=payload) as response:
                    if response.status_code == 429:
                        raise ModelCallError("agent_model_rate_limited", "模型服务请求过于频繁。")
                    if response.status_code >= 500:
                        raise ModelCallError("agent_model_unavailable", "模型服务暂时不可用。")
                    if response.status_code != 200:
                        raise ModelCallError("agent_model_misconfigured", "模型服务拒绝了请求。")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > _MAX_BODY_BYTES:
                            raise ModelCallError(
                                "agent_model_invalid_response", "模型响应超过大小限制。", 502
                            )
                        body.extend(chunk)
            elapsed_ms = (perf_counter() - started) * 1000
            return self._parse(bytes(body), offered_names, elapsed_ms)
        except ModelCallError as error:
            error.elapsed_ms = (perf_counter() - started) * 1000
            raise
        except (httpx.HTTPError, TimeoutError, RuntimeError):
            raise ModelCallError(
                "agent_model_unavailable",
                "模型服务连接失败或请求超时。",
                elapsed_ms=(perf_counter() - started) * 1000,
            ) from None
