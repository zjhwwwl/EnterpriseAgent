"""Persist fenced knowledge indexing and protect published policy sources.

Revision ID: 0002_knowledge
Revises: 0001_ecommerce
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_knowledge"
down_revision = "0001_ecommerce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("index_attempt_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("active_index_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("indexing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("index_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_documents", sa.Column("index_fingerprint", sa.String(128), nullable=True)
    )
    op.add_column(
        "knowledge_documents", sa.Column("index_error_code", sa.String(64), nullable=True)
    )
    op.add_column(
        "knowledge_documents", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Preserve already published generations before adding the ordering constraint.
    op.execute(
        sa.text("UPDATE knowledge_documents SET index_attempt_generation = index_generation")
    )
    op.create_check_constraint(
        op.f("ck_knowledge_documents_index_attempt_generation_ordered"),
        "knowledge_documents",
        "index_attempt_generation >= index_generation",
    )

    op.execute(
        sa.text("""
        CREATE FUNCTION ea_knowledge_policy_source_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.refund_policy_versions
                WHERE document_id = OLD.id
            ) THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A published refund policy source cannot be deleted'
                        USING ERRCODE = '23514',
                        CONSTRAINT = 'ck_knowledge_policy_source_immutable';
                END IF;
                IF ROW(NEW.id, NEW.title, NEW.file_type, NEW.file_path, NEW.checksum,
                       NEW.version, NEW.visibility, NEW.deleted_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.title, OLD.file_type, OLD.file_path, OLD.checksum,
                       OLD.version, OLD.visibility, OLD.deleted_at) THEN
                    RAISE EXCEPTION 'A published refund policy source cannot be changed'
                        USING ERRCODE = '23514',
                        CONSTRAINT = 'ck_knowledge_policy_source_immutable';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER ea_knowledge_policy_source_immutable_trigger
        BEFORE UPDATE OR DELETE ON knowledge_documents
        FOR EACH ROW EXECUTE FUNCTION ea_knowledge_policy_source_immutable();
        """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION ea_refund_policy_source_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            source_visibility text;
            source_deleted_at timestamptz;
        BEGIN
            -- The FK's KEY SHARE allows non-key updates. SHARE also blocks
            -- concurrent soft deletion or source changes until INSERT commits.
            SELECT visibility, deleted_at
            INTO source_visibility, source_deleted_at
            FROM public.knowledge_documents
            WHERE id = NEW.document_id
            FOR SHARE;
            IF NOT FOUND OR source_deleted_at IS NOT NULL
               OR source_visibility <> 'public' THEN
                RAISE EXCEPTION 'Refund policy source must be existing, public and not deleted'
                    USING ERRCODE = '23514',
                    CONSTRAINT = 'ck_refund_policy_source_available';
            END IF;
            RETURN NEW;
        END;
        $$;
        """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER ea_refund_policy_source_guard_trigger
        BEFORE INSERT ON refund_policy_versions
        FOR EACH ROW EXECUTE FUNCTION ea_refund_policy_source_guard();
        """)
    )
    op.execute(
        sa.text("""
        DO $$ DECLARE app_role text; BEGIN
            FOREACH app_role IN ARRAY ARRAY['ea_app', 'ea_test_app'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role)
                   AND has_database_privilege(app_role, current_database(), 'CONNECT') THEN
                    EXECUTE format('GRANT INSERT, UPDATE ON TABLE '
                        'public.knowledge_documents TO %I', app_role);
                    EXECUTE format('GRANT INSERT, DELETE ON TABLE '
                        'public.knowledge_chunks TO %I', app_role);
                END IF;
            END LOOP;
        END $$;
        """)
    )


def downgrade() -> None:
    # Downgrade is explicit. Do not revoke privileges on unrelated project objects.
    op.execute(
        sa.text("""
        DO $$ DECLARE app_role text; BEGIN
            FOREACH app_role IN ARRAY ARRAY['ea_app', 'ea_test_app'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role)
                   AND has_database_privilege(app_role, current_database(), 'CONNECT') THEN
                    EXECUTE format('REVOKE INSERT, UPDATE ON TABLE '
                        'public.knowledge_documents FROM %I', app_role);
                    EXECUTE format('REVOKE INSERT, DELETE ON TABLE '
                        'public.knowledge_chunks FROM %I', app_role);
                END IF;
            END LOOP;
        END $$;
        """)
    )
    op.execute(
        sa.text("DROP TRIGGER ea_refund_policy_source_guard_trigger ON refund_policy_versions")
    )
    op.execute(sa.text("DROP FUNCTION ea_refund_policy_source_guard()"))
    op.execute(
        sa.text("DROP TRIGGER ea_knowledge_policy_source_immutable_trigger ON knowledge_documents")
    )
    op.execute(sa.text("DROP FUNCTION ea_knowledge_policy_source_immutable()"))
    op.drop_constraint(
        op.f("ck_knowledge_documents_index_attempt_generation_ordered"),
        "knowledge_documents",
        type_="check",
    )
    for column in (
        "indexed_at",
        "index_error_code",
        "index_fingerprint",
        "index_started_at",
        "indexing_job_id",
        "active_index_id",
        "index_attempt_generation",
    ):
        op.drop_column("knowledge_documents", column)
