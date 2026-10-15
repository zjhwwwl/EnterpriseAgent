"""Bounded real HTTP failures and real Chroma metric/lifecycle checks."""

import asyncio
import select
import socket
import socketserver
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.rag.store import ChromaStore

pytestmark = pytest.mark.integration


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        self.request.settimeout(0.1)
        try:
            while not server.finished.is_set():
                try:
                    first = self.request.recv(65536)
                    break
                except TimeoutError:
                    continue
            else:
                return
            if not first:
                return
            server.accepted.set()
            if not server.forwarding.is_set():
                # Accept the HTTP request but deliberately never send a response.
                # Observe EOF to prove the timed-out client's pool is disposed.
                while not server.finished.is_set():
                    try:
                        if not self.request.recv(65536):
                            server.disconnected.set()
                            return
                    except TimeoutError:
                        continue
                return
            with socket.create_connection(server.target, timeout=1.0) as upstream:
                self.request.settimeout(1.0)
                upstream.sendall(first)
                while not server.finished.is_set():
                    readable, _, _ = select.select([self.request, upstream], [], [], 0.1)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = upstream if source is self.request else self.request
                        destination.sendall(data)
        except OSError:
            # Socket teardown is expected after a real client timeout/shutdown.
            return


class _SwitchableProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, target=None):
        self.target = target
        self.forwarding = threading.Event()
        self.accepted = threading.Event()
        self.disconnected = threading.Event()
        self.finished = threading.Event()
        super().__init__(("127.0.0.1", 0), _ProxyHandler)


@contextmanager
def _proxy(target=None):
    server = _SwitchableProxy(target)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.finished.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        assert not thread.is_alive()


@pytest.fixture
def live_chroma(request, rag_settings):
    # This fixture never loads/prepares models; remove its isolated health probe.
    settings = rag_settings.model_copy(update={"chroma_timeout_seconds": 0.5})
    probe = ChromaStore(settings, "probe_" + uuid4().hex)
    try:
        available = probe.ready()
    finally:
        try:
            if probe._collection is not None:
                probe.client.delete_collection(probe.name)
        finally:
            probe.close()
    if not available:
        if request.config.getoption("require_rag", default=False):
            pytest.fail("A real local Chroma HTTP server is required")
        pytest.skip("The local Chroma HTTP server is unavailable")
    return settings


def test_first_http_request_times_out_and_failed_transport_is_disposed():
    # Warm imports outside the timing assertion: it measures network waiting.
    from chromadb.config import System  # noqa: F401

    with _proxy() as server:
        settings = Settings(
            _env_file=None,
            app_env="test",
            chroma_host="127.0.0.1",
            chroma_port=server.server_address[1],
            chroma_timeout_seconds=0.2,
        )
        store = ChromaStore(settings, "blackhole_" + uuid4().hex)
        try:
            started = time.monotonic()
            assert not store.ready()
            assert time.monotonic() - started < 2.0
            assert server.accepted.wait(1.0), "An actual HTTP request must reach the blackhole"
            assert server.disconnected.wait(1.0), "The failed HTTP pool must close its socket"
            assert store._client is None
            assert store._system is None
        finally:
            store.close()
        store.close()  # Idempotent shutdown, with no lazy client recreation.
        assert not store.ready()
        with pytest.raises(BusinessError) as raised:
            _ = store.client
        assert raised.value.code == "vector_store_closed"


@pytest.mark.rag
@pytest.mark.asyncio
async def test_same_runtime_recovers_after_blackhole_and_closes_real_http_pool(live_chroma):
    endpoint = (live_chroma.chroma_host, live_chroma.chroma_port)
    with _proxy(endpoint) as server:
        settings = live_chroma.model_copy(
            update={
                "chroma_host": "127.0.0.1",
                "chroma_port": server.server_address[1],
                "chroma_timeout_seconds": 0.2,
                "rag_collection_prefix": "ea_timeout_" + uuid4().hex,
            }
        )
        runtime = RagRuntime(settings)
        created = False
        try:
            assert not await asyncio.wait_for(runtime.call(runtime.store.ready), timeout=2.0)
            assert server.disconnected.wait(1.0)
            # The same TCP endpoint and store recover by forwarding to real Chroma.
            server.forwarding.set()
            assert await asyncio.wait_for(runtime.call(runtime.store.ready), timeout=2.0)
            created = True
            assert not runtime._tasks, "The physical worker must release runtime capacity"
            collection = await runtime.call(lambda: runtime.store.collection)
            assert collection.configuration["hnsw"]["space"] == "cosine"
            await runtime.call(
                runtime.store.upsert,
                ["parallel", "orthogonal", "opposite"],
                ["Metric probe A", "Metric probe B", "Metric probe C"],
                [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
                [{"index_id": "metric-probe"}] * 3,
            )
            distances = dict(
                await runtime.call(runtime.store.query, [1.0, 0.0], ["metric-probe"], 3)
            )
            assert distances == pytest.approx(
                {"parallel": 0.0, "orthogonal": 1.0, "opposite": 2.0}, abs=1e-5
            )
            session = runtime.store.client._session
            assert not session.is_closed
        finally:
            try:
                if created:
                    await runtime.call(runtime.store.client.delete_collection, runtime.store.name)
            finally:
                await runtime.close()
        assert session.is_closed
        assert not runtime.store.ready()
        with pytest.raises(BusinessError):
            _ = runtime.store.collection


@pytest.mark.rag
def test_legacy_cosine_metadata_really_sets_server_metric(live_chroma):
    from chromadb.api.models.Collection import Collection

    store = ChromaStore(live_chroma, "legacy_" + uuid4().hex)
    name = "ea_legacy_metric_" + uuid4().hex
    created = False
    try:
        model = store.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
        created = True
        collection = Collection(client=store.client, model=model, embedding_function=None)
        assert collection.configuration["hnsw"]["space"] == "cosine"
        collection.upsert(
            ids=["parallel", "orthogonal", "opposite"],
            embeddings=[[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
        )
        result = collection.query(query_embeddings=[[1.0, 0.0]], n_results=3, include=["distances"])
        distances = dict(zip(result["ids"][0], result["distances"][0], strict=True))
        assert distances == pytest.approx(
            {"parallel": 0.0, "orthogonal": 1.0, "opposite": 2.0}, abs=1e-5
        )
    finally:
        try:
            if created:
                store.client.delete_collection(name)
        finally:
            store.close()


@pytest.mark.rag
def test_existing_wrong_metric_is_rejected_instead_of_silently_reused(live_chroma):
    store = ChromaStore(live_chroma, "wrong_metric_" + uuid4().hex)
    created = False
    try:
        store.client.get_or_create_collection(store.name, configuration={"hnsw": {"space": "l2"}})
        created = True
        assert not store.ready(), "A live server with the wrong collection metric is not ready"
        with pytest.raises(BusinessError) as raised:
            _ = store.collection
        assert raised.value.code == "vector_store_configuration"
        assert raised.value.status_code == 503
    finally:
        try:
            if created:
                store.client.delete_collection(store.name)
        finally:
            store.close()
