"""Execute the real init-script expressions with newline-terminated secret fixtures."""

import re

import pytest
from sqlalchemy import text

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.deployment import read_secret

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
INITIALIZATION_SQL = (PROJECT_ROOT / "deploy/postgres/init.sh").read_text(encoding="utf-8")
PASSWORD_EXPRESSIONS = re.findall(
    r"^(SELECT .*pg_read_file\('/run/secrets/([^']+)'\).*?)\s+\\gset$",
    INITIALIZATION_SQL,
    flags=re.MULTILINE,
)


@pytest.mark.parametrize("sql,filename", PASSWORD_EXPRESSIONS)
@pytest.mark.parametrize("prefix,suffix", [("", ""), ("", "\n"), ("", "\r\n"), (" \t", "\r\n ")])
async def test_database_secret_normalization_matches_application_reader(
    sql, filename, prefix, suffix, tmp_path, pg_factory
):
    fixture_value = "RegressionOnly!Passw0rd"
    file_value = prefix + fixture_value + suffix
    secret_name = filename.replace("_", "-")
    (tmp_path / secret_name).write_text(file_value, encoding="utf-8", newline="")
    application_value = read_secret(tmp_path, secret_name).get_secret_value()
    actual_sql = sql.replace(f"pg_read_file('/run/secrets/{filename}')", ":file_value")
    async with pg_factory() as session:
        database_value = await session.scalar(text(actual_sql), {"file_value": file_value})
    assert database_value == application_value == fixture_value


def test_all_four_database_role_password_statements_are_exercised():
    assert {filename for _, filename in PASSWORD_EXPRESSIONS} == {
        "postgres_app_password",
        "postgres_test_password",
        "postgres_analytics_password",
        "postgres_test_analytics_password",
    }
