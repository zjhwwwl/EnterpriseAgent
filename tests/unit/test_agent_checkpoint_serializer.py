"""Checkpoint deserialization uses the SDK's strict built-in allowlist."""

from dataclasses import dataclass
from typing import ClassVar

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from enterprise_agent.agents.checkpoint import AgentCheckpointRuntime, thread_lock_key


@dataclass
class UnregisteredCheckpointType:
    value: str
    construction_count: ClassVar[int] = 0

    def __post_init__(self):
        type(self).construction_count += 1


def strict_serializer():
    return JsonPlusSerializer(
        allowed_json_modules=None, allowed_msgpack_modules=None, pickle_fallback=False
    )


def test_plain_json_graph_state_round_trips_without_custom_constructors():
    state = {"messages": [{"role": "user", "content": "查询订单 10086"}], "steps": [1], "ok": True}
    serializer = strict_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(state)) == state


def test_unregistered_checkpoint_type_is_not_constructed_when_loading():
    value = UnregisteredCheckpointType("untrusted")
    serializer = strict_serializer()
    encoded = serializer.dumps_typed(value)
    original_count = UnregisteredCheckpointType.construction_count
    assert serializer.loads_typed(encoded) == {"value": "untrusted"}
    assert UnregisteredCheckpointType.construction_count == original_count


def test_pickle_deserialization_is_disabled():
    with pytest.raises(NotImplementedError):
        strict_serializer().loads_typed(("pickle", b"not-executed"))


def test_lock_keys_normalize_uuid_and_reject_non_thread_identifiers():
    thread_id = "7c096f4c-7d43-47ce-bca2-cd52daf4107f"
    assert thread_lock_key(thread_id) == thread_lock_key(thread_id.upper())
    assert -(2**63) <= thread_lock_key(thread_id) < 2**63
    with pytest.raises(ValueError):
        thread_lock_key("arbitrary-client-thread-id")


def test_checkpoint_pool_cannot_starve_itself_or_accept_sqlite():
    with pytest.raises(ValueError):
        AgentCheckpointRuntime("postgresql://unused", pool_size=1)
    with pytest.raises(ValueError):
        AgentCheckpointRuntime("sqlite:///unused")
