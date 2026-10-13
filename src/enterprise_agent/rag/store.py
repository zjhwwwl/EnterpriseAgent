"""Official Chroma 1.5.9 HTTP SDK with finite transport timeouts.

The pinned SDK creates ``FastAPI._session`` with ``timeout=None`` and ignores
the nonexistent ``chroma_server_request_timeout_seconds`` setting. HTTP
``Client.from_system`` also creates fresh Systems instead of reusing its
argument because HTTP System identifiers are random. We consequently bind the
official ``System`` / ``ServerAPI`` / ``Collection`` directly, setting the one
private transport field *before* identity/tenant/database network requests.
There is no custom REST implementation or mutation of SDK globals. Upgrading
the pinned client requires reviewing this small bridge and its real HTTP tests.
"""

from importlib.metadata import PackageNotFoundError, version
from threading import RLock
from typing import TYPE_CHECKING

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError

if TYPE_CHECKING:
    from chromadb.api import ServerAPI
    from chromadb.api.models.Collection import Collection
    from chromadb.config import System

_CHROMA_CLIENT_VERSION = "1.5.9"


class ChromaStore:
    def __init__(self, settings: Settings, fingerprint: str):
        self.settings = settings
        self.name = settings.rag_collection_prefix + "_" + fingerprint[:20]
        self._client: ServerAPI | None = None
        self._collection: Collection | None = None
        self._system: System | None = None
        self._lock = RLock()
        self._closed = False

    @staticmethod
    def _dispose(system: "System", server: "ServerAPI | None") -> None:
        # FastAPI inherits Component.stop(), which does not close its HTTP pool.
        try:
            system.stop()
        finally:
            if server is not None:
                server._session.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise BusinessError("vector_store_closed", "向量检索服务已停止", 503)

    @property
    def client(self) -> "ServerAPI":
        """Return the official server API, with bounded requests from the outset."""
        with self._lock:
            self._ensure_open()
            if self._client is not None:
                return self._client
            try:
                installed_version = version("chromadb-client")
            except PackageNotFoundError:
                installed_version = None
            if installed_version != _CHROMA_CLIENT_VERSION:
                raise BusinessError("vector_store_sdk_mismatch", "向量检索客户端版本不匹配", 503)

            import httpx
            from chromadb.api import ServerAPI
            from chromadb.api.fastapi import FastAPI
            from chromadb.config import DEFAULT_DATABASE, DEFAULT_TENANT, System
            from chromadb.config import Settings as ChromaSettings

            system = System(
                ChromaSettings(
                    chroma_api_impl="chromadb.api.fastapi.FastAPI",
                    chroma_server_host=self.settings.chroma_host,
                    chroma_server_http_port=self.settings.chroma_port,
                    chroma_server_ssl_enabled=False,
                    anonymized_telemetry=False,
                    chroma_http_max_connections=1,
                    chroma_http_max_keepalive_connections=1,
                )
            )
            server = None
            try:
                server = system.instance(ServerAPI)
                if not isinstance(server, FastAPI) or not isinstance(server._session, httpx.Client):
                    raise RuntimeError("The pinned Chroma HTTP transport contract changed")
                deadline = self.settings.chroma_timeout_seconds
                server._session.timeout = httpx.Timeout(
                    deadline, connect=min(3.0, deadline), pool=min(3.0, deadline)
                )
                system.start()
                # Preserve the official HTTP client's initial server validation.
                server.get_user_identity()
                server.get_tenant(DEFAULT_TENANT)
                server.get_database(DEFAULT_DATABASE, tenant=DEFAULT_TENANT)
            except Exception:
                self._dispose(system, server)
                raise BusinessError("vector_store_unavailable", "向量检索暂不可用", 503) from None
            self._system = system
            self._client = server
            return server

    @property
    def collection(self) -> "Collection":
        with self._lock:
            self._ensure_open()
            if self._collection is None:
                from chromadb.api.models.Collection import Collection

                server = self.client
                model = server.get_or_create_collection(
                    self.name, configuration={"hnsw": {"space": "cosine"}}
                )
                collection = Collection(client=server, model=model, embedding_function=None)
                if collection.configuration.get("hnsw", {}).get("space") != "cosine":
                    # get_or_create does not change an existing collection's metric.
                    raise BusinessError(
                        "vector_store_configuration", "向量索引距离配置不匹配，请检查索引", 503
                    )
                self._collection = collection
            return self._collection

    def close(self) -> None:
        """Release transport resources; this store cannot reopen after shutdown."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            system, server = self._system, self._client
            self._collection = None
            self._client = None
            self._system = None
            if system is not None:
                self._dispose(system, server)

    def ready(self) -> bool:
        try:
            if not self.client.heartbeat():
                return False
            # Validate actual collection access and metric, including existing indexes.
            return self.collection.configuration["hnsw"]["space"] == "cosine"
        except Exception:
            return False

    def upsert(self, ids, texts, vectors, metadatas):
        try:
            for offset in range(0, len(ids), 64):
                self.collection.upsert(
                    ids=ids[offset : offset + 64],
                    documents=texts[offset : offset + 64],
                    embeddings=vectors[offset : offset + 64],
                    metadatas=metadatas[offset : offset + 64],
                )
        except Exception:
            raise BusinessError(
                "vector_store_unavailable", "向量索引写入失败，请稍后重新索引", 503
            ) from None

    def query(self, vector, index_ids: list[str], limit: int):
        if not index_ids:
            return []
        try:
            result = self.collection.query(
                query_embeddings=[vector],
                n_results=limit,
                where={"index_id": {"$in": index_ids}},
                include=["distances"],
            )
            return list(zip(result["ids"][0], result["distances"][0], strict=True))
        except Exception:
            raise BusinessError("vector_store_unavailable", "向量检索暂不可用", 503) from None

    def delete_index(self, index_id: str):
        self.collection.delete(where={"index_id": index_id})

    def delete_document(self, document_id: str):
        self.collection.delete(where={"document_id": document_id})
