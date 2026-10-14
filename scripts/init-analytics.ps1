# Add or rotate only the two project analytics roles in an existing cluster.
# Run init-infra.ps1 first to mount their ignored secret files. No reset occurs.
. (Join-Path $PSScriptRoot 'common.ps1')
$dockerPath = Get-ProjectDocker
$credentialsPath = Join-Path $projectPath 'var\infra-secrets.env'
if (-not (Test-Path -LiteralPath $credentialsPath)) {
    throw 'Run scripts/init-infra.ps1 before initializing analytics roles.'
}
foreach ($name in @('postgres-analytics-password', 'postgres-test-analytics-password')) {
    $secretPath = Join-Path $projectPath ("var\infra\" + $name)
    if (-not (Test-Path -LiteralPath $secretPath)) {
        throw 'Run scripts/init-infra.ps1 to generate analytics secrets.'
    }
}

$sql = @'
\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE reader_role text; password_file text; parent_role text;
BEGIN
    FOREACH reader_role IN ARRAY ARRAY['ea_analytics_reader', 'ea_test_analytics_reader'] LOOP
        password_file := CASE reader_role
            WHEN 'ea_analytics_reader' THEN '/run/secrets/postgres_analytics_password'
            ELSE '/run/secrets/postgres_test_analytics_password' END;
        IF trim(pg_read_file(password_file)) !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'Analytics secret file has an invalid format';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = reader_role) THEN
            EXECUTE format('CREATE ROLE %I', reader_role);
        END IF;
        EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
            reader_role, trim(pg_read_file(password_file)));
        FOR parent_role IN SELECT parent.rolname FROM pg_auth_members memberships
            JOIN pg_roles parent ON parent.oid = memberships.roleid
            JOIN pg_roles member ON member.oid = memberships.member
            WHERE member.rolname = reader_role LOOP
            EXECUTE format('REVOKE %I FROM %I', parent_role, reader_role);
        END LOOP;
        EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', reader_role);
        EXECUTE format('ALTER ROLE %I SET statement_timeout = ''5000ms''', reader_role);
        EXECUTE format('ALTER ROLE %I SET lock_timeout = ''1000ms''', reader_role);
        EXECUTE format('ALTER ROLE %I SET idle_in_transaction_session_timeout = ''10000ms''', reader_role);
        EXECUTE format('ALTER ROLE %I SET search_path = ea_analytics, pg_catalog', reader_role);
    END LOOP;
END $$;
REVOKE CONNECT ON DATABASE enterprise_agent FROM PUBLIC;
REVOKE CONNECT ON DATABASE enterprise_agent_test FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE enterprise_agent FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE enterprise_agent_test FROM PUBLIC;
REVOKE ALL ON DATABASE enterprise_agent FROM ea_analytics_reader, ea_test_analytics_reader;
REVOKE ALL ON DATABASE enterprise_agent_test FROM ea_analytics_reader, ea_test_analytics_reader;
GRANT CONNECT ON DATABASE enterprise_agent TO ea_analytics_reader;
GRANT CONNECT ON DATABASE enterprise_agent_test TO ea_test_analytics_reader;
COMMIT;

\connect enterprise_agent
BEGIN;
REVOKE ALL ON SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ea_checkpoint') THEN
        REVOKE ALL ON SCHEMA ea_checkpoint FROM ea_analytics_reader, ea_test_analytics_reader;
        REVOKE ALL ON ALL TABLES IN SCHEMA ea_checkpoint FROM ea_analytics_reader, ea_test_analytics_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ea_analytics') THEN
        REVOKE ALL ON SCHEMA ea_analytics FROM ea_analytics_reader, ea_test_analytics_reader;
        REVOKE ALL ON ALL TABLES IN SCHEMA ea_analytics FROM ea_analytics_reader, ea_test_analytics_reader;
        GRANT USAGE ON SCHEMA ea_analytics TO ea_analytics_reader;
        IF to_regclass('ea_analytics.daily_orders') IS NOT NULL THEN
            GRANT SELECT ON TABLE ea_analytics.daily_orders TO ea_analytics_reader;
        END IF;
        IF to_regclass('ea_analytics.daily_product_sales') IS NOT NULL THEN
            GRANT SELECT ON TABLE ea_analytics.daily_product_sales TO ea_analytics_reader;
        END IF;
    END IF;
END $$;
COMMIT;

\connect enterprise_agent_test
BEGIN;
REVOKE ALL ON SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ea_analytics_reader, ea_test_analytics_reader;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ea_checkpoint') THEN
        REVOKE ALL ON SCHEMA ea_checkpoint FROM ea_analytics_reader, ea_test_analytics_reader;
        REVOKE ALL ON ALL TABLES IN SCHEMA ea_checkpoint FROM ea_analytics_reader, ea_test_analytics_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ea_analytics') THEN
        REVOKE ALL ON SCHEMA ea_analytics FROM ea_analytics_reader, ea_test_analytics_reader;
        REVOKE ALL ON ALL TABLES IN SCHEMA ea_analytics FROM ea_analytics_reader, ea_test_analytics_reader;
        GRANT USAGE ON SCHEMA ea_analytics TO ea_test_analytics_reader;
        IF to_regclass('ea_analytics.daily_orders') IS NOT NULL THEN
            GRANT SELECT ON TABLE ea_analytics.daily_orders TO ea_test_analytics_reader;
        END IF;
        IF to_regclass('ea_analytics.daily_product_sales') IS NOT NULL THEN
            GRANT SELECT ON TABLE ea_analytics.daily_product_sales TO ea_test_analytics_reader;
        END IF;
    END IF;
END $$;
COMMIT;
'@

Push-Location $projectPath
try {
    # SQL contains only secret-file paths; passwords never enter command args.
    $sql | & $dockerPath compose --env-file var/infra-secrets.env -f deploy/compose.yaml exec -T postgres psql --username ea_local_admin --dbname enterprise_agent --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Analytics role initialization failed; check local infrastructure.' }
    Write-Output 'Dedicated analytics roles initialized. Run scripts/migrate.ps1 for analytics views.'
} finally { Pop-Location }
