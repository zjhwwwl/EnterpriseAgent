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
        return {
            "status": "passed" if value.get("status") == "passed" else "failed",
            "stage": stage if isinstance(stage, str) and stage in STAGES else "unknown",
            **{
                name: value.get(name) if type(value.get(name)) is bool else None
                for name in ("database_ready", "rag_ready")
            },
        }
    except (OSError, ValueError, TypeError):
        return {"status": "unavailable"}


if __name__ == "__main__":
    print(json.dumps(read_setup_status(Path("var/compose/app/setup-status.json"))))
