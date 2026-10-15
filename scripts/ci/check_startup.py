"""Check non-root startup access without printing secrets or application logs."""

import json
import tempfile
from pathlib import Path

from enterprise_agent.deployment import load_settings, read_secret
from enterprise_agent.rag.backends import verify_model_assets

SETUP_SECRETS = (
    "postgres-superuser-password",
    "postgres-app-password",
    "postgres-analytics-password",
    "jwt-secret",
    "demo-password",
)


def check_startup(secret_dir=Path("/run/secrets")):
    report = {"secret_readable": {}, "configuration_valid": False}
    for name in SETUP_SECRETS:
        try:
            read_secret(secret_dir, name, minimum=8 if name == "demo-password" else 16)
            valid = True
        except Exception:
            valid = False
        report["secret_readable"][name] = valid
    try:
        settings = load_settings("setup", secret_dir=secret_dir)
    except Exception:
        report["status"] = "failed"
        return report
    report["configuration_valid"] = True
    report["pinned_models_verified"] = verify_model_assets(settings.rag_model_dir)
    try:
        with tempfile.TemporaryFile(dir=settings.data_dir):
            pass
        report["runtime_directory_writable"] = True
    except Exception:
        report["runtime_directory_writable"] = False
    report["status"] = (
        "passed"
        if all(report["secret_readable"].values())
        and report["pinned_models_verified"]
        and report["runtime_directory_writable"]
        else "failed"
    )
    return report


if __name__ == "__main__":
    result = check_startup()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "passed" else 1)
