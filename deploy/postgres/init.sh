#!/bin/sh
# Runs once for a new project cluster. Never resets an existing data directory.
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
SELECT trim(pg_read_file('/run/secrets/postgres_app_password')) AS app_password \gset
SELECT trim(pg_read_file('/run/secrets/postgres_test_password')) AS test_password \gset
SELECT trim(pg_read_file('/run/secrets/postgres_analytics_password')) AS analytics_password \gset
SELECT trim(pg_read_file('/run/secrets/postgres_test_analytics_password')) AS test_analytics_password \gset
CREATE ROLE ea_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ea_test_app LOGIN PASSWORD :'test_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ea_analytics_reader LOGIN PASSWORD :'analytics_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE ea_test_analytics_reader LOGIN PASSWORD :'test_analytics_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE ea_analytics_reader SET default_transaction_read_only = on;
ALTER ROLE ea_test_analytics_reader SET default_transaction_read_only = on;
ALTER ROLE ea_analytics_reader SET statement_timeout = '5000ms';
ALTER ROLE ea_test_analytics_reader SET statement_timeout = '5000ms';
ALTER ROLE ea_analytics_reader SET lock_timeout = '1000ms';
ALTER ROLE ea_test_analytics_reader SET lock_timeout = '1000ms';
ALTER ROLE ea_analytics_reader SET idle_in_transaction_session_timeout = '10000ms';
ALTER ROLE ea_test_analytics_reader SET idle_in_transaction_session_timeout = '10000ms';
ALTER ROLE ea_analytics_reader SET search_path = ea_analytics, pg_catalog;
ALTER ROLE ea_test_analytics_reader SET search_path = ea_analytics, pg_catalog;
CREATE DATABASE enterprise_agent_test OWNER ea_local_admin;
REVOKE CONNECT ON DATABASE enterprise_agent FROM PUBLIC;
REVOKE CONNECT ON DATABASE enterprise_agent_test FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE enterprise_agent FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE enterprise_agent_test FROM PUBLIC;
GRANT CONNECT ON DATABASE enterprise_agent TO ea_app;
GRANT CONNECT ON DATABASE enterprise_agent_test TO ea_test_app;
GRANT CONNECT ON DATABASE enterprise_agent TO ea_analytics_reader;
GRANT CONNECT ON DATABASE enterprise_agent_test TO ea_test_analytics_reader;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ea_app;

\connect enterprise_agent_test
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ea_test_app;
SQL
