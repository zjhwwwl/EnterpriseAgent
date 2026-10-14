"""Controlled fixtures, not real database/model measurements, for runner guards."""

import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.config import Settings
from enterprise_agent.evaluations.live import (
    api,
    discover_bindings,
    local_origin,
    summarize,
)
from enterprise_agent.evaluations.live import (
    test_urls as guarded_urls,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost/api",
        "http://me:secret@localhost",
        "http://localhost/?x=1",
    ],
)
def test_live_runner_rejects_non_loopback_or_non_origin_targets(url):
    with pytest.raises(ValueError):
        local_origin(url)


def test_live_runner_refuses_development_db_even_with_correct_role():
    settings = Settings(
        _env_file=None,
        test_database_url=SecretStr(
            "postgresql+psycopg://ea_test_app:synthetic@127.0.0.1/enterprise_agent"
        ),
    )
    with pytest.raises(ValueError):
        guarded_urls(settings)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/refunds",
        "/api/v1/agent/tasks/abc/resume",
        "/api/v1/agent/tasks/abc/recover",
    ],
)
async def test_evaluator_will_not_confirm_refunds_or_recover_models(path):
    class NoRequests:
        async def request(self, *args, **kwargs):
            pytest.fail("A forbidden mutation reached the transport")

    with pytest.raises(ValueError):
        await api(NoRequests(), "POST", path)


def row(identifier, tokens, *, complete=True):
    return {
        "split": "validation",
        "message_attempted": True,
        "task": {
            "id": identifier,
            "token_usage": {
                "model_calls": 2,
                "prompt_tokens": tokens - 1,
                "completion_tokens": 1,
                "total_tokens": tokens,
                "unknown_usage_calls": 0,
                "usage_complete": complete,
            },
        },
    }


def test_task_observation_replaces_cumulative_usage_instead_of_double_counting():
    result = summarize([row("same", 100), row("same", 150), row("other", 20)])
    assert result["provider_usage"]["total_tokens"] == 170
    assert result["provider_usage"]["unique_task_count"] == 2


def test_lost_message_response_cannot_claim_complete_or_zero_provider_usage():
    records = [row("known", 50), {"split": "validation", "message_attempted": True}]
    usage = summarize(records)["provider_usage"]
    assert usage["total_tokens"] == 50
    assert usage["usage_complete"] is False
    assert usage["actual_total_tokens"] is None


def test_interrupted_observation_preserves_known_usage_but_has_unknown_actual_total():
    usage = summarize([row("interrupted", 44, complete=False)])["provider_usage"]
    assert usage["total_tokens"] == 44
    assert usage["incomplete_task_ids"] == ["interrupted"]
    assert usage["actual_total_tokens"] is None


class CandidateSessionFixture:
    """Only the static foreign-order candidate query is replaced; no real DB."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        assert str(statement) == "SELECT order_no FROM public.orders ORDER BY order_no LIMIT 10001"

        class Result:
            def scalars(self):
                return self

            def all(self):
                return ["20000"]

        return Result()


def binding_transport_fixture(order_numbers, logistics_statuses, requests):
    """Synthetic API fixtures; any POST/provider request fails immediately."""

    def handle(request):
        assert request.method == "GET"
        path = request.url.path
        requests.append(path)
        if path == "/api/v1/orders":
            assert dict(request.url.params) == {"limit": "100"}
            return httpx.Response(200, json={"items": [{"order_no": no} for no in order_numbers]})
        if path.endswith("/logistics"):
            order_no = path.split("/")[-2]
            return httpx.Response(
                logistics_statuses[order_no], json={"order_no": order_no, "status": "simulated"}
            )
        if path == "/api/v1/orders/20000":
            return httpx.Response(404, json={"code": "order_not_found"})
        if path.endswith("/refund-eligibility"):
            assert dict(request.url.params) == {"reason": "change_mind"}
            # The first owned order is refundable, independently of logistics.
            return httpx.Response(200, json={"eligible": path.split("/")[-2] == order_numbers[0]})
        pytest.fail(f"Unexpected synthetic API request: {path}")

    return httpx.MockTransport(handle)


async def test_binding_discovery_skips_404_logistics_and_keeps_refund_candidate_independent(
    monkeypatch,
):
    async def readonly_fixture(session):
        assert isinstance(session, CandidateSessionFixture)

    monkeypatch.setattr("enterprise_agent.evaluations.live._prepare_reference", readonly_fixture)
    requests = []
    transport = binding_transport_fixture(
        ["10086", "10088"], {"10086": 404, "10088": 200}, requests
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        bindings = await discover_bindings(client, CandidateSessionFixture)
    assert bindings == {
        "own_order_no": "10088",
        "foreign_order_no": "20000",
        "refundable_order_no": "10086",
    }
    assert requests == [
        "/api/v1/orders",
        "/api/v1/orders/10086/logistics",
        "/api/v1/orders/10088/logistics",
        "/api/v1/orders/20000",
        "/api/v1/orders/10086/refund-eligibility",
    ]


async def test_binding_discovery_fails_preflight_if_every_owned_logistics_is_404():
    def forbidden_database():
        pytest.fail("Logistics preflight failure must precede database candidate queries")

    requests = []
    transport = binding_transport_fixture(
        ["10086", "10088"], {"10086": 404, "10088": 404}, requests
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        with pytest.raises(ValueError, match="No owned synthetic order has logistics"):
            await discover_bindings(client, forbidden_database)
    assert requests == [
        "/api/v1/orders",
        "/api/v1/orders/10086/logistics",
        "/api/v1/orders/10088/logistics",
    ]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_binding_discovery_does_not_hide_non_404_logistics_errors(status):
    def forbidden_database():
        pytest.fail("A non-404 transport failure must terminate preflight")

    requests = []
    transport = binding_transport_fixture(
        ["10086", "10088"], {"10086": status, "10088": 200}, requests
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        with pytest.raises(RuntimeError, match=f"HTTP {status}"):
            await discover_bindings(client, forbidden_database)
    assert requests == ["/api/v1/orders", "/api/v1/orders/10086/logistics"]


async def test_binding_discovery_never_probes_more_than_100_owned_logistics():
    def forbidden_database():
        pytest.fail("Exhausted bounded preflight must not query other candidates")

    orders = [str(10000 + index) for index in range(101)]
    requests = []
    statuses = {no: 404 for no in orders}
    statuses[orders[-1]] = 200
    transport = binding_transport_fixture(orders, statuses, requests)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        with pytest.raises(ValueError, match="within the first 100 orders"):
            await discover_bindings(client, forbidden_database)
    assert len(requests) == 101
    assert f"/api/v1/orders/{orders[-1]}/logistics" not in requests
