"""Print fixed startup state fields only; never stream raw container logs."""

import json
from pathlib import Path

STAGES = {
    "resources",
    "migrations",
    "seed",
    "dependencies",
    "cpu_inference",
    "knowledge",
    "complete",
}
DATABASE_CHECKS = (
    "connection_opened",
    "query_completed",
    "role_safe",
    "schema_current",
    "privileges_bounded",
    "required_grants",
    "timed_out",
)


def read_setup_status(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        stage = value.get("stage")
        result = {
            "status": "passed" if value.get("status") == "passed" else "failed",
            "stage": stage if isinstance(stage, str) and stage in STAGES else "unknown",
            **{
                name: value.get(name) if type(value.get(name)) is bool else None
                for name in ("database_ready", "rag_ready")
            },
        }
        checks = value.get("database_checks")
        if isinstance(checks, dict):
            result["database_checks"] = {
                name: checks.get(name) if type(checks.get(name)) is bool else None
                for name in DATABASE_CHECKS
            }
        for name in ("database_initial_timeout", "database_retried"):
            if type(value.get(name)) is bool:
                result[name] = value[name]
        return result
    except (OSError, ValueError, TypeError):
        return {"status": "unavailable"}


if __name__ == "__main__":
    print(json.dumps(read_setup_status(Path("var/compose/app/setup-status.json"))))
