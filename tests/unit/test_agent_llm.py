"""HTTP protocol controls only; synthetic replies are not online model evaluation."""

import asyncio
import copy
import json

import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.agents.llm import DeepSeekChatModel, ModelCallError
from enterprise_agent.config import Settings

_MESSAGES = [{"role": "user", "content": "查询订单 10086 的物流状态。"}]
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_logistics",
            "description": "查询当前已认证用户的订单物流。",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    }
]
_USAGE = {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28}


def _settings(**overrides):
    return Settings(
        _env_file=None,
        llm_enabled=True,
        deepseek_api_key=SecretStr("protocol-only-not-a-real-key"),
        **overrides,
    )


def _completion(*, arguments=None, content="物流信息已查询。"):
    message = {"role": "assistant", "content": content}
    reason = "stop"
    if arguments is not None:
        message["tool_calls"] = [
            {
                "id": "call_protocol_1",
                "type": "function",
                "function": {"name": "get_logistics", "arguments": arguments},
            }
        ]
        reason = "tool_calls"
    return {
        "choices": [{"index": 0, "finish_reason": reason, "message": message}],
        "usage": dict(_USAGE),
    }


async def _controlled_response(payload, *, tools=None):
    def handler(request):
        return httpx.Response(200, content=json.dumps(payload), request=request)

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        return await model.complete(_MESSAGES, tools=tools)
    finally:
        await model.aclose()


@pytest.mark.parametrize("base_url", ["https://api.deepseek.com", "https://api.deepseek.com/v1/"])
async def test_request_uses_compatible_endpoint_bounded_output_and_nonthinking_auto_tools(base_url):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=_completion(arguments='{"order_id":10086}', content=None))

    model = DeepSeekChatModel(
        _settings(deepseek_base_url=base_url), transport=httpx.MockTransport(handler)
    )
    try:
        reply = await model.complete(_MESSAGES, tools=_TOOLS)
    finally:
        await model.aclose()

    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == base_url.rstrip("/") + "/chat/completions"
    assert request.headers["Authorization"] == "Bearer protocol-only-not-a-real-key"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert payload["max_tokens"] == 1024
    assert "tool_choice" not in payload
    assert "parallel_tool_calls" not in payload
    assert payload["tools"] == _TOOLS
    assert reply.tool_calls[0].name == "get_logistics"
    assert reply.tool_calls[0].arguments == {"order_id": 10086}
    assert reply.content == ""
    assert reply.usage == _USAGE
    assert reply.elapsed_ms >= 0
    assert model._client.is_closed


async def test_text_response_and_usage_are_preserved_without_internal_reasoning_or_metadata():
    payload = _completion()
    payload["usage"]["prompt_cache_hit_tokens"] = 4
    payload["choices"][0]["message"].update(
        {"reasoning_content": "private internal content", "actor_id": "forged", "approved": True}
    )
    reply = await _controlled_response(payload)
    assert reply.content == "物流信息已查询。"
    assert reply.tool_calls == []
    assert reply.usage == _USAGE
    assert reply.message == {"role": "assistant", "content": "物流信息已查询。"}


async def test_followup_round_preserves_call_and_corresponding_tool_message():
    first = await _controlled_response(
        _completion(arguments='{"order_id":10086}', content="先查询物流。"), tools=_TOOLS
    )
    outgoing = []

    def handler(request):
        outgoing.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(content="已根据查询结果整理回答。"))

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        await model.complete(
            [
                *_MESSAGES,
                first.message,
                {
                    "role": "tool",
                    "tool_call_id": first.tool_calls[0].id,
                    "content": '{"status":"在途"}',
                },
            ],
            tools=_TOOLS,
        )
    finally:
        await model.aclose()
    assert outgoing[0]["messages"][1] == first.message
    assert outgoing[0]["messages"][2]["tool_call_id"] == first.tool_calls[0].id


@pytest.mark.parametrize(
    "arguments",
    [
        "not JSON",
        "null",
        "[]",
        '"10086"',
        '{"order_id":10086,"order_id":20000}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e999}',
        '{"x":"\\ud800"}',
        '{"\\ud800":"value"}',
        '{"x":' + "[" * 14 + "0" + "]" * 14 + "}",
        '{"x":' + json.dumps(list(range(257))) + "}",
        json.dumps({"x": "中" * 6000}, ensure_ascii=False),
    ],
    ids=[
        "not_json",
        "null",
        "array",
        "string",
        "duplicate",
        "nan",
        "infinity",
        "overflow",
        "surrogate_value",
        "surrogate_key",
        "too_deep",
        "too_many_values",
        "utf8_byte_limit",
    ],
)
async def test_unsafe_json_arguments_are_rejected_and_reported_usage_is_not_lost(arguments):
    with pytest.raises(ModelCallError) as caught:
        await _controlled_response(_completion(arguments=arguments, content=None), tools=_TOOLS)
    assert caught.value.code == "agent_model_invalid_response"
    assert caught.value.usage == _USAGE
    assert arguments not in str(caught.value)


async def test_business_parameter_validation_is_left_to_the_strict_tool_registry():
    # The adapter parses JSON, not authorization. The registry must reject extra
    # fields and validate the integer before running its bound authenticated Service.
    reply = await _controlled_response(
        _completion(arguments='{"order_id":"10086","actor_id":"forged","confirmed":true}'),
        tools=_TOOLS,
    )
    assert reply.tool_calls[0].arguments == {
        "order_id": "10086",
        "actor_id": "forged",
        "confirmed": True,
    }
    assert "actor_id" not in reply.message


@pytest.mark.parametrize(
    "case", ["unknown_tool", "duplicate_id", "five_calls", "unsafe_name", "type"]
)
async def test_calls_must_be_bounded_unique_and_offered(case):
    payload = _completion(arguments="{}", content=None)
    calls = payload["choices"][0]["message"]["tool_calls"]
    if case == "unknown_tool":
        calls[0]["function"]["name"] = "delete_database"
    elif case == "duplicate_id":
        calls.append(copy.deepcopy(calls[0]))
    elif case == "five_calls":
        for index in range(1, 5):
            extra = copy.deepcopy(calls[0])
            extra["id"] = f"call_protocol_{index + 1}"
            calls.append(extra)
    elif case == "unsafe_name":
        calls[0]["function"]["name"] = "get_logistics\nAuthorization"
    else:
        calls[0]["type"] = "computer"
    with pytest.raises(ModelCallError) as caught:
        await _controlled_response(payload, tools=_TOOLS)
    assert caught.value.code == "agent_model_invalid_response"
    assert caught.value.usage == _USAGE


async def test_four_calls_are_parsed_but_never_executed_by_the_adapter():
    payload = _completion(arguments='{"order_id":10086}', content=None)
    calls = payload["choices"][0]["message"]["tool_calls"]
    for index in range(1, 4):
        extra = copy.deepcopy(calls[0])
        extra["id"] = f"call_protocol_{index + 1}"
        calls.append(extra)
    reply = await _controlled_response(payload, tools=_TOOLS)
    assert len(reply.tool_calls) == 4
    assert len(reply.message["tool_calls"]) == 4


@pytest.mark.parametrize(
    "reason", ["length", "content_filter", "insufficient_system_resource", "aborted"]
)
async def test_interrupted_or_truncated_generations_cannot_become_an_answer(reason):
    payload = _completion()
    payload["choices"][0]["finish_reason"] = reason
    with pytest.raises(ModelCallError) as caught:
        await _controlled_response(payload)
    assert caught.value.usage == _USAGE
    assert caught.value.code == "agent_model_invalid_response"


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {**_USAGE, "prompt_tokens": "21"},
        {**_USAGE, "prompt_tokens": True},
        {**_USAGE, "completion_tokens": -1},
        {**_USAGE, "total_tokens": 100},
    ],
)
async def test_missing_or_invalid_usage_is_unknown_not_fabricated_zero(usage):
    payload = _completion()
    payload["usage"] = usage
    with pytest.raises(ModelCallError) as caught:
        await _controlled_response(payload)
    assert caught.value.usage is None
    assert caught.value.code == "agent_model_invalid_response"


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "no_choices",
        "many_choices",
        "role",
        "reason",
        "no_content",
        "index_bool",
        "surrogate",
    ],
)
async def test_malformed_completion_is_rejected(case):
    payload = _completion()
    choice = payload["choices"][0]
    if case == "empty":
        choice["message"]["content"] = "   "
    elif case == "no_choices":
        payload["choices"] = []
    elif case == "many_choices":
        payload["choices"].append(copy.deepcopy(choice))
    elif case == "role":
        choice["message"]["role"] = "system"
    elif case == "reason":
        choice["finish_reason"] = "tool_calls"
    elif case == "no_content":
        del choice["message"]["content"]
    elif case == "index_bool":
        choice["index"] = False
    else:
        choice["message"]["content"] = "\ud800"
    with pytest.raises(ModelCallError) as caught:
        await _controlled_response(payload)
    assert caught.value.code == "agent_model_invalid_response"
    assert caught.value.usage == _USAGE


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "agent_model_misconfigured"),
        (401, "agent_model_misconfigured"),
        (402, "agent_model_misconfigured"),
        (403, "agent_model_misconfigured"),
        (404, "agent_model_misconfigured"),
        (302, "agent_model_misconfigured"),
        (429, "agent_model_rate_limited"),
        (500, "agent_model_unavailable"),
        (503, "agent_model_unavailable"),
    ],
)
async def test_http_errors_are_safe_and_never_retried_or_redirected(status, code):
    requests = []
    private = "upstream-error-must-never-be-exposed"

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text=private, headers={"Location": "https://other.invalid"})

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES)
    finally:
        await model.aclose()
    assert len(requests) == 1
    assert caught.value.code == code
    assert caught.value.usage is None
    assert private not in str(caught.value)
    assert "protocol-only-not-a-real-key" not in str(caught.value)


@pytest.mark.parametrize(
    "error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
async def test_transport_errors_are_safe_without_implicit_retry(error_type):
    requests = []

    def handler(request):
        requests.append(request)
        raise error_type("private exception details", request=request)

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES)
    finally:
        await model.aclose()
    assert len(requests) == 1
    assert caught.value.code == "agent_model_unavailable"
    assert caught.value.usage is None
    assert "private exception details" not in str(caught.value)


async def test_wall_deadline_bounds_a_transport_that_keeps_waiting():
    requests = []
    interrupted = asyncio.Event()

    async def handler(request):
        requests.append(request)
        try:
            await asyncio.sleep(3)
        finally:
            interrupted.set()

    settings = _settings().model_copy(update={"agent_model_timeout_seconds": 0.05})
    model = DeepSeekChatModel(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES)
    finally:
        await model.aclose()
    assert caught.value.code == "agent_model_unavailable"
    assert interrupted.is_set()
    assert len(requests) == 1
    assert 0 < caught.value.elapsed_ms < 1000


async def test_workflow_cancellation_propagates_and_releases_the_request():
    entered = asyncio.Event()
    released = asyncio.Event()

    async def handler(request):
        entered.set()
        try:
            await asyncio.sleep(3)
        finally:
            released.set()

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    running = asyncio.create_task(model.complete(_MESSAGES))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert released.is_set()
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await model.aclose()


@pytest.mark.parametrize("body", [b"not JSON", b'{"choices":[],"choices":[]}', b'"text"', b"\xff"])
async def test_non_json_or_ambiguous_response_is_rejected_without_echoing_body(body):
    def handler(request):
        return httpx.Response(200, content=body)

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES)
    finally:
        await model.aclose()
    assert caught.value.code == "agent_model_invalid_response"
    assert caught.value.usage is None


async def test_oversize_response_is_bounded_before_json_parsing():
    def handler(request):
        return httpx.Response(200, content=b" " * (256 * 1024 + 1))

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES)
    finally:
        await model.aclose()
    assert caught.value.code == "agent_model_invalid_response"
    assert caught.value.usage is None


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "attacker", "content": "text"}],
        [{"role": "tool", "content": "{}"}],
        [{"role": "user", "content": "text", "approved": True}],
        [{"role": "assistant", "content": None}],
        [{"role": "user", "content": "中" * 90000}],
    ],
    ids=[
        "empty",
        "role",
        "tool_without_id",
        "extra_authorization",
        "empty_assistant",
        "byte_budget",
    ],
)
async def test_invalid_local_request_is_rejected_before_http(messages):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=_completion())

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(messages)
    finally:
        await model.aclose()
    assert caught.value.code == "agent_model_misconfigured"
    assert requests == []


@pytest.mark.parametrize("case", ["duplicate", "not_object", "unsafe_name", "extra"])
async def test_invalid_tool_schema_is_rejected_before_http(case):
    tools = copy.deepcopy(_TOOLS)
    if case == "duplicate":
        tools.append(copy.deepcopy(tools[0]))
    elif case == "not_object":
        tools[0]["function"]["parameters"]["type"] = "array"
    elif case == "unsafe_name":
        tools[0]["function"]["name"] = "unsafe.function"
    else:
        tools[0]["function"]["actor_id"] = "forged"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=_completion())

    model = DeepSeekChatModel(_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelCallError) as caught:
            await model.complete(_MESSAGES, tools=tools)
    finally:
        await model.aclose()
    assert caught.value.code == "agent_model_misconfigured"
    assert requests == []


def test_disabled_model_cannot_construct_an_authenticated_client():
    with pytest.raises(ModelCallError) as caught:
        DeepSeekChatModel(Settings(_env_file=None))
    assert caught.value.code == "agent_model_misconfigured"
