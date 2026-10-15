"""Verify the local Compose HTTP stack without model calls or refund mutations."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:15173")
    parser.add_argument(
        "--output", type=Path, default=Path("var/compose/test-output/stack-smoke.json")
    )
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if (
        origin.scheme != "http"
        or origin.hostname not in {"127.0.0.1", "localhost", "::1"}
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
    ):
        parser.error("An explicit local HTTP origin is required")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model_calls": 0,
        "refund_mutations": 0,
        "checks": {},
        "status": "failed",
    }

    def request(path, *, data=None, token=None, status=200):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = json.dumps(data).encode() if data is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(args.origin + path, data=body, headers=headers)
        try:
            response = opener.open(req, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            if response.status != status:
                raise AssertionError("Unexpected HTTP status")
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AssertionError("HTTP response exceeds bounded size")
            return raw

    try:
        home = request("/")
        assert b'id="app"' in home and b"/assets/" in home
        assert hashlib.sha256(request("/orders")).digest() == hashlib.sha256(home).digest()
        report["checks"]["static_frontend_and_spa_route"] = True
        asset = re.search(rb'src="(/assets/[A-Za-z0-9_.-]+\.js)"', home)
        assert asset is not None and len(request(asset.group(1).decode("ascii"))) > 1000
        request("/assets/missing-enterpriseagent-ci.js", status=404)
        report["checks"]["compiled_javascript_asset_and_missing_asset_404"] = True
        readiness = json.loads(request("/health/ready"))
        assert readiness["version"] == "0.9.0" and readiness["phase"] == 9
        assert readiness["checks"] == dict.fromkeys(
            ("application", "database", "redis", "knowledge", "agent_checkpoint", "analytics"),
            "ok",
        )
        report["checks"]["all_six_dependencies_ready"] = True
        report["health"] = readiness
        request("/api/v1/orders", status=401)
        report["checks"]["anonymous_order_access_denied"] = True
        password = Path("var/compose/secrets/demo-password").read_text(encoding="utf-8").strip()

        def login(username):
            response = json.loads(
                request("/api/v1/auth/login", data={"username": username, "password": password})
            )
            assert response["user"]["username"] == username
            return response["access_token"]

        customer = login("demo_customer")
        orders = json.loads(request("/api/v1/orders?limit=5", token=customer))
        assert orders["total"] > 0 and orders["items"]
        first = orders["items"][0]
        order = json.loads(request("/api/v1/orders/" + first["order_no"], token=customer))
        assert order["id"] == first["id"]
        report["checks"]["authenticated_order_database_query"] = True
        other = login("demo_other")
        other_orders = json.loads(request("/api/v1/orders?limit=1", token=other))
        assert other_orders["items"]
        foreign = other_orders["items"][0]["order_no"]
        request("/api/v1/orders/" + foreign, token=customer, status=404)
        report["checks"]["foreign_customer_order_hidden"] = True
        request("/api/v1/analytics/catalog", token=customer, status=403)
        report["checks"]["customer_analytics_permission_denied"] = True
        analyst = login("demo_analyst")
        catalog = json.loads(request("/api/v1/analytics/catalog", token=analyst))
        assert catalog
        documents = json.loads(request("/api/v1/knowledge/documents", token=analyst))
        assert documents["total"] == 5
        assert all(item["status"] == "indexed" for item in documents["items"])
        report["checks"]["five_indexed_knowledge_documents"] = True
        report["checks"]["analyst_authenticated_catalog"] = True
        retrieved = json.loads(
            request(
                "/api/v1/knowledge/search",
                token=customer,
                data={
                    "query": "普通商品签收后几天可以无理由退货？",
                    "mode": "hybrid_rerank",
                    "top_k": 3,
                },
            )
        )
        assert retrieved["status"] == "evidence_found" and retrieved["citations"]
        assert retrieved["llm_calls"] == 0
        for citation in retrieved["citations"]:
            chunk = UUID(citation["chunk_id"])
            path = f"/api/v1/knowledge/citations/{chunk}"
            assert citation["citation_url"] == path
            source = json.loads(request(path, token=customer))
            assert source["text"] == citation["text"]
            assert source["document_id"] == citation["document_id"]
        report["checks"]["hybrid_rerank_and_canonical_citation"] = True
        query = json.loads(
            request(
                "/api/v1/analytics/query",
                token=analyst,
                data={
                    "sql": "SELECT SUM(gross_revenue_cents) AS sales_cents "
                    "FROM ea_analytics.daily_orders",
                    "metric_ids": ["paid_sales"],
                },
            )
        )
        assert query["query"]["row_count"] == 1 and query["query"]["rows"][0][0] is not None
        report["checks"]["read_only_analytics_sql_executed"] = True
        denied = json.loads(
            request(
                "/api/v1/analytics/query",
                token=analyst,
                data={
                    "sql": "SELECT password_hash FROM public.users",
                    "metric_ids": ["paid_sales"],
                },
                status=422,
            )
        )
        assert denied["error"]["code"] == "analytics_sql_rejected"
        report["checks"]["unauthorized_sql_dataset_rejected"] = True
        request("/health/live")
        report["checks"]["proxy_liveness"] = True
        report["status"] = "passed"
    except Exception as error:
        report["error_type"] = type(error).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "checks": report["checks"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
