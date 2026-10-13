"""Persist Agent turns and the pinned official PostgreSQL checkpointer.

Revision ID: 0003_agent
Revises: 0002_knowledge

The ea_checkpoint tables match langgraph-checkpoint-postgres 3.1.2 MIGRATIONS
0 through 9. Alembic creates non-concurrent indexes inside its transaction and
records all ten SDK migration versions. API startup must never call setup().
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_agent"
down_revision = "0002_knowledge"
branch_labels = None
depends_on = None

CHECKPOINT_SCHEMA = "ea_checkpoint"
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    op.add_column(
        "agent_sessions", sa.Column("current_task_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "agent_sessions",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("agent_tasks", sa.Column("message", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "agent_tasks",
        sa.Column(
            "result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column("agent_tasks", sa.Column("elapsed_ms", sa.Integer(), nullable=True))
    op.add_column("agent_tasks", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agent_tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_agent_tasks_status_valid"),
        "agent_tasks",
        "status IN ('running', 'waiting_approval', 'completed', 'failed', "
        "'rejected', 'expired', 'cancelled')",
    )
    op.create_check_constraint(
        op.f("ck_agent_tasks_elapsed_nonnegative"),
        "agent_tasks",
        "elapsed_ms IS NULL OR elapsed_ms >= 0",
    )
    op.create_check_constraint(
        op.f("ck_agent_tasks_message_bounded"), "agent_tasks", "char_length(message) <= 2000"
    )
    op.create_index("ix_agent_tasks_session_created", "agent_tasks", ["session_id", "created_at"])

    # Dedicated, owner-created schema: no CREATE/DDL privilege for API accounts.
    op.execute(sa.text("CREATE SCHEMA ea_checkpoint"))
    op.execute(sa.text("REVOKE ALL ON SCHEMA ea_checkpoint FROM PUBLIC"))
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), primary_key=True),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        schema=CHECKPOINT_SCHEMA,
    )
    for table in CHECKPOINT_TABLES:
        op.create_index(f"{table}_thread_id_idx", table, ["thread_id"], schema=CHECKPOINT_SCHEMA)
    op.execute(
        sa.text("INSERT INTO ea_checkpoint.checkpoint_migrations (v) SELECT generate_series(0,9)")
    )

    op.execute(
        sa.text("""
        DO $$ DECLARE app_role text; BEGIN
            FOREACH app_role IN ARRAY ARRAY['ea_app', 'ea_test_app'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role)
                   AND has_database_privilege(app_role, current_database(), 'CONNECT') THEN
                    EXECUTE format('GRANT USAGE ON SCHEMA ea_checkpoint TO %I', app_role);
                    EXECUTE format('GRANT SELECT ON TABLE '
                        'ea_checkpoint.checkpoint_migrations TO %I', app_role);
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE '
                        'ea_checkpoint.checkpoints, ea_checkpoint.checkpoint_blobs, '
                        'ea_checkpoint.checkpoint_writes TO %I', app_role);
                END IF;
            END LOOP;
        END $$;
        """)
    )


def downgrade() -> None:
    # Explicit downgrade removes only the new, migration-owned checkpoint schema.
    for table in reversed(CHECKPOINT_TABLES):
        op.drop_table(table, schema=CHECKPOINT_SCHEMA)
    op.drop_table("checkpoint_migrations", schema=CHECKPOINT_SCHEMA)
    op.execute(sa.text("DROP SCHEMA ea_checkpoint"))
    op.drop_index("ix_agent_tasks_session_created", "agent_tasks")
    for name in ("message_bounded", "elapsed_nonnegative", "status_valid"):
        op.drop_constraint(op.f(f"ck_agent_tasks_{name}"), "agent_tasks", type_="check")
    for name in ("updated_at", "completed_at", "expires_at", "elapsed_ms", "result", "message"):
        op.drop_column("agent_tasks", name)
    op.drop_column("agent_sessions", "updated_at")
    op.drop_column("agent_sessions", "current_task_id")
