"""interview_rounds -- post-VoiceHR technical/managerial interview stage

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

Adds `recruitment.interview_rounds`, the post-VoiceHR technical/managerial interview
stage for candidates whose language gate has cleared (see handoff.md and
language_gate.py). Deliberately does NOT touch `applications.status` or its CHECK
constraint -- this is a parallel, one-to-many signal beside the existing
selected/invited/interviewed/accepted/rejected state machine, the same relationship
the language gate already has to it (a computed gate, not a state-machine value).

`outcome` is paired with `status` by a CHECK constraint, the same style already used
for jobs_language_requirement_paired_check: a round only has a pass/fail/needs_review
verdict once it's `completed`; `scheduled`/`cancelled` rounds always have a NULL
outcome. `interviewer_name` is a plain TEXT column, not a foreign key -- this project
has exactly one seeded recruiter account and no interviewer/user directory
(CLAUDE.md's non-negotiable decisions), so there is no table to reference.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE recruitment.interview_rounds (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id    UUID        NOT NULL REFERENCES recruitment.applications(id),
            round_type        TEXT        NOT NULL
                CHECK (round_type IN ('technical', 'managerial')),
            status            TEXT        NOT NULL DEFAULT 'scheduled'
                CHECK (status IN ('scheduled', 'completed', 'cancelled')),
            outcome           TEXT
                CHECK (outcome IN ('pass', 'fail', 'needs_review') OR outcome IS NULL),
            interviewer_name  TEXT        NOT NULL,
            scheduled_at      TIMESTAMPTZ,
            notes             TEXT,
            created_by        UUID        NOT NULL REFERENCES recruitment.recruiters(id),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT interview_rounds_outcome_paired_check
                CHECK ((status = 'completed') = (outcome IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE INDEX interview_rounds_application_id_idx
            ON recruitment.interview_rounds (application_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recruitment.interview_rounds")
