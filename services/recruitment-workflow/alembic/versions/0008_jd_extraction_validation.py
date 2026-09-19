"""job_descriptions: extraction_status + extraction_warnings

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-20

Adds deterministic JD-extraction validation metadata to `job_descriptions` --
recorded once, immediately after Gemini's JD structuring and before matching
(see jd_validation.py) -- never a correction of `required_skills`/
`preferred_skills`/`min_years_experience`/`titles`, which stay exactly what was
actually used for scoring (reproducibility unaffected). `extraction_status` is
`'clean'` or `'flagged'`; `extraction_warnings` is a JSON array of
`{"field": ..., "message": ...}` objects, empty when clean.

Both columns are `NOT NULL` with a constant default (`'clean'` / `'[]'`) rather
than nullable, unlike the language-requirement columns in migration 0006: there
`NULL` carries real meaning ("no requirement configured"), but here every job
description -- including every one that predates this migration -- has a
sensible, unambiguous backfill value: nothing validated it, so there is no
warning to show, i.e. `'clean'`. A constant default on `ADD COLUMN` is a fast
metadata-only operation on Postgres 11+, not a full table rewrite.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.job_descriptions
            ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'clean'
                CHECK (extraction_status IN ('clean', 'flagged')),
            ADD COLUMN extraction_warnings JSONB NOT NULL DEFAULT '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.job_descriptions
            DROP COLUMN IF EXISTS extraction_warnings,
            DROP COLUMN IF EXISTS extraction_status
    """)
