"""Opt-in local API smoke run; no provider keys, direct provider requests or writes."""

import argparse
import csv
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from enterprise_agent.schemas.agent import TaskResponse
from enterprise_agent.schemas.analytics_report import AnalyticsReport
from enterprise_agent.services.analytics import AnalyticsService

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "datasets" / "analytics-live-smoke.json"
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def loopback_origin(value):
    try:
        address = urlsplit(value)
        port = address.port
        if (
            value != value.strip()
            or "?" in value
            or "#" in value
            or address.scheme != "http"
            or address.hostname not in {"127.0.0.1", "localhost"}
            or address.username is not None
            or address.password is not None
            or address.path not in {"", "/"}
            or (port is not None and port < 1)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Only a plain loopback HTTP origin is accepted") from None
    return value.rstrip("/")


def api(client, method, path, *, status=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != status:
        raise RuntimeError(f"local_api_http_{response.status_code}")
    return response.json()


def windows(catalog):
    today = date.fromisoformat(catalog["today"])
    if catalog["business_timezone"] != "Asia/Shanghai":
        raise ValueError("Unexpected business timezone")
    return {
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "seven_start": (today - timedelta(days=6)).isoformat(),
        "month_start": today.replace(day=1).isoformat(),
        "previous_start": (today - timedelta(days=14)).isoformat(),
        "latest_start": (today - timedelta(days=7)).isoformat(),
    }


def equal_cell(actual, expected, numeric):
    if actual is None or expected is None:
        return actual is None and expected is None
    if type(actual) is bool or type(expected) is bool:
        return False
    if not numeric:
        return type(actual) is type(expected) and actual == expected
    if type(actual) not in (int, str) or type(expected) not in (int, str):
        return False
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected))
        return left.is_finite() and right.is_finite() and left == right
    except InvalidOperation:
        return False


def validate(case, task, reference):
    if not isinstance(task, dict) or not isinstance(task.get("result"), dict):
        return False, "invalid_task_shape"
    result = task["result"]
    if result.get("approval") is not None or result.get("refund_result") is not None:
        return False, "unexpected_business_write_path"
    if task.get("status") != "completed" or task.get("error_code") is not None:
        return False, task.get("error_code") or "task_not_completed"
    if task.get("task_type") != "analytics":
        return False, "analytics_route_required"
    try:
        analysis = AnalyticsReport.model_validate(result.get("analysis"))
        baseline = AnalyticsReport.model_validate(reference)
    except ValidationError:
        return False, "invalid_analysis_or_reference_shape"
    query, expected = analysis.query, baseline.query
    if analysis.metric_ids != case["metric_ids"] or baseline.metric_ids != case["metric_ids"]:
        return False, "metric_contract_mismatch"
    if query.columns != case["columns"] or expected.columns != case["columns"]:
        return False, "column_contract_mismatch"
    if query.truncated or expected.truncated or query.row_count != expected.row_count:
        return False, "row_count_or_truncation_mismatch"
    for row, expected_row in zip(query.rows, expected.rows, strict=True):
        if any(
            not equal_cell(value, expected_value, column in case["numeric_columns"])
            for column, value, expected_value in zip(query.columns, row, expected_row, strict=True)
        ):
            return False, "ordered_exact_decimal_rows_mismatch"
    rendered = AnalyticsService(None).render(query, analysis.metric_ids)
    if analysis.model_dump(mode="json") != rendered or result.get("answer") != analysis.answer:
        return False, "report_chart_or_definitions_not_grounded"
    facts = result.get("facts")
    if (
        not isinstance(facts, list)
        or len(facts) != 1
        or not isinstance(facts[0], dict)
        or facts[0].get("name") != "execute_analytics_sql"
        or facts[0].get("result") != query.model_dump(mode="json")
    ):
        return False, "sql_query_facts_mismatch"
    return True, "ordered_columns_and_rows_match_real_reader_reference"


def ledger(records):
    known, known_calls, incomplete = dict.fromkeys(TOKEN_KEYS, 0), 0, 0
    for record in records:
        usage = (record.get("task") or {}).get("token_usage")
        valid = isinstance(usage, dict)
        for key in TOKEN_KEYS:
            value = usage.get(key) if valid else None
            if type(value) is int and value >= 0:
                known[key] += value
        calls = usage.get("model_calls") if valid else None
        if type(calls) is int and calls >= 0:
            known_calls += calls
        if record["executed"] and (
            not valid
            or usage.get("usage_complete") is not True
            or usage.get("unknown_usage_calls", 0)
            or usage.get("interrupted_usage_unknown")
            or any(
                type(usage.get(key)) is not int or usage[key] < 0
                for key in (*TOKEN_KEYS, "model_calls")
            )
            or usage.get("total_tokens")
            != usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        ):
            incomplete += 1
    return {
        "known_provider_tokens": known,
        "known_model_call_attempts": known_calls,
        "tasks_with_unknown_or_incomplete_consumption": incomplete,
        "consumption_complete": incomplete == 0,
    }


def save(output, records, raw, started, model_label, catalog):
    report = {
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "four_schema_guided_development_smoke_cases_not_blind_or_statistical_evaluation",
        "transport": "loopback_api_only",
        "controlled_model_only": False,
        "provider_identity": "not_exposed_by_local_task_dto",
        "operator_supplied_model_label": model_label,
        "operator_settings": "not_exposed_by_api",
        "trusted_catalog": catalog,
        "reference_snapshot": "separate_readonly_api_requests_not_a_shared_database_snapshot",
        "date_drift_detected": any(row.get("date_drift") for row in records),
        "planned_cases": 4,
        "recorded_cases": len(records),
        "run_complete": len(records) == 4,
        "passed_cases": sum(row["passed"] for row in records),
        **ledger(records),
        "records": records,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "cases.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "id",
                "executed",
                "passed",
                "reason",
                "request_elapsed_ms",
                "model_calls",
                *TOKEN_KEYS,
            ],
        )
        writer.writeheader()
        for record in records:
            usage = (record.get("task") or {}).get("token_usage") or {}
            writer.writerow(
                {
                    **{key: record.get(key) for key in writer.fieldnames[:5]},
                    **{key: usage.get(key) for key in writer.fieldnames[5:]},
                }
            )
    passed, complete = report["passed_cases"], report["consumption_complete"]
    tokens, calls = report["known_provider_tokens"], report["known_model_call_attempts"]
    lines = [
        "# 运营分析在线冒烟记录",
        "",
        "四个手选、带输出合同的开发检查，不属于盲评，不可泛化为模型质量。",
        "通过 canonical 只读 API 查询逐列逐行对照；不同请求不共享数据库快照。",
        f"通过 {passed}/{len(records)}；消费完整：{complete}。",
        f"已知 Token：{tokens}；已知模型调用尝试：{calls}。",
        "未知消费保留为未知；跨业务日期时判定失败，需新日期重新执行。",
        "",
        "| 场景 | 已执行 | 通过 | 依据 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['id']} | {row['executed']} | {row['passed']} | {row['reason']} |"
        for row in records
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Saved {len(records)} cases; passed={passed}; consumption_complete={complete}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-live", action="store_true", help="Authorize configured local Agent provider calls"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "var" / "evals" / "analytics-live")
    parser.add_argument("--model-label", default="unspecified")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("--allow-live is required; no requests sent")
    try:
        origin = loopback_origin(args.base_url)
    except ValueError as error:
        parser.error(str(error))
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "var" / "evals") or any(
        (output / name).exists() for name in ("results.json", "cases.csv", "report.md")
    ):
        parser.error("Use a new output directory within the project's var/evals")
    raw = DATASET.read_bytes()
    cases, records, catalog, stopped = json.loads(raw)["cases"], [], None, None
    started = datetime.now(UTC).isoformat()
    with httpx.Client(
        base_url=origin, timeout=110, trust_env=False, follow_redirects=False
    ) as client:
        try:
            api(client, "GET", "/health/ready")
            login = api(
                client,
                "POST",
                "/api/v1/auth/login",
                json={"username": "demo_analyst", "password": "DemoOnly!2026"},
            )
            client.headers["Authorization"] = "Bearer " + login["access_token"]
            catalog = api(client, "GET", "/api/v1/analytics/catalog")
            bounds = windows(catalog)
        except Exception as error:
            stopped = f"preflight_failed_{type(error).__name__}"
        for case in cases:
            record = {
                "id": case["id"],
                "question": case["message"],
                "executed": False,
                "passed": False,
                "reason": stopped or "not_started",
                "task": None,
                "reference": None,
            }
            records.append(record)
            if stopped:
                continue
            try:
                sql = case["reference_sql"].format(**bounds)
                record["reference"] = api(
                    client,
                    "POST",
                    "/api/v1/analytics/query",
                    json={"sql": sql, "metric_ids": case["metric_ids"]},
                )
                record["reference_metadata"] = {
                    "canonical_sql": sql,
                    "metric_ids": case["metric_ids"],
                    "trusted_date": bounds["today"],
                }
                session = api(client, "POST", "/api/v1/agent/sessions", status=201, json={})
                record["session_id"], record["executed"] = session["id"], True
                began = perf_counter()
                task = api(
                    client,
                    "POST",
                    f"/api/v1/agent/sessions/{session['id']}/messages",
                    json={"message": case["message"]},
                )
                record["request_elapsed_ms"] = round((perf_counter() - began) * 1000, 3)
                record["task"] = TaskResponse.model_validate(task).model_dump(mode="json")
                record["passed"], record["reason"] = validate(
                    case, record["task"], record["reference"]
                )
                after = api(client, "GET", "/api/v1/analytics/catalog")
                record["date_drift"] = after["today"] != bounds["today"]
                if record["date_drift"]:
                    record.update(passed=False, reason="trusted_business_date_changed")
            except Exception as error:
                record["reason"] = f"local_verification_error_{type(error).__name__}"
            save(output, records, raw, started, args.model_label, catalog)
    save(output, records, raw, started, args.model_label, catalog)
    return 0 if all(row["passed"] for row in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
