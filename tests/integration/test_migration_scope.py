"""The migration grants project objects without changing unrelated table ACLs."""

import ast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from enterprise_agent.config import PROJECT_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _migration_grant_sql() -> str:
    source = PROJECT_ROOT / "alembic" / "versions" / "0001_ecommerce.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lstrip().startswith("DO $$ DECLARE app_role text; BEGIN")
    ]
    assert len(literals) == 1, "Expected one actual migration grant block"
    return literals[0]


async def test_project_grants_preserve_unrelated_table_permissions(pg_admin_factory):
    existing = "ea_scope_existing_" + uuid4().hex
    private = "ea_scope_private_" + uuid4().hex
    async with pg_admin_factory() as session:
        transaction = await session.begin()
        try:
            # All probes and ACL changes remain inside this disposable transaction.
            for name in (existing, private):
                await session.execute(text(f'CREATE TABLE public."{name}" (id integer)'))
                await session.execute(text(f'REVOKE ALL ON TABLE public."{name}" FROM ea_test_app'))
            await session.execute(text(f'GRANT SELECT ON TABLE public."{existing}" TO ea_test_app'))
            await session.execute(text(_migration_grant_sql()))

            grants = (
                await session.execute(
                    text(
                        "SELECT has_table_privilege('ea_test_app', :existing, 'SELECT'), "
                        "has_table_privilege('ea_test_app', :existing, 'INSERT'), "
                        "has_table_privilege('ea_test_app', :private, 'SELECT')"
                    ),
                    {"existing": f"public.{existing}", "private": f"public.{private}"},
                )
            ).one()
            assert tuple(grants) == (True, False, False)

            # Verify execution under the ordinary role in the same real transaction.
            await session.execute(text("SET LOCAL ROLE ea_test_app"))
            await session.execute(text(f'SELECT id FROM public."{existing}"'))
            for denied_query in (
                f'INSERT INTO public."{existing}" (id) VALUES (1)',
                f'SELECT id FROM public."{private}"',
            ):
                with pytest.raises(ProgrammingError) as raised:
                    async with session.begin_nested():
                        await session.execute(text(denied_query))
                assert raised.value.orig.sqlstate == "42501"
            await session.execute(text("RESET ROLE"))
        finally:
            await transaction.rollback()
