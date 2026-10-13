"""Real pinned Chinese ONNX inference; no substitute scores or online requests."""

import math
import os
import shutil
import socket
from pathlib import Path

import pytest

from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag.backends import EMBEDDING_DIMENSION, MODEL_SPECS, ModelRuntime

pytestmark = [pytest.mark.integration, pytest.mark.rag]
_MODEL_DIRECTORY = Path(os.environ.get("EA_RAG_MODEL_DIR", PROJECT_ROOT / "var" / "models"))


@pytest.fixture(scope="module")
def actual_models(request):
    settings = Settings(
        _env_file=None, rag_model_dir=_MODEL_DIRECTORY, rag_model_threads=2, rag_enabled=True
    )
    runtime = ModelRuntime(settings)
    if not runtime.ready():
        if request.config.getoption("require_rag", default=False):
            pytest.fail(
                "Pinned RAG models must be explicitly prepared for required real-model checks"
            )
        pytest.skip("Pinned local Chinese ONNX models are not prepared")
    return runtime


def test_missing_model_assets_are_503_without_download_or_directory_creation(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Model readiness must never contact the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    directory = tmp_path / "unprepared"
    runtime = ModelRuntime(Settings(_env_file=None, rag_model_dir=directory, rag_model_threads=2))
    assert not runtime.ready() and not directory.exists()
    with pytest.raises(BusinessError) as raised:
        runtime.embed_query("退款申请期限")
    assert raised.value.status_code == 503 and raised.value.code == "rag_models_unavailable"
    assert not directory.exists()


def test_chinese_embedding_and_quantized_reranker_run_offline_on_cpu(actual_models, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Prepared model inference must remain offline")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    documents = [
        "已签收订单可在签收七天内申请退款，优惠后商品货款可退，配送运费不退。",
        "会员积分可用于兑换会员活动奖励，积分账户展示会员权益。",
    ]
    query = "签收后几天内可以申请退款？"
    vectors = actual_models.embed_documents(documents)
    query_vector = actual_models.embed_query(query)
    assert len(vectors) == len(documents)
    assert len(query_vector) == EMBEDDING_DIMENSION
    for vector in vectors + [query_vector]:
        assert len(vector) == EMBEDDING_DIMENSION
        assert all(math.isfinite(value) for value in vector)
        assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1, abs_tol=1e-5)
    similarity = [
        sum(a * b for a, b in zip(query_vector, vector, strict=True)) for vector in vectors
    ]
    assert similarity[0] > similarity[1]
    scores = actual_models.rerank(query, documents)
    assert len(scores) == len(documents) and all(math.isfinite(score) for score in scores)
    assert all(0 <= score <= 1 for score in scores)
    assert scores[0] > scores[1]
    assert actual_models._embedding.model.model.get_providers() == ["CPUExecutionProvider"]
    assert actual_models._reranker.model.model.get_providers() == ["CPUExecutionProvider"]


def test_cached_model_verification_rejects_same_size_changed_artifact(actual_models, tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    shutil.copyfile(actual_models.model_dir / "manifest.json", directory / "manifest.json")
    changed_file = Path("embedding") / "tokenizer_config.json"
    for spec in MODEL_SPECS:
        for file in spec.files:
            relative = Path(spec.component) / file.path
            source = actual_models.model_dir / relative
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == changed_file:
                shutil.copyfile(source, target)
            else:
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
    runtime = ModelRuntime(Settings(_env_file=None, rag_model_dir=directory, rag_model_threads=2))
    assert runtime.ready()
    target = directory / changed_file
    previous = target.stat()
    data = bytearray(target.read_bytes())
    data[0] ^= 1
    target.write_bytes(data)
    os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
    assert target.stat().st_size == previous.st_size
    assert not runtime.ready()
    with pytest.raises(BusinessError) as raised:
        runtime.embed_query("退款申请期限")
    assert raised.value.status_code == 503
    assert actual_models.ready(), "The private test copy must not alter the source artifacts"
