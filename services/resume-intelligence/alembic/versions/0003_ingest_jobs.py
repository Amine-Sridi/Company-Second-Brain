"""ingest_jobs table

Adds `resume_intel.ingest_jobs`: durable state for queued resume ingestion.

The point of this table is that **Redis is not the source of truth**. ARQ keeps its own
job records in Redis, but Redis is a broker here and may be flushed, evicted or
restarted; when that happens the acceptable loss is throughput, never the knowledge that
a recruiter uploaded a CV. A row is written here *before* anything is enqueued.

Deliberately holds no PII -- no name, email, phone or extracted text. `candidate_id` is
the opaque link to the real record, and `error` is a generic reason string, because the
extraction pipeline's own exception messages can embed candidate data.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingest_jobs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("upload_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ingest_jobs_status",
        ),
        schema="resume_intel",
    )
    # The recovery query: "what was queued and never finished?" -- used to re-enqueue
    # after a Redis loss, and the reason this table exists at all.
    op.create_index(
        "ix_ingest_jobs_status_created_at",
        "ingest_jobs",
        ["status", "created_at"],
        schema="resume_intel",
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_jobs_status_created_at", table_name="ingest_jobs", schema="resume_intel")
    op.drop_table("ingest_jobs", schema="resume_intel")
