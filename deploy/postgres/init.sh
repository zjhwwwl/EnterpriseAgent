#!/bin/sh
# Runs once for a new project cluster. Never resets an existing data directory.
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
SELECT trim(pg_read_file('/run/secrets/postgres_app_password')) AS app_password \gset
SELECT trim(pg_read_file('/run/secrets/postgres_test_password')) AS test_password \gset
CREATE ROLE ea_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ea_test_app LOGIN PASSWORD :'test_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE enterprise_agent_test OWNER ea_local_admin;
REVOKE CONNECT ON DATABASE enterprise_agent FROM PUBLIC;
REVOKE CONNECT ON DATABASE enterprise_agent_test FROM PUBLIC;
GRANT CONNECT ON DATABASE enterprise_agent TO ea_app;
GRANT CONNECT ON DATABASE enterprise_agent_test TO ea_test_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ea_app;

\connect enterprise_agent_test
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ea_test_app;
SQL
