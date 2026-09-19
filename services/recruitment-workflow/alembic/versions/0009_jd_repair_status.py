"""job_descriptions: extraction_status allows 'repaired'

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-19

Widens migration 0008's CHECK constraint on `extraction_status` from
`'clean'|'flagged'` to `'clean'|'flagged'|'repaired'`. `'repaired'` means a
flagged extraction was automatically re-submitted to Gemini once (see
main.py's `repair_structured_extraction`) and the repaired result passed
deterministic validation cleanly -- distinct from `'clean'` (no repair was
ever needed) and `'flagged'` (repair was not attempted, failed outright, or
its own result still failed validation, in which case the original
extraction is what's actually persisted and matched, not the failed repair
attempt). No data migration needed: no existing row can already hold
`'repaired'`, since this status did not exist before this feature.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.job_descriptions
            DROP CONSTRAINT job_descriptions_extraction_status_check,
            ADD CONSTRAINT job_descriptions_extraction_status_check
                CHECK (extraction_status IN ('clean', 'flagged', 'repaired'))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.job_descriptions
            DROP CONSTRAINT job_descriptions_extraction_status_check,
            ADD CONSTRAINT job_descriptions_extraction_status_check
                CHECK (extraction_status IN ('clean', 'flagged'))
    """)
