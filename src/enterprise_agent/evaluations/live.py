"""Frozen small-sample live evaluation through an isolated local test API.

No confirmation, recovery, direct business write, schema reset or cloud tracing.
Failures and missing observations stay in the denominator and the usage ledger.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_agent import __version__
from enterprise_agent.analytics.evaluation import _prepare_reference, _snapshot
from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.evaluations.boundaries import (
    run_boundary_evaluation,
    write_boundary_artifacts,
)
from enterprise_agent.evaluations.checks import evaluate_case
from enterprise_agent.evaluations.dataset import load_dataset, render_message
from enterprise_agent.evaluations.references import build_sql_reference
from enterprise_agent.models import User, UserRole
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.schemas.agent import TaskResponse
from enterprise_agent.security import Principal
from enterprise_agent.seed import seed_demo
from enterprise_agent.services.knowledge import KnowledgeService

DATASET = PROJECT_ROOT / "evals/datasets/agent-evaluation.json"
BOUNDARIES = PROJECT_ROOT / "evals/datasets/agent-boundaries.json"
DATA_DIR = PROJECT_ROOT / "var/evaluation-test"
PREFIX = "ea_phase8_eval"


def test_urls(settings):
    required = (
        (settings.test_database_url, "ea_test_app"),
        (settings.test_migration_database_url, "ea_local_admin"),
        (settings.test_analytics_database_url, "ea_test_analytics_reader"),
    )
    result = []
    for secret, role in required:
        if secret is None:
            raise ValueError("Explicit dedicated test database credentials are required")
        url = make_url(secret.get_secret_value())
        if (
            url.database != "enterprise_agent_test"
            or url.username != role
            or url.host not in {"127.0.0.1", "localhost"}
            or url.drivername != "postgresql+psycopg"
        ):
            raise ValueError("Evaluation refuses a non-test database, role, driver or host")
        result.append(secret.get_secret_value())
    return tuple(result)


def local_origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Evaluation accepts only a plain HTTP loopback origin")
    return value.rstrip("/")


def source_hashes():
    paths = [
        DATASET,
        BOUNDARIES,
        *sorted((PROJECT_ROOT / "src/enterprise_agent").rglob("*.py")),
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "requirements/base.lock",
        PROJECT_ROOT / "requirements/dev.lock",
    ]
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


async def prepare(settings, output):
    business_url, owner_url, _ = test_urls(settings)
    owner = create_async_engine(owner_url, hide_parameters=True, poolclass=NullPool)
    engine = create_async_engine(business_url, hide_parameters=True, poolclass=NullPool)
    runtime = None
    try:
        counts = await seed_demo(
            async_sessionmaker(owner),
            as_of=datetime(2026, 10, 12, tzinfo=UTC),
            seed=42,
            orders_count=500,
            demo_password="DemoOnly!2026",
        )
        configured = settings.model_copy(
            update={
                "app_env": "test",
                "data_dir": DATA_DIR,
                "rag_enabled": True,
                "rag_collection_prefix": PREFIX,
                "langsmith_enabled": False,
            }
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).where(User.username == "demo_admin"))
            if user is None or not user.is_active or user.role != UserRole.ADMIN:
                raise ValueError("An enabled synthetic administrator is required")
            actor = Principal(user.id, user.role)
        runtime = RagRuntime(configured)
        if not runtime.models.ready() or not await runtime.call(runtime.store.ready):
            raise ValueError("Verified local models and real Chroma are required")
        service = KnowledgeService(factory, configured, runtime)
        documents = await service.list_documents(actor, limit=100)
        indexed = []
        for document in documents.items:
            result = await service.reindex(actor, document.id)
            if result.status != "indexed":
                raise ValueError("A real knowledge index failed")
            indexed.append(
                {"id": str(result.id), "title": result.title, "chunks": result.chunk_count}
            )
        report = {
            "prepared_at_utc": datetime.now(UTC).isoformat(),
            "database": "enterprise_agent_test",
            "collection_prefix": PREFIX,
            "data_dir": "var/evaluation-test",
            "seed_base_date": "2026-10-12",
            "seed_counts_at_preparation": counts,
            "indexed_documents": indexed,
            "reset_performed": False,
            "provider_requests": 0,
            "scope": "Existing seed retained; data fingerprints collected before/after",
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "preparation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"prepared_documents": len(indexed), "provider_requests": 0}))
        return report
    finally:
        if runtime is not None:
            await runtime.close()
        await engine.dispose()
        await owner.dispose()


async def api(client, method, path, *, expected=200, **kwargs):
    # There is deliberately no generic mutation or retry mechanism here.
    if method != "GET" and not (
        method == "POST"
        and (
            path == "/api/v1/auth/login"
            or path == "/api/v1/agent/sessions"
            or path.endswith("/messages")
        )
    ):
        raise ValueError("Evaluator cannot confirm, recover or mutate business state")
    response = await client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise RuntimeError(f"Evaluation API returned HTTP {response.status_code}")
    return response.json()


async def business_snapshot(factory, bindings):
    async with factory() as session, session.begin():
        await _prepare_reference(session)
        refunds = (
            (
                await session.execute(
                    text("SELECT id FROM public.refund_requests ORDER BY id LIMIT 10001")
                )
            )
            .scalars()
            .all()
        )
        tickets = (
            (
                await session.execute(
                    text("SELECT id FROM public.support_tickets ORDER BY id LIMIT 10001")
                )
            )
            .scalars()
            .all()
        )
        orders = (
            await session.execute(
                text("SELECT order_no, status FROM public.orders ORDER BY order_no LIMIT 10001")
            )
        ).all()
        if max(len(refunds), len(tickets), len(orders)) > 10000:
            raise ValueError("Synthetic reference snapshot exceeds the bounded budget")
        selected = set(bindings.values())
        return {
            "refund_ids": [str(value) for value in refunds],
            "ticket_ids": [str(value) for value in tickets],
            "order_statuses": {no: status for no, status in orders if no in selected},
        }


async def discover_bindings(client, factory):
    own = await api(client, "GET", "/api/v1/orders?limit=100")
    if not own["items"]:
        raise ValueError("The synthetic customer has no owned orders")
    # Recent orders can be unpaid/cancelled and therefore have no logistics.
    # Prove the reference is available before spending any model requests.
    own_order_no = None
    for order in own["items"][:100]:
        logistics = await client.get(f"/api/v1/orders/{order['order_no']}/logistics")
        if logistics.status_code == 404:
            continue
        if logistics.status_code != 200:
            raise RuntimeError(f"Evaluation API returned HTTP {logistics.status_code}")
        own_order_no = order["order_no"]
        break
    if own_order_no is None:
        raise ValueError("No owned synthetic order has logistics within the first 100 orders")
    owned = {item["order_no"] for item in own["items"]}
    async with factory() as session, session.begin():
        await _prepare_reference(session)
        candidates = (
            (
                await session.execute(
                    text("SELECT order_no FROM public.orders ORDER BY order_no LIMIT 10001")
                )
            )
            .scalars()
            .all()
        )
    foreign = next((value for value in candidates if value not in owned), None)
    if foreign is None:
        raise ValueError("No distinct foreign synthetic order is available")
    # The API, rather than a fixture assumption, proves the denied reference.
    denial = await client.get(f"/api/v1/orders/{foreign}")
    if denial.status_code != 404:
        raise ValueError("Foreign-order reference was not rejected")
    bindings = {"own_order_no": own_order_no, "foreign_order_no": foreign}
    for order in own["items"]:
        eligibility = await api(
            client,
            "GET",
            f"/api/v1/orders/{order['order_no']}/refund-eligibility?reason=change_mind",
        )
        if eligibility["eligible"]:
            bindings["refundable_order_no"] = order["order_no"]
            break
    return bindings


async def collect_reference(case, client, bindings):
    name = case.expectations.reference_tool
    if case.expectations.approval_required:
        name = "get_refund_policy"
    if name is None:
        return {}
    key = "refundable_order_no" if name == "get_refund_policy" else "own_order_no"
    order_no = bindings[key]
    path = f"/api/v1/orders/{order_no}"
    suffix = {
        "get_order": "",
        "get_order_items": "/items",
        "get_logistics": "/logistics",
        "get_refund_policy": "/refund-eligibility?reason=change_mind",
    }.get(name)
    if suffix is None:
        raise ValueError("Unsupported predefined business reference")
    observed = await api(client, "GET", path + suffix)
    return {
        name: {"eligibility": observed, "knowledge": None}
        if name == "get_refund_policy"
        else observed
    }


async def assert_test_session(factory, session_id):
    """Prove the API's newly created session exists in the independently guarded test DB."""
    identifier = UUID(session_id)
    async with factory() as session, session.begin():
        await _prepare_reference(session)
        exists = await session.scalar(
            text("SELECT count(*) FROM public.agent_sessions WHERE id = :session_id"),
            {"session_id": identifier},
        )
        if exists != 1:
            raise ValueError("API session does not belong to the dedicated test database")


def tool_projections(task):
    result = task["result"]
    preview = any(step.get("tool") == "create_refund_request" for step in task["steps"])
    calls = []
    for fact in result.get("facts") or []:
        if fact.get("name") == "execute_analytics_sql":
            continue
        calls.append(
            {
                "name": fact["name"],
                "arguments": fact.get("arguments", {}),
                "origin": "workflow"
                if preview and fact["name"] == "get_refund_policy"
                else "public_projection",
            }
        )
    approval = result.get("approval")
    if approval:
        calls.append(
            {
                "name": "create_refund_request",
                "arguments": {
                    "order_no": approval["order_no"],
                    "reason": approval["reason"],
                    "notes": approval["notes"],
                },
                "origin": "public_projection",
            }
        )
    return calls


def summarize(records):
    ledger = {record["task"]["id"]: record["task"] for record in records if record.get("task")}
    counts = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "unknown_usage_calls": 0,
    }
    incomplete = []
    for identifier, task in ledger.items():
        usage = task["token_usage"]
        for key in counts:
            counts[key] += usage.get(key, 0)
        if usage.get("usage_complete") is not True or usage.get("interrupted_usage_unknown"):
            incomplete.append(identifier)
    unobserved = sum(
        record.get("message_attempted", False) and not record.get("task") for record in records
    )
    complete = not incomplete and not unobserved
    counts.update(
        unique_task_count=len(ledger),
        incomplete_task_ids=incomplete,
        messages_without_observed_task=unobserved,
        usage_complete=complete,
        actual_total_tokens=counts["total_tokens"] if complete and ledger else None,
        scope="Provider cumulative Task ledger, latest per unique Task ID; no fixture tokens",
    )
    splits = {}
    for split in ("development", "regression", "validation"):
        subset = [record for record in records if record["split"] == split]
        splits[split] = {
            "total": len(subset),
            **{
                outcome: sum(
                    record.get("evaluation", {}).get("outcome") == outcome for record in subset
                )
                for outcome in ("pass", "fail", "manual_review")
            },
            "not_executed_or_unobserved": sum(not record.get("evaluation") for record in subset),
        }
    checks = [item for record in records for item in record.get("evaluation", {}).get("checks", [])]
    return {
        "splits": splits,
        "provider_usage": counts,
        "check_counts": {
            status: sum(item["status"] == status for item in checks)
            for status in ("pass", "fail", "manual_review")
        },
    }


def write_live(report, output):
    report["summary"] = summarize(report["cases"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "cases.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "case_id",
                "split",
                "category",
                "outcome",
                "request_elapsed_ms",
                "known_total_tokens",
                "usage_complete",
            ],
        )
        writer.writeheader()
        for record in report["cases"]:
            usage = (record.get("task") or {}).get("token_usage", {})
            writer.writerow(
                {
                    "case_id": record["id"],
                    "split": record["split"],
                    "category": record["category"],
                    "outcome": record.get("evaluation", {}).get(
                        "outcome", "not_executed_or_unobserved"
                    ),
                    "request_elapsed_ms": record.get("request_elapsed_ms"),
                    "known_total_tokens": usage.get("total_tokens"),
                    "usage_complete": usage.get("usage_complete"),
                }
            )
    lines = [
        "# Phase 8 真实模型评估",
        "",
        "开发助手编写的小样本，首次运行前冻结；不是第三方盲评。"
        "语义与缺失原始参数证据保留 manual_review。",
        "",
        "| Split | Total | Pass | Fail | Manual review | Unobserved |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, stats in report["summary"]["splits"].items():
        lines.append(
            f"| {name} | {stats['total']} | {stats['pass']} | {stats['fail']} | "
            f"{stats['manual_review']} | {stats['not_executed_or_unobserved']} |"
        )
    usage = report["summary"]["provider_usage"]
    lines += [
        "",
        f"实际观测 {usage['unique_task_count']} 个唯一任务；"
        f"已知供应商 Token {usage['total_tokens']}，usage_complete={usage['usage_complete']}。",
        "",
        "| Case | Outcome | Failed / unreviewed checks |",
        "|---|---|---|",
    ]
    for record in report["cases"]:
        evaluation = record.get("evaluation", {})
        problems = ", ".join(
            item["id"] for item in evaluation.get("checks", []) if item["status"] != "pass"
        )
        lines.append(
            f"| {record['id']} | {evaluation.get('outcome', 'unobserved')} | "
            f"{problems or record.get('error_code', '')} |"
        )
    lines += [
        "",
        "响应耗时为当前机器的单次实际观察，不是性能基准。API会写会话、任务、审批预览和审计；评估不会确认退款，业务基表前后指纹另行核对。",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


async def run_live(settings, origin, dataset_path, output):
    _, owner_url, _ = test_urls(settings)
    origin = local_origin(origin)
    dataset = load_dataset(dataset_path)
    frozen = source_hashes()
    report = {
        "schema_version": 1,
        "phase": 8,
        "version": __version__,
        "evaluation_kind": "real_model_frozen_project_cases",
        "status": "running",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "dataset": dataset.model_dump(mode="json"),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "source_hashes_before": frozen,
        "cases": [],
        "cloud_tracing_enabled": False,
        "limitations": [
            "Assistant-written and corpus-aware, not an independently annotated blind benchmark",
            "Original model JSON is unavailable in public Task projection",
            "Quote relevance needs separate human adjudication; no automatic semantic PASS",
            "Conservative SQL AST checker does not recognize every equivalent correct query",
        ],
    }
    engine = create_async_engine(owner_url, hide_parameters=True, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clients = {
        actor: httpx.AsyncClient(
            base_url=origin, timeout=110, trust_env=False, follow_redirects=False
        )
        for actor in ("customer", "analyst")
    }
    try:
        info = await api(clients["customer"], "GET", "/")
        if info.get("phase") != 8 or info.get("version") != __version__:
            raise ValueError("The evaluation server is not the current Phase 8 build")
        await api(clients["customer"], "GET", "/health/ready")
        for actor, client in clients.items():
            login = await api(
                client,
                "POST",
                "/api/v1/auth/login",
                json={"username": "demo_" + actor, "password": "DemoOnly!2026"},
            )
            client.headers["Authorization"] = "Bearer " + login["access_token"]
        bindings = await discover_bindings(clients["customer"], factory)
        catalog = await api(clients["analyst"], "GET", "/api/v1/analytics/catalog")
        report.update(bindings=bindings, catalog=catalog, data_before=await _snapshot(factory))
        write_live(report, output)
        for case in dataset.cases:
            record = {
                "id": case.id,
                "split": case.split,
                "category": case.category,
                "message_attempted": False,
                "task": None,
                "error_code": None,
            }
            report["cases"].append(record)
            client = clients[case.actor]
            try:
                message = render_message(case, bindings)
                record["message"] = message
                before = await business_snapshot(factory, bindings)
                references = await collect_reference(case, client, bindings)
                session = await api(client, "POST", "/api/v1/agent/sessions", expected=201, json={})
                await assert_test_session(factory, session["id"])
                request_id = "phase8-" + str(uuid4())
                record.update(
                    session_id=session["id"],
                    request_id_sha256=hashlib.sha256(request_id.encode()).hexdigest(),
                )
                started = perf_counter()
                record["message_attempted"] = True
                try:
                    task = await api(
                        client,
                        "POST",
                        f"/api/v1/agent/sessions/{session['id']}/messages",
                        json={"message": message},
                        headers={"X-Request-ID": request_id},
                    )
                except Exception:
                    # Observe a possibly committed task; never resubmit or recover automatically.
                    page = await api(
                        client, "GET", f"/api/v1/agent/tasks?session_id={session['id']}&limit=1"
                    )
                    if not page["items"]:
                        raise RuntimeError("Submitted message has no observable task") from None
                    task = page["items"][0]
                    record["response_observed_after_request_error"] = True
                record["request_elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
                task = TaskResponse.model_validate(task).model_dump(mode="json")
                record["task"] = task
                write_live(report, output)  # Persist billing evidence before further checks.
                citations = {}
                for citation in task["result"].get("citations") or []:
                    try:
                        chunk = str(UUID(citation["chunk_id"]))
                    except (KeyError, TypeError, ValueError):
                        continue
                    response = await client.get(f"/api/v1/knowledge/citations/{chunk}")
                    if response.status_code == 200:
                        citations[chunk] = response.json()
                evidence = {
                    "bindings": bindings,
                    "business_before": before,
                    "business_after": await business_snapshot(factory, bindings),
                    "facts_reference": references,
                    "tool_calls": tool_projections(task),
                    "citations": citations,
                }
                analysis = task["result"].get("analysis")
                if case.expectations.sql is not None and analysis:
                    evidence["sql_reference"] = await build_sql_reference(
                        case.expectations.sql.reference_id, catalog, analysis, factory
                    )
                record["evidence"] = evidence
                record["evaluation"] = evaluate_case(case, task, evidence).model_dump(mode="json")
                after_catalog = await api(clients["analyst"], "GET", "/api/v1/analytics/catalog")
                record["trusted_date_stable"] = after_catalog["today"] == catalog["today"]
                if not record["trusted_date_stable"]:
                    record["evaluation"]["outcome"] = "fail"
                    record["evaluation"]["checks"].append(
                        {
                            "id": "business_date_drift",
                            "status": "fail",
                            "detail": "Trusted business date changed during execution",
                        }
                    )
            except Exception as error:
                record["error_code"] = "evaluation_local_error_" + type(error).__name__
            write_live(report, output)
            print(
                json.dumps(
                    {
                        "case": case.id,
                        "outcome": record.get("evaluation", {}).get("outcome", "unobserved"),
                        "error_code": record["error_code"],
                    }
                )
            )
        report["data_after"] = await _snapshot(factory)
        report["data_stable"] = (
            report["data_before"]["data_fingerprint_sha256"]
            == report["data_after"]["data_fingerprint_sha256"]
        )
        report["source_hashes_after"] = source_hashes()
        report["source_stable"] = frozen == report["source_hashes_after"]
        report["status"] = "completed_with_explicit_check_results"
    except Exception as error:
        report.update(
            status="infrastructure_failed",
            error_code="evaluation_preflight_" + type(error).__name__,
        )
    finally:
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_live(report, output)
        for client in clients.values():
            await client.aclose()
        await engine.dispose()
    return report


async def main_async(args):
    settings = Settings()
    output = args.output.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "var"):
        raise ValueError("Raw evaluation artifacts must stay in the ignored project var directory")
    if args.mode == "boundaries":
        result = await run_boundary_evaluation(BOUNDARIES)
        write_boundary_artifacts(result, output)
        return 0 if result["summary"]["failed"] == 0 else 1
    if args.mode == "prepare":
        await prepare(settings, output)
        return 0
    if not args.allow_live:
        raise ValueError("--allow-live is required before any provider evaluation")
    result = await run_live(settings, args.base_url, DATASET, output)
    return 0 if result["status"] != "infrastructure_failed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("boundaries", "prepare", "live"), default="boundaries")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "var/evaluation/phase8/boundaries"
    )
    args = parser.parse_args()
    if args.mode == "live" and not args.allow_live:
        parser.error("--allow-live is required; no requests have been sent")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        return asyncio.run(main_async(args))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
