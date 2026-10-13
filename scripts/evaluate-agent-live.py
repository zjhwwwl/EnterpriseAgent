"""Explicitly enabled local-API smoke run; never reads a provider key or calls it directly."""

import argparse
import csv
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "datasets" / "agent-smoke.json"


def response_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"local_api_http_{response.status_code}")
    return response.json()


def citation_checks(client, task):
    checks = []
    for source in task.get("result", {}).get("citations") or []:
        path = source.get("citation_url", "")
        if not re.fullmatch(r"/api/v1/knowledge/citations/[0-9a-f-]{36}", path):
            checks.append(False)
            continue
        actual = response_json(client, "GET", path)
        checks.append(
            all(
                actual.get(key) == source.get(key)
                for key in (
                    "chunk_id",
                    "document_id",
                    "document_title",
                    "document_version",
                    "index_generation",
                    "text",
                )
            )
        )
    return checks


def validate(case, task, reference, checks):
    result = task.get("result") or {}
    facts, sources = result.get("facts") or [], result.get("citations") or []
    if result.get("approval") is not None or result.get("refund_result") is not None:
        return False, "unexpected_business_write_path"
    if checks and not all(checks):
        return False, "citation_changed_or_invalid"
    kind = case["expectation"]
    if kind == "order_denied":
        leaked = any(
            isinstance(fact.get("result"), dict)
            and fact["result"].get("order_no") == case["order_no"]
            for fact in facts
        )
        passed = (
            task["status"] == "failed"
            and task.get("error_code") == "order_not_found"
            and not leaked
        )
        return passed, "authorized_order_boundary" if passed else "foreign_order_boundary_failed"
    if task["status"] != "completed" or task.get("error_code") is not None:
        return False, task.get("error_code") or "task_not_completed"
    if kind == "logistics":
        actual = [fact["result"] for fact in facts if fact.get("name") == "get_logistics"]
        passed = bool(actual) and all(item == reference for item in actual)
        return passed, "exact_real_api_facts" if passed else "logistics_facts_missing_or_different"
    if task.get("task_type") != "knowledge":
        return False, "knowledge_route_required_not_capability_rejection"
    if kind == "insufficient_evidence":
        searches = [fact["result"] for fact in facts if fact.get("name") == "search_knowledge"]
        rewrites = sum(step.get("node") == "query_rewrite" for step in task.get("steps", []))
        passed = (
            bool(searches)
            and not sources
            and rewrites <= 1
            and "无法确认" in result.get("answer", "")
        )
        if not passed:
            return False, "insufficient_evidence_not_demonstrated"
        return True, (
            "retrieval_insufficiency_confirmed"
            if all(item.get("status") == "insufficient_evidence" for item in searches)
            else "candidate_evidence_rejected_in_answer"
        )
    quotes = re.findall(r"(?:^|\n)- (.*?) \[来源：", result.get("answer", ""), re.DOTALL)
    grounded = (
        bool(sources)
        and len(quotes) == len(sources)
        and all(quote in source["text"] for quote, source in zip(quotes, sources, strict=True))
    )
    answered = bool(re.search(r"签收后\s*7\s*(?:日|天)", result.get("answer", "")))
    return (
        grounded and answered,
        "original_seven_day_policy" if grounded and answered else "policy_quote_or_answer_failed",
    )


def write_report(output, records, dataset_digest, started, model_label):
    known = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    unknown = 0
    for record in records:
        usage = record.get("token_usage")
        for key in known:
            if isinstance(usage, dict) and type(usage.get(key)) is int:
                known[key] += usage[key]
        if record["executed"] and (
            not isinstance(usage, dict)
            or usage.get("unknown_usage_calls", 0)
            or usage.get("interrupted_usage_unknown")
            or usage.get("usage_complete") is False
        ):
            unknown += 1
    report = {
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "dataset_sha256": dataset_digest,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "five_handpicked_smoke_cases_not_statistical_evaluation",
        "transport": "loopback_api_only",
        "controlled_model_only": False,
        "earlier_probe_included": False,
        "known_provider_tokens": known,
        "configured_model_label": model_label,
        "model_label_source": "operator_supplied_not_provider_response",
        "online_deepseek_tested": any(
            (record.get("token_usage") or {}).get("model_calls", 0) > 0 for record in records
        ),
        "tasks_with_unknown_or_incomplete_consumption": unknown,
        "passed_cases": sum(record["passed"] for record in records),
        "records": records,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    columns = [
        "id",
        "executed",
        "passed",
        "reason",
        "status",
        "elapsed_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "unknown_usage_calls",
    ]
    with (output / "cases.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key) for key in columns[:6]}
            row.update({key: (record.get("token_usage") or {}).get(key) for key in columns[6:]})
            writer.writerow(row)
    lines = [
        "# Agent 在线冒烟记录",
        "",
        "五个手选场景，不代表统计性模型评估。未合并此前单次探针。",
        "",
        f"通过 {report['passed_cases']} / {len(records)}；消费不完整的任务数：{unknown}。",
        (
            f"已知 Token：输入 {known['prompt_tokens']}，输出 {known['completion_tokens']}，"
            f"合计 {known['total_tokens']}。"
        ),
        "",
        "| 场景 | 已执行 | 通过 | 依据 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['id']} | {row['executed']} | {row['passed']} | {row['reason']} |"
        for row in records
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Saved {len(records)} smoke records; passed={report['passed_cases']}; "
        f"unknown_tasks={unknown}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Authorize the configured local Agent's paid provider calls",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "var" / "evals" / "agent-live")
    parser.add_argument(
        "--model-label",
        default="unspecified",
        help="Configured server model label; not a returned provider identifier",
    )
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("--allow-live is required; no requests have been sent")
    address = urlsplit(args.base_url)
    if (
        address.scheme != "http"
        or address.hostname not in {"127.0.0.1", "localhost"}
        or address.username
        or address.password
        or address.query
        or address.fragment
        or address.path not in {"", "/"}
    ):
        parser.error("Only a plain loopback HTTP origin is accepted")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "var"):
        parser.error("Reports must stay within this project's ignored var directory")
    raw = DATASET.read_bytes()
    cases = json.loads(raw)["cases"]
    records, sessions, stopped = [], {}, None
    started = datetime.now(UTC).isoformat()
    with httpx.Client(
        base_url=args.base_url, timeout=120, trust_env=False, follow_redirects=False
    ) as client:
        try:
            response_json(client, "GET", "/health/ready")
            login = response_json(
                client,
                "POST",
                "/api/v1/auth/login",
                json={
                    "username": "demo_customer",
                    "password": "DemoOnly!2026",
                },
            )
            client.headers["Authorization"] = "Bearer " + login["access_token"]
        except Exception as error:
            stopped = f"preflight_failed_{type(error).__name__}"
        for index, case in enumerate(cases):
            record = {
                "id": case["id"],
                "question": case["message"],
                "executed": False,
                "passed": False,
                "reason": stopped or "not_started",
            }
            records.append(record)
            if stopped:
                continue
            try:
                if case["session"] not in sessions:
                    created = client.post("/api/v1/agent/sessions", json={})
                    if created.status_code != 201:
                        raise RuntimeError("session_creation_failed")
                    sessions[case["session"]] = created.json()["id"]
                reference = (
                    response_json(client, "GET", f"/api/v1/orders/{case['order_no']}/logistics")
                    if case["expectation"] == "logistics"
                    else None
                )
                record["executed"] = True
                task = response_json(
                    client,
                    "POST",
                    f"/api/v1/agent/sessions/{sessions[case['session']]}/messages",
                    json={"message": case["message"]},
                )
                record.update(
                    {
                        key: task.get(key)
                        for key in (
                            "id",
                            "status",
                            "error_code",
                            "elapsed_ms",
                            "token_usage",
                            "steps",
                            "result",
                        )
                    }
                )
                record["id"], record["task_id"], record["session_id"] = (
                    case["id"],
                    task["id"],
                    task["session_id"],
                )
                checks = citation_checks(client, task)
                record["citation_checks"] = checks
                record["passed"], record["reason"] = validate(case, task, reference, checks)
                if index == 0 and not record["passed"] and task["status"] == "failed":
                    stopped = "not_executed_after_first_task_failure"
            except Exception as error:
                record["reason"] = f"local_verification_error_{type(error).__name__}"
                if index == 0:
                    stopped = "not_executed_after_first_case_infrastructure_failure"
    write_report(output, records, hashlib.sha256(raw).hexdigest(), started, args.model_label)


if __name__ == "__main__":
    main()
