"""job-specific VoiceHR language eligibility gate -- requirement + snapshot columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-13

Adds an optional (required_language, min_language_cefr) pair to `jobs` -- a recruiter-
configured, deterministic screening criterion, never an LLM decision (see
language_gate.py). Both columns are nullable and NULL on every existing row after this
migration: no arbitrary requirement is backfilled onto jobs that never had one, and
`language_gate.compute_language_gate` treats "no requirement" (either column NULL) as
`not_required`, preserving current accept/advance behavior for every existing job and
application untouched by this feature.

`applications` gets the same pair, snapshotted from the job at *selection* time
(db.create_application) rather than read live from `jobs` on every request -- `jobs`'
requirement is mutable (see the new PATCH .../language-requirement endpoint), so an
old application's already-reported gate result must not silently change if a job's
requirement is edited after the fact (see CLAUDE.md's reproducibility rule, applied
here the same way MatchResult already snapshots candidate/threshold data).

The *assessed* language/CEFR are not new columns: they already live inside
`applications.interview_assessment` (JSONB, unchanged shape here) once VoiceHR's
webhook starts sending an assessment `language` field (see shared/schemas and
voicehr/backend/services/rws_client.py) -- reading them from there instead of a third
duplicate column is the smaller, exactly-as-reproducible option, since that JSONB blob
is itself never mutated after the one webhook-triggered write.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LANGUAGE_CHECK = "required_language IN ('en', 'fr') OR required_language IS NULL"
_CEFR_CHECK = "min_language_cefr IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2') OR min_language_cefr IS NULL"
_PAIRED_CHECK = "(required_language IS NULL) = (min_language_cefr IS NULL)"

_APP_CEFR_CHECK = "required_cefr IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2') OR required_cefr IS NULL"
_APP_PAIRED_CHECK = "(required_language IS NULL) = (required_cefr IS NULL)"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE recruitment.jobs
            ADD COLUMN required_language TEXT,
            ADD COLUMN min_language_cefr TEXT,
            ADD CONSTRAINT jobs_required_language_check CHECK ({_LANGUAGE_CHECK}),
            ADD CONSTRAINT jobs_min_language_cefr_check CHECK ({_CEFR_CHECK}),
            ADD CONSTRAINT jobs_language_requirement_paired_check CHECK ({_PAIRED_CHECK})
    """)

    op.execute(f"""
        ALTER TABLE recruitment.applications
            ADD COLUMN required_language TEXT,
            ADD COLUMN required_cefr TEXT,
            ADD CONSTRAINT applications_required_language_check CHECK ({_LANGUAGE_CHECK}),
            ADD CONSTRAINT applications_required_cefr_check CHECK ({_APP_CEFR_CHECK}),
            ADD CONSTRAINT applications_language_requirement_paired_check CHECK ({_APP_PAIRED_CHECK})
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.applications
            DROP CONSTRAINT IF EXISTS applications_language_requirement_paired_check,
            DROP CONSTRAINT IF EXISTS applications_required_cefr_check,
            DROP CONSTRAINT IF EXISTS applications_required_language_check,
            DROP COLUMN IF EXISTS required_cefr,
            DROP COLUMN IF EXISTS required_language
    """)
    op.execute("""
        ALTER TABLE recruitment.jobs
            DROP CONSTRAINT IF EXISTS jobs_language_requirement_paired_check,
            DROP CONSTRAINT IF EXISTS jobs_min_language_cefr_check,
            DROP CONSTRAINT IF EXISTS jobs_required_language_check,
            DROP COLUMN IF EXISTS min_language_cefr,
            DROP COLUMN IF EXISTS required_language
    """)
