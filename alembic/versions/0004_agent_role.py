"""Pin Agent history to its original authorization role.

Revision ID: 0004_agent_role
Revises: 0003_agent

Role changes require a new session, keeping staff facts and internal knowledge
out of a less privileged user's history and subsequent model input.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_agent_role"
down_revision = "0003_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("role_at_creation", sa.String(8), nullable=True))
    op.execute(
        sa.text("""
        UPDATE agent_sessions AS sessions SET role_at_creation = users.role
        FROM users WHERE users.id = sessions.user_id
        """)
    )
    op.alter_column(
        "agent_sessions", "role_at_creation", existing_type=sa.String(8), nullable=False
    )
    op.create_check_constraint(
        op.f("ck_agent_sessions_agent_session_role"),
        "agent_sessions",
        "role_at_creation IN ('customer', 'support', 'analyst', 'admin')",
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION ea_agent_session_identity_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.user_id, NEW.thread_id, NEW.role_at_creation)
               IS DISTINCT FROM ROW(OLD.user_id, OLD.thread_id, OLD.role_at_creation) THEN
                RAISE EXCEPTION 'Agent session ownership and creation role are immutable'
                    USING ERRCODE = '23514',
                    CONSTRAINT = 'ck_agent_session_identity_immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER ea_agent_session_identity_immutable_trigger
        BEFORE UPDATE ON agent_sessions
        FOR EACH ROW EXECUTE FUNCTION ea_agent_session_identity_immutable();
        """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP TRIGGER ea_agent_session_identity_immutable_trigger ON agent_sessions")
    )
    op.execute(sa.text("DROP FUNCTION ea_agent_session_identity_immutable()"))
    op.drop_constraint(
        op.f("ck_agent_sessions_agent_session_role"), "agent_sessions", type_="check"
    )
    op.drop_column("agent_sessions", "role_at_creation")
