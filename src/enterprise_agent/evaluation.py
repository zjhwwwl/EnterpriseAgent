"""Real, scoped RAG evaluation; it neither seeds data nor calls a generative model."""

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import mean
from time import perf_counter

from sqlalchemy import select

from enterprise_agent.config import PROJECT_ROOT, Settings, get_settings
from enterprise_agent.database import Database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.logging_config import configure_logging
from enterprise_agent.models import AuditLog, KnowledgeDocument, User
from enterprise_agent.rag.backends import expected_manifest
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.schemas.knowledge import SearchInput
from enterprise_agent.security import Principal
from enterprise_agent.services.knowledge import KnowledgeService

MODES = ("vector", "hybrid_rerank")
_METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")
_DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "rag.json"


def document_metrics(ranked_files: list[str], relevant_files: list[str]) -> dict:
    """Binary document relevance, deduplicated before computing rank positions."""
    ranked = list(dict.fromkeys(ranked_files))
    relevant = set(relevant_files)
    if not relevant:
        return dict.fromkeys(_METRICS)
    metrics = {f"recall_at_{k}": len(set(ranked[:k]) & relevant) / len(relevant) for k in (1, 3, 5)}
    first_hit = next((rank for rank, file in enumerate(ranked, 1) if file in relevant), None)
    metrics["mrr"] = 1.0 / first_hit if first_hit is not None else 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1) for rank, file in enumerate(ranked[:5], 1) if file in relevant
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), 5) + 1))
    metrics["ndcg_at_5"] = dcg / ideal
    return metrics


def percentile95(values: list[float]) -> float | None:
    """Nearest-rank percentile; retain the definition for small samples."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dataset(path: Path) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1 or not isinstance(data.get("corpus"), list):
        raise ValueError("Unsupported evaluation dataset schema")
    corpus = data["corpus"]
    if len(corpus) != 5:
        raise ValueError("This evaluation requires the fixed five-document corpus")
    files = {item["file"] for item in corpus}
    if len(files) != len(corpus) or any(
        not re.fullmatch(r"[a-z][a-z0-9-]*\.md", file) for file in files
    ):
        raise ValueError("Invalid evaluation corpus filenames")
    for item in corpus:
        expected = item["sha256"]
        source = PROJECT_ROOT / "data" / "knowledge" / item["file"]
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or _sha(source) != expected:
            raise ValueError(
                "Corpus source changed; update the dataset labels and checksum explicitly"
            )
    cases = data.get("cases", [])
    if (
        not cases
        or len({case["id"] for case in cases}) != len(cases)
        or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", case["id"]) for case in cases)
    ):
        raise ValueError("Evaluation query IDs must be nonempty and unique")
    if len({case["query"] for case in cases}) != len(cases):
        raise ValueError("Repeated queries cannot be shared across dev and test")
    if {case["split"] for case in cases} != {"dev", "test"}:
        raise ValueError("The evaluation dataset requires distinct dev and test cases")
    for case in cases:
        relevant = case["relevant_files"]
        if (
            case["split"] not in ("dev", "test")
            or type(case["answerable"]) is not bool
            or not isinstance(relevant, list)
            or len(set(relevant)) != len(relevant)
            or not set(relevant).issubset(files)
            or case["answerable"] != bool(relevant)
            or case["expected_status"]
            != ("evidence_found" if relevant else "insufficient_evidence")
        ):
            raise ValueError("Inconsistent evaluation case labels")
        SearchInput(query=case["query"])
    data["_dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    return data


def _git_metadata() -> dict:
    result = {"commit": None, "dirty": None}
    # Trust only this explicit workspace for these read-only child commands.
    git = ["git", "-c", f"safe.directory={PROJECT_ROOT.as_posix()}"]
    try:
        head = subprocess.run(
            [*git, "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        if head.returncode == 0 and re.fullmatch(r"[a-f0-9]{40}", head.stdout.strip()):
            result["commit"] = head.stdout.strip()
        changes = subprocess.run(
            [*git, "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        if changes.returncode == 0:
            result["dirty"] = bool(changes.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def _dependencies() -> dict:
    packages = [
        "enterprise-agent",
        "fastapi",
        "pydantic",
        "SQLAlchemy",
        "psycopg",
        "chromadb-client",
        "fastembed",
        "onnxruntime",
        "rank-bm25",
        "pypdf",
        "python-docx",
        "tokenizers",
    ]
    found = {}
    for name in packages:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


async def _corpus(factory, data: dict, runtime: RagRuntime) -> tuple[list[dict], dict]:
    entries = []
    file_by_id = {}
    async with factory() as session:
        for item in data["corpus"]:
            documents = list(
                await session.scalars(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.file_path == f"data/knowledge/{item['file']}",
                        KnowledgeDocument.deleted_at.is_(None),
                    )
                )
            )
            if len(documents) != 1:
                raise BusinessError("evaluation_corpus_missing", "评估固定语料未完整登记", 409)
            document = documents[0]
            if (
                document.visibility != "public"
                or document.checksum != item["sha256"]
                or document.status != "indexed"
                or document.index_generation <= 0
                or document.active_index_id is None
                or document.index_fingerprint != runtime.fingerprint
            ):
                raise BusinessError(
                    "evaluation_corpus_not_ready", "评估语料版本或索引与当前配置不一致", 409
                )
            entries.append(
                {
                    "file": item["file"],
                    "document_id": str(document.id),
                    "document_title": document.title,
                    "sha256": document.checksum,
                    "document_version": document.version,
                    "index_generation": document.index_generation,
                    "active_index_id": str(document.active_index_id),
                    "index_fingerprint": document.index_fingerprint,
                }
            )
            file_by_id[document.id] = item["file"]
    return entries, file_by_id


async def _check_citation(service, actor, citation, file_by_id):
    try:
        actual = await service.citation(actor, citation.chunk_id)
        fields = [
            "document_id",
            "document_title",
            "document_version",
            "index_generation",
            "chunk_id",
            "chunk_index",
            "text",
            "source_location",
            "citation_url",
        ]
        matches = all(getattr(actual, field) == getattr(citation, field) for field in fields)
        in_scope = citation.document_id in file_by_id
        return {
            "chunk_id": str(citation.chunk_id),
            "valid": matches and in_scope,
            "source_fields_match": matches,
            "in_evaluation_scope": in_scope,
            "error_code": None,
        }
    except Exception as error:
        return {
            "chunk_id": str(citation.chunk_id),
            "valid": False,
            "source_fields_match": False,
            "in_evaluation_scope": citation.document_id in file_by_id,
            "error_code": error.code
            if isinstance(error, BusinessError)
            else "citation_check_failed",
            "exception_type": type(error).__name__,
        }


async def _query(service, actor, case, mode, top_k, document_ids, file_by_id):
    started = perf_counter()
    record = {
        "case_id": case["id"],
        "split": case["split"],
        "query": case["query"],
        "mode": mode,
        "answerable": case["answerable"],
        "expected_status": case["expected_status"],
        "relevant_files": case["relevant_files"],
    }
    try:
        response = await service.search(
            actor,
            SearchInput(query=case["query"], mode=mode, top_k=top_k),
            apply_gate=False,
            document_ids=document_ids,
        )
        wall_ms = (perf_counter() - started) * 1000
        ranked_files = [
            file_by_id[citation.document_id]
            for citation in response.citations
            if citation.document_id in file_by_id
        ]
        ranked_files = list(dict.fromkeys(ranked_files))
        checks = [
            await _check_citation(service, actor, citation, file_by_id)
            for citation in response.citations
        ]
        record.update(
            {
                "outcome": "completed",
                "status": response.status,
                "gate_correct": response.status == case["expected_status"],
                "elapsed_ms": wall_ms,
                "service_elapsed_ms": response.elapsed_ms,
                "stage_ms": response.stage_ms,
                "ranked_files": ranked_files,
                "metrics": document_metrics(ranked_files, case["relevant_files"]),
                "citations": [citation.model_dump(mode="json") for citation in response.citations],
                "citation_checks": checks,
                "llm_calls": response.llm_calls,
                "llm_input_tokens": response.llm_input_tokens,
                "llm_output_tokens": response.llm_output_tokens,
            }
        )
    except Exception as error:
        record.update(
            {
                "outcome": "error",
                "status": "error",
                "gate_correct": False,
                "elapsed_ms": (perf_counter() - started) * 1000,
                "service_elapsed_ms": None,
                "stage_ms": {},
                "ranked_files": [],
                "metrics": document_metrics([], case["relevant_files"]),
                "citations": [],
                "citation_checks": [],
                "error_code": error.code
                if isinstance(error, BusinessError)
                else "evaluation_query_failed",
                "exception_type": type(error).__name__,
                "llm_calls": 0,
                "llm_input_tokens": 0,
                "llm_output_tokens": 0,
            }
        )
    return record


def summarize(records: list[dict]) -> dict:
    report = {}
    for split in ("dev", "test"):
        report[split] = {}
        for mode in MODES:
            rows = [row for row in records if row["split"] == split and row["mode"] == mode]
            positives = [row for row in rows if row["answerable"]]
            negatives = [row for row in rows if not row["answerable"]]
            citations = [check for row in rows for check in row["citation_checks"]]
            latencies = [row["elapsed_ms"] for row in rows]
            metrics = {
                key: mean(row["metrics"][key] for row in positives) if positives else None
                for key in _METRICS
            }
            metrics.update(
                {
                    "queries": len(rows),
                    "answerable_queries": len(positives),
                    "off_topic_queries": len(negatives),
                    "execution_success_rate": mean(row["outcome"] == "completed" for row in rows)
                    if rows
                    else None,
                    "errors": sum(row["outcome"] != "completed" for row in rows),
                    "answerable_gate_accept_rate": mean(
                        row["status"] == "evidence_found" for row in positives
                    )
                    if positives
                    else None,
                    "off_topic_rejection_rate": mean(
                        row["status"] == "insufficient_evidence" for row in negatives
                    )
                    if negatives
                    else None,
                    "gate_accuracy": mean(row["gate_correct"] for row in rows) if rows else None,
                    "checked_citations": len(citations),
                    "citation_validity_rate": mean(check["valid"] for check in citations)
                    if citations
                    else None,
                    "mean_latency_ms": mean(latencies) if latencies else None,
                    "p95_latency_ms": percentile95(latencies),
                    "llm_calls": sum(row["llm_calls"] for row in rows),
                    "llm_input_tokens": sum(row["llm_input_tokens"] for row in rows),
                    "llm_output_tokens": sum(row["llm_output_tokens"] for row in rows),
                    "embedding_provider_token_usage": None,
                    "reranker_provider_token_usage": None,
                }
            )
            report[split][mode] = metrics
    return report


def _json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _number(value, *, percentage=False):
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%" if percentage else f"{value:.3f}"


def _write_reports(output: Path, report: dict) -> None:
    _json(output / "results.json", report)
    metric_fields = [
        "split",
        "mode",
        "queries",
        "answerable_queries",
        "off_topic_queries",
        *_METRICS,
        "execution_success_rate",
        "answerable_gate_accept_rate",
        "off_topic_rejection_rate",
        "gate_accuracy",
        "citation_validity_rate",
        "checked_citations",
        "errors",
        "mean_latency_ms",
        "p95_latency_ms",
        "llm_calls",
        "llm_input_tokens",
        "llm_output_tokens",
        "embedding_provider_token_usage",
        "reranker_provider_token_usage",
    ]
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=metric_fields)
        writer.writeheader()
        for split, modes in report["summary"].items():
            for mode, metrics in modes.items():
                writer.writerow({"split": split, "mode": mode} | metrics)
    with (output / "queries.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = [
            "case_id",
            "split",
            "mode",
            "query",
            "answerable",
            "outcome",
            "status",
            "elapsed_ms",
            "gate_correct",
            *_METRICS,
            "ranked_files",
            "error_code",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["records"]:
            values = {key: row.get(key) for key in fields if key not in _METRICS}
            values["ranked_files"] = ";".join(row["ranked_files"])
            writer.writerow(values | row["metrics"])
    lines = [
        "# EnterpriseAgent Phase 3 实测检索评估",
        "",
        f"- 运行状态：{report['run_status']}",
        f"- 数据集：{report['metadata']['dataset_id']}",
        f"- UTC 时间：{report['metadata']['started_at']}",
        f"- Git：{report['metadata']['git']['commit']}；"
        f"工作区修改：{report['metadata']['git']['dirty']}",
        "- test 与 dev 分开呈现；开发者已接触自编数据，分组不等于盲评保留集。",
        "- 这是人工标注的小型模拟政策集；并非盲评，不能外推到真实企业或大型语料。",
        "- Recall/MRR/nDCG 为文档级二元相关性；先去重返回 Chunk 的文档 ID。",
        "- 原始检索不截断低分引用，门控状态仍由业务 Service 的真实阈值计算。",
        "- API 返回的前 top_k 个 Chunk 去重后可能不足五篇文档；未召回文档按未命中计。",
        "- 延迟包含真实 Service 检索及模型计算，不包含后续逐条引用校验；p95 用 nearest rank。",
        "- 每模式一次 warmup 的耗时单独保存在 metadata，不计入下表。",
        "- 执行器只进行知识检索；生成模型使用量来自真实 Service 返回记录。",
        "- 本地 Embedding/Reranker 不提供 API Token 计费统计，该值为 N/A。",
        "- 执行失败不会从指标分母中删除：可回答问题的检索分数为零，门控判定失败。",
        "",
    ]
    for split in ("test", "dev"):
        lines.extend(
            [
                f"## {split} {'主结果' if split == 'test' else '调参集'}",
                "",
                "| 模式 | 可回答/无证据 | R@1 | R@3 | R@5 | MRR | nDCG@5 | "
                "无证据拒绝率 | 引用有效率 | 平均/P95 ms | 错误 |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for mode, metrics in report["summary"][split].items():
            lines.append(
                f"| {mode} | {metrics['answerable_queries']}/{metrics['off_topic_queries']} "
                f"| {_number(metrics['recall_at_1'])} | {_number(metrics['recall_at_3'])} "
                f"| {_number(metrics['recall_at_5'])} | {_number(metrics['mrr'])} "
                f"| {_number(metrics['ndcg_at_5'])} "
                f"| {_number(metrics['off_topic_rejection_rate'], percentage=True)} "
                f"| {_number(metrics['citation_validity_rate'], percentage=True)} "
                f"| {_number(metrics['mean_latency_ms'])}/{_number(metrics['p95_latency_ms'])} "
                f"| {metrics['errors']} |"
            )
        lines.append("")
    lines.extend(
        [
            f"生成模型记录合计：调用 {sum(row['llm_calls'] for row in report['records'])} 次，"
            f"输入 Token {sum(row['llm_input_tokens'] for row in report['records'])}，"
            f"输出 Token {sum(row['llm_output_tokens'] for row in report['records'])}。",
            "",
        ]
    )
    lines.extend(
        [
            "## 结果解释",
            "",
            "两种配置在同一固定语料、Embedding、top_k 和候选上限下对比。"
            " 混合检索与 Reranker 是否改善指标，以表中的实际变化为准；没有保证优化一定有效。",
            "引用有效率只证明原文、版本、位置及权限校验匹配，"
            "不能证明引用语义足以回答问题。"
            " 可回答问题的相关文档标签用于检索评估；未评估生成答案忠实度、"
            "Agent 任务完成率或业务工具正确性。",
            "详见 results.json 中的完整排序、stage_ms、引用检查和元数据，"
            "以及 queries.csv 的逐查询结果。",
            "",
        ]
    )
    if report.get("corpus_changed_during_run"):
        lines.extend(["**运行期间语料发生变更，本次比较无效，必须在固定版本下重跑。**", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


async def run_evaluation(
    settings: Settings, *, dataset_path: Path, output: Path, top_k: int
) -> bool:
    data = _dataset(dataset_path)
    if settings.database_url is None or not settings.rag_enabled:
        raise BusinessError("evaluation_not_configured", "请配置普通业务数据库并启用 RAG", 503)
    configure_logging(settings.log_level, secret_values=settings.secret_values())
    database = Database(settings.database_url.get_secret_value())
    runtime = RagRuntime(settings)
    try:
        if not await database.is_ready() or not await runtime.ready():
            raise BusinessError(
                "evaluation_dependency_not_ready", "真实数据库或模型/向量服务未就绪", 503
            )
        corpus, file_by_id = await _corpus(database.session_factory, data, runtime)
        async with database.session_factory() as session:
            seed_audit = await session.scalar(
                select(AuditLog)
                .where(AuditLog.action == "seed.import")
                .order_by(AuditLog.id.desc())
            )
            user = await session.scalar(
                select(User).where(User.username == "demo_customer", User.is_active.is_(True))
            )
            if user is None:
                raise BusinessError("evaluation_actor_missing", "请先初始化模拟客户账号", 409)
            actor = Principal(user_id=user.id, role=user.role)
        service = KnowledgeService(database.session_factory, settings, runtime)
        source_files = [
            "src/enterprise_agent/evaluation.py",
            "src/enterprise_agent/services/knowledge.py",
            "src/enterprise_agent/rag/backends.py",
            "src/enterprise_agent/rag/ranking.py",
            "src/enterprise_agent/rag/runtime.py",
            "src/enterprise_agent/rag/store.py",
            "src/enterprise_agent/repositories/knowledge.py",
            "src/enterprise_agent/config.py",
            "src/enterprise_agent/rag/parsing.py",
            "src/enterprise_agent/rag/chunking.py",
        ]
        manifest = settings.rag_model_dir / "manifest.json"
        metadata = {
            "dataset_id": data["dataset_id"],
            "dataset_sha256": data["_dataset_sha256"],
            "started_at": datetime.now(UTC).isoformat(),
            "dataset_seed_anchor_utc": data["seed_as_of_utc"],
            "database_seed_as_of_utc": seed_audit.details.get("as_of") if seed_audit else None,
            "database_seed_audit_id": seed_audit.id if seed_audit else None,
            "git": _git_metadata(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "dependencies": _dependencies(),
            "source_sha256": {file: _sha(PROJECT_ROOT / file) for file in source_files},
            "models": expected_manifest(),
            "model_manifest_sha256": _sha(manifest),
            "model_fingerprint": runtime.models.fingerprint,
            "index_fingerprint": runtime.fingerprint,
            "assets_verified": True,
            "corpus": corpus,
            "configuration": {
                "chunk_size": settings.rag_chunk_size,
                "chunk_overlap": settings.rag_chunk_overlap,
                "top_k_chunks": top_k,
                "vector_candidates": 30,
                "bm25_candidates": 30,
                "rrf_candidates": 30,
                "rrf_k": 60,
                "vector_gate_min_similarity": settings.rag_min_vector_similarity,
                "rerank_gate_min_sigmoid_score": settings.rag_min_rerank_score,
                "gate_rule": (
                    "vector floor for every mode; rerank additionally requires sigmoid floor"
                ),
                "model_threads_configured": settings.rag_model_threads,
                "model_threads_actual": runtime.models.threads,
                "inference_timeout_seconds": settings.rag_timeout_seconds,
            },
            "warmup": [],
            "query_order": "dataset order; mode order alternates per query",
            "metric_scope": "document relevance after deduplicating top_k returned chunks",
            "token_usage_scope": {
                "llm": "no generative model calls",
                "embedding": "local inference; provider API usage not applicable",
                "reranker": "local inference; provider API usage not applicable",
            },
        }
        output.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "run_status": "running", "metadata": metadata, "records": []}
        _json(output / "results.json", report)
        document_ids = list(file_by_id)
        try:
            first_query = next(case["query"] for case in data["cases"] if case["split"] == "dev")
            for mode in MODES:
                started = perf_counter()
                await service.search(
                    actor,
                    SearchInput(query=first_query, mode=mode, top_k=top_k),
                    apply_gate=False,
                    document_ids=document_ids,
                )
                metadata["warmup"].append(
                    {
                        "mode": mode,
                        "query": first_query,
                        "elapsed_ms": (perf_counter() - started) * 1000,
                    }
                )
            for index, case in enumerate(data["cases"]):
                order = MODES if index % 2 == 0 else MODES[::-1]
                for mode in order:
                    record = await _query(
                        service, actor, case, mode, top_k, document_ids, file_by_id
                    )
                    report["records"].append(record)
                    _json(output / "results.json", report)
                    print(
                        f"Evaluation {len(report['records'])}/{len(data['cases']) * len(MODES)} "
                        f"{case['id']} {mode}: {record['outcome']}",
                        flush=True,
                    )
            final_corpus, _ = await _corpus(database.session_factory, data, runtime)
            report["corpus_changed_during_run"] = final_corpus != corpus
            metadata["model_manifest_sha256_after"] = _sha(manifest)
            manifest_changed = (
                metadata["model_manifest_sha256_after"] != metadata["model_manifest_sha256"]
            )
            metadata["dataset_sha256_after"] = _sha(dataset_path)
            metadata["source_sha256_after"] = {
                file: _sha(PROJECT_ROOT / file) for file in source_files
            }
            inputs_changed = (
                metadata["dataset_sha256_after"] != metadata["dataset_sha256"]
                or metadata["source_sha256_after"] != metadata["source_sha256"]
            )
            problems = any(
                row["outcome"] == "error"
                or any(not check["valid"] for check in row["citation_checks"])
                or row["llm_calls"]
                or row["llm_input_tokens"]
                or row["llm_output_tokens"]
                for row in report["records"]
            )
            report["run_status"] = (
                "invalidated"
                if report["corpus_changed_during_run"] or manifest_changed or inputs_changed
                else "completed_with_failures"
                if problems
                else "complete"
            )
            report["summary"] = summarize(report["records"])
            metadata["finished_at"] = datetime.now(UTC).isoformat()
            _write_reports(output, report)
            return report["run_status"] == "complete"
        except Exception as error:
            report["run_status"] = "aborted"
            report["fatal_error"] = {
                "code": error.code if isinstance(error, BusinessError) else "evaluation_failed",
                "exception_type": type(error).__name__,
            }
            report["summary"] = summarize(report["records"])
            metadata["finished_at"] = datetime.now(UTC).isoformat()
            _write_reports(output, report)
            return False
    finally:
        await runtime.close()
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real scoped Phase 3 RAG comparison")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "var" / "evaluation" / "phase3"
    )
    parser.add_argument("--top-k", type=int, choices=range(5, 11), default=5)
    args = parser.parse_args()
    try:
        settings = get_settings()
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            succeeded = runner.run(
                run_evaluation(
                    settings,
                    dataset_path=args.dataset.resolve(),
                    output=args.output.resolve(),
                    top_k=args.top_k,
                )
            )
    except BusinessError as error:
        parser.exit(1, f"Evaluation refused: {error.code}\n")
    except Exception as error:
        # Do not print driver messages, environment values or password-bearing URLs.
        parser.exit(
            1, f"Evaluation refused: {type(error).__name__}; check configuration and fixed corpus\n"
        )
    print(f"Evaluation report: {args.output.resolve() / 'report.md'}", flush=True)
    if not succeeded:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
