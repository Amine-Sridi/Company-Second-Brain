"""candidate_feedback table (recruiter-decision write-back from RWS)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-27

PII-free by design, like candidate_knowledge: only the job title, which required
skills matched/were missing, the final score, and the accept/reject outcome. No name,
email, phone, or raw resume text.

candidate_id and application_id are plain UUID columns, not foreign keys.
application_id belongs to RWS's recruitment schema -- a cross-schema FK is impossible
here anyway (no cross-schema grants, per the architecture), and is also the wrong
default in general for this kind of write-back: this endpoint must accept feedback
for a candidate_id even if that candidate's knowledge row was later removed, the same
lesson learned from interview_webhook_events.application_id in a previous session.

application_id UNIQUE is the idempotency key: a recruiter decision happens at most
once per application (the Application state machine is terminal), so upserting on
application_id makes a retried delivery safe -- it updates the same row instead of
creating a duplicate.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE second_brain.candidate_feedback (
            id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_id           UUID        NOT NULL,
            application_id         UUID        NOT NULL UNIQUE,
            decision               TEXT        NOT NULL CHECK (decision IN ('accepted', 'rejected')),
            job_title              TEXT        NOT NULL,
            matched_requirements   JSONB       NOT NULL DEFAULT '[]',
            missing_requirements   JSONB       NOT NULL DEFAULT '[]',
            final_score            FLOAT       NOT NULL,
            decided_at             TIMESTAMPTZ NOT NULL,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX candidate_feedback_candidate_id_idx
            ON second_brain.candidate_feedback (candidate_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS second_brain.candidate_feedback")
