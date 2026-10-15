#!/bin/sh
# Create local Compose credentials only; no existing .env or external API key is read.
set -eu
umask 077

for directory in secrets postgres redis chroma app test-output; do
    mkdir -p "var/compose/$directory"
done
for secret in postgres-superuser-password postgres-app-password postgres-test-password \
    postgres-analytics-password postgres-test-analytics-password jwt-secret demo-password; do
    path="var/compose/secrets/$secret"
    if [ ! -f "$path" ]; then
        openssl rand -hex 32 > "$path"
    fi
    if [ ! -s "$path" ]; then
        echo 'A required local Compose credential is empty.' >&2
        exit 1
    fi
    chmod 0644 "$path"
done
if [ ! -f var/compose/secrets/deepseek-api-key ]; then
    : > var/compose/secrets/deepseek-api-key
fi
chmod 0644 var/compose/secrets/deepseek-api-key
mkdir -p var/models
cat > var/compose/compose.env <<'EOF'
EA_FULL_LLM_ENABLED=false
EA_FULL_DOCS_ENABLED=false
EOF
# Bind-mounted output belongs to the API's unprivileged, fixed UID.
# Only these two explicit directories are changed; no recursive ownership operation.
docker run --rm --user 0 --entrypoint sh \
    --mount "type=bind,source=$(pwd)/var/compose/app,target=/app-output" \
    --mount "type=bind,source=$(pwd)/var/compose/test-output,target=/test-output" \
    python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 \
    -c 'chown 10001:10001 /app-output /test-output; chmod 0755 /app-output /test-output'
echo 'Local Compose directories and credentials prepared; model downloads remain explicit.'
