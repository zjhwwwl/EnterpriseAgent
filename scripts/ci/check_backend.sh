#!/bin/sh
# Called by the isolated Compose tests service, never by the ordinary API.
set -eu

cd /app
export EA_LLM_ENABLED=false
export EA_LANGSMITH_ENABLED=false
unset EA_DEEPSEEK_API_KEY EA_LANGSMITH_API_KEY
mkdir -p var/test-results var/evaluation/phase9 var/tmp
# Pytest's model-integrity check needs a private 397 MB copy. /tmp is a small
# runtime tmpfs; this path is the dedicated D-backed output mount on Windows.
available_kib=$(df -Pk var/tmp | awk 'NR == 2 { print $4 }')
case "$available_kib" in
    ''|*[!0-9]*)
        echo 'Unable to determine available test-output disk space.' >&2
        exit 1
        ;;
esac
if [ "$available_kib" -lt 2097152 ]; then
    echo 'At least 2 GiB free space is required for this isolated test run.' >&2
    exit 1
fi
CI_RUN_DIR=$(mktemp -d "$PWD/var/tmp/ci-run.XXXXXXXX")
# Docker Desktop's NTFS bind mount does not support pytest's unnamed capture
# files reliably. Keep those on Linux tmpfs, and move large named fixtures to disk.
export TMPDIR=/tmp
export RUFF_CACHE_DIR="$CI_RUN_DIR/ruff"

python -m ruff check .
python -m ruff format --check .
python -m pip check
python scripts/prepare-rag-models.py --verify-only
python -m pytest --require-postgres --require-rag -o cache_dir=var/.pytest_cache \
    --basetemp="$CI_RUN_DIR/pytest" \
    --junitxml=var/test-results/phase9-linux.xml
python -m enterprise_agent.evaluations.live --mode boundaries \
    --output var/evaluation/phase9/boundaries
python -m enterprise_agent.analytics.evaluation \
    --output var/evaluation/phase9/sql
