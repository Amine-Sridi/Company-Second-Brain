"""candidates table

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23

Adds the `candidates` table to the `resume_intel` schema.
  - id: UUID primary key minted here (this is the canonical candidate_id for all services)
  - email_normalized: lowercase-stripped email for dedup; UNIQUE, nullable (no email → no dedup)
  - name, email, phone: PII fields, owned exclusively by Resume Intelligence
  - raw_text: extraction output used later for embedding (PII-stripped before passing to Second Brain)
  - candidate_json: full parser output (JSONB), kept for auditability and re-processing
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE resume_intel.candidates (
            id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email_normalized TEXT        UNIQUE,
            name             TEXT        NOT NULL,
            email            TEXT,
            phone            TEXT,
            raw_text         TEXT        NOT NULL,
            candidate_json   JSONB       NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS resume_intel.candidates")
