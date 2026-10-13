"""Pinned Chinese ONNX inference with verified assets and no implicit downloads.

The embedding graph uses CLS pooling and L2 normalization through FastEmbed.
The query instruction is applied only to queries. Reranking applies sigmoid to
raw logits. These relevance scores are not calibrated probabilities; thresholds
must be evaluated on project data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from enterprise_agent.domain_errors import BusinessError

if TYPE_CHECKING:
    from enterprise_agent.config import Settings

logger = logging.getLogger("enterprise_agent.rag.models")
EMBEDDING_DIMENSION = 512
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
FASTEMBED_VERSION = "0.8.0"
ORT_VERSION = "1.23.2"
_REGISTRATION_LOCK = threading.Lock()


@dataclass(frozen=True)
class ModelFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    component: str
    model_name: str
    repository: str
    revision: str
    model_file: str
    files: tuple[ModelFile, ...]


EMBEDDING_SPEC = ModelSpec(
    component="embedding",
    model_name="BAAI/bge-small-zh-v1.5",
    repository="Qdrant/bge-small-zh-v1.5",
    revision="46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
    model_file="model_optimized.onnx",
    files=(
        ModelFile(
            "model_optimized.onnx",
            94781076,
            "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
        ),
        ModelFile(
            "config.json", 739, "9088751d39abbf86ec3d19ffca92ad62ad19075f7e59712e6c71217fa125d1d3"
        ),
        ModelFile(
            "tokenizer.json",
            439125,
            "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26",
        ),
        ModelFile(
            "tokenizer_config.json",
            367,
            "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a",
        ),
        ModelFile(
            "special_tokens_map.json",
            125,
            "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
        ),
        ModelFile(
            "vocab.txt", 109540, "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c"
        ),
    ),
)
RERANKER_SPEC = ModelSpec(
    component="reranker",
    model_name="EnterpriseAgent/bge-reranker-base-quantized",
    repository="Xenova/bge-reranker-base",
    revision="280bcc27a84e0b898c251e06fddb25171bd9b101",
    model_file="onnx/model_quantized.onnx",
    files=(
        ModelFile(
            "onnx/model_quantized.onnx",
            279301077,
            "dd98f3e67837d23210a6b7550c08cced4f61845b940ac45be3565840a10f3244",
        ),
        ModelFile(
            "config.json", 782, "b6575b9d5be20d6747417c8e20c5a0db1636356e0b6d422d7244c628423c4d4c"
        ),
        ModelFile(
            "tokenizer.json",
            17098079,
            "48564c5c7d3fa64d85d95e65414a542385f88b0f128fd8d4163fd7a57f2be05c",
        ),
        ModelFile(
            "tokenizer_config.json",
            443,
            "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
        ),
        ModelFile(
            "special_tokens_map.json",
            279,
            "d5469a60db23249c7f8945013d78df30b44b6bf686c6bb4740f4223f77b1b535",
        ),
        ModelFile(
            "sentencepiece.bpe.model",
            5069051,
            "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
        ),
    ),
)
MODEL_SPECS = (EMBEDDING_SPEC, RERANKER_SPEC)


def model_metadata(spec: ModelSpec) -> dict[str, Any]:
    return {
        "model_name": spec.model_name,
        "repository": spec.repository,
        "revision": spec.revision,
        "model_file": spec.model_file,
        "license": "mit",
        "files": [
            {"path": file.path, "size_bytes": file.size_bytes, "sha256": file.sha256}
            for file in spec.files
        ],
    }


def _embedding_fingerprint() -> str:
    identity = {
        "model": model_metadata(EMBEDDING_SPEC),
        "dimension": EMBEDDING_DIMENSION,
        "query_instruction": QUERY_INSTRUCTION,
        "passage_instruction": "",
        "pooling": "cls",
        "normalization": "l2",
        "max_input_tokens": 512,
        "fastembed_version": FASTEMBED_VERSION,
        "onnxruntime_version": ORT_VERSION,
    }
    content = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "bge-zh-512-" + hashlib.sha256(content.encode("utf-8")).hexdigest()


EMBEDDING_FINGERPRINT = _embedding_fingerprint()


def expected_manifest() -> dict[str, Any]:
    """Public provenance used by both the prepare command and offline validation."""
    return {
        "schema_version": 1,
        "embedding_fingerprint": EMBEDDING_FINGERPRINT,
        "models": {spec.component: model_metadata(spec) for spec in MODEL_SPECS},
        "inference": {
            "embedding_dimension": EMBEDDING_DIMENSION,
            "embedding_query_instruction": QUERY_INSTRUCTION,
            "embedding_passage_instruction": "",
            "embedding_pooling": "cls",
            "embedding_normalization": "l2",
            "reranker_score_type": "sigmoid_raw_logit_uncalibrated",
            "provider": "CPUExecutionProvider",
            "fastembed_version": FASTEMBED_VERSION,
            "onnxruntime_version": ORT_VERSION,
        },
    }


def verified_model_file(path: Path, expected: ModelFile) -> bool:
    """Check actual file bytes against a checksum pinned in source, not user input."""
    if not path.is_file() or path.stat().st_size != expected.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == expected.sha256


def verify_model_assets(model_dir: Path) -> bool:
    """Read local assets only. This never imports inference SDKs or contacts HF."""
    root = model_dir.resolve()
    manifest_path = root / "manifest.json"
    try:
        if not manifest_path.is_file() or manifest_path.stat().st_size > 128_000:
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = expected_manifest()
        if not isinstance(manifest, dict) or any(
            manifest.get(key) != value for key, value in expected.items()
        ):
            return False
        for spec in MODEL_SPECS:
            for file in spec.files:
                path = root / spec.component / file.path
                if not path.resolve().is_relative_to(root) or not verified_model_file(path, file):
                    return False
        return True
    except (OSError, ValueError, TypeError):
        return False


class ModelRuntime:
    """One serialized CPU inference instance with a lazily loaded pair of models.

    Unchanged file-stat signatures cache a successful SHA verification. Any
    changed manifest or model file forces verification again before inference.
    The model directory is trusted operator configuration, not an upload target.
    """

    def __init__(self, settings: Settings):
        self.model_dir = Path(settings.rag_model_dir).resolve()
        self.threads = min(4, max(1, settings.rag_model_threads))
        self.fingerprint = EMBEDDING_FINGERPRINT
        self._lock = threading.RLock()
        self._verified_signature: tuple | None = None
        self._embedding: Any = None
        self._reranker: Any = None

    def _signature(self) -> tuple:
        paths = [self.model_dir / "manifest.json"] + [
            self.model_dir / spec.component / file.path
            for spec in MODEL_SPECS
            for file in spec.files
        ]
        if any(not path.resolve().is_relative_to(self.model_dir) for path in paths):
            raise ValueError("Model asset escapes the configured directory")
        return tuple((path.stat().st_size, path.stat().st_mtime_ns) for path in paths)

    def ready(self) -> bool:
        """Verify local manifest/checksums; no model load, directory creation or network."""
        with self._lock:
            try:
                signature = self._signature()
            except (OSError, ValueError):
                self._verified_signature = None
                self._embedding = self._reranker = None
                return False
            if signature == self._verified_signature:
                return True
            self._embedding = self._reranker = None
            if not verify_model_assets(self.model_dir):
                self._verified_signature = None
                return False
            self._verified_signature = signature
            return True

    def _require_assets(self) -> None:
        if not self.ready():
            raise BusinessError(
                "rag_models_unavailable", "知识检索模型尚未准备或校验失败，请先准备模型。", 503
            )

    def _load_models(self) -> None:
        self._require_assets()
        if self._embedding is not None and self._reranker is not None:
            return
        if version("fastembed") != FASTEMBED_VERSION or version("onnxruntime") != ORT_VERSION:
            raise RuntimeError("Inference SDK versions do not match the pinned model runtime")
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        with _REGISTRATION_LOCK:
            registered = TextCrossEncoder.list_supported_models()
            if not any(model["model"] == RERANKER_SPEC.model_name for model in registered):
                TextCrossEncoder.add_custom_model(
                    model=RERANKER_SPEC.model_name,
                    sources=ModelSource(hf=RERANKER_SPEC.repository),
                    model_file=RERANKER_SPEC.model_file,
                    description="BGE Chinese/English reranker, Xenova quantized ONNX export",
                    license="mit",
                    size_in_gb=0.279,
                )
        common = {
            "cache_dir": str(self.model_dir / "runtime-cache"),
            "threads": self.threads,
            "providers": ["CPUExecutionProvider"],
            "cuda": False,
            "local_files_only": True,
            "enable_cpu_mem_arena": False,
        }
        self._embedding = TextEmbedding(
            model_name=EMBEDDING_SPEC.model_name,
            specific_model_path=str(self.model_dir / EMBEDDING_SPEC.component),
            **common,
        )
        self._reranker = TextCrossEncoder(
            model_name=RERANKER_SPEC.model_name,
            specific_model_path=str(self.model_dir / RERANKER_SPEC.component),
            **common,
        )

    @staticmethod
    def _check_texts(texts: list[str]) -> None:
        if not isinstance(texts, list) or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise BusinessError("invalid_rag_text", "检索文本必须为非空字符串。", 422)

    @staticmethod
    def _inference_error(error: Exception) -> BusinessError:
        logger.warning(
            "Local model inference failed",
            extra={"event": "rag_model_failure", "exception_type": type(error).__name__},
        )
        return BusinessError("rag_inference_unavailable", "本地检索模型暂不可用。", 503)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return 512-dimensional unit vectors; passages have no query instruction."""
        self._check_texts(texts)
        if not texts:
            return []
        with self._lock:
            try:
                self._load_models()
                vectors = [
                    [float(value) for value in vector]
                    for vector in self._embedding.passage_embed(texts, batch_size=8, parallel=None)
                ]
                if len(vectors) != len(texts) or any(
                    len(vector) != EMBEDDING_DIMENSION
                    or not all(math.isfinite(value) for value in vector)
                    or not any(vector)
                    for vector in vectors
                ):
                    raise ValueError("Invalid embedding output")
                return vectors
            except BusinessError:
                raise
            except Exception as error:
                raise self._inference_error(error) from None

    def embed_query(self, query: str) -> list[float]:
        """Apply the BGE Chinese retrieval instruction only to the query."""
        self._check_texts([query])
        return self.embed_documents([QUERY_INSTRUCTION + query])[0]

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Return sigmoid(raw logit) in [0,1], input order; not calibrated confidence."""
        self._check_texts([query])
        self._check_texts(texts)
        if not texts:
            return []
        with self._lock:
            try:
                self._load_models()
                scores = [
                    float(score) for score in self._reranker.rerank(query, texts, batch_size=4)
                ]
                if len(scores) != len(texts) or not all(math.isfinite(score) for score in scores):
                    raise ValueError("Invalid reranker output")
                return [
                    1 / (1 + math.exp(-score))
                    if score >= 0
                    else math.exp(score) / (1 + math.exp(score))
                    for score in scores
                ]
            except BusinessError:
                raise
            except Exception as error:
                raise self._inference_error(error) from None
