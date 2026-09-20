"""interview_rounds: interviewer_email, location, notification_status/error/sequence

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-21

Phase 1 of automating human interview scheduling (see handoff.md): adds the fields
needed to notify a candidate and an interviewer by email once a recruiter schedules
a technical/managerial round, without inventing an interviewer user-management
system (CLAUDE.md non-negotiable) -- interviewer_email is plain text on the round
itself, exactly like interviewer_name already is, not a foreign key to a directory
that doesn't exist.

interviewer_email/location are nullable: every interview_rounds row created before
this migration has neither, and there is no sensible non-null default to backfill
(unlike migration 0008's extraction_status, "clean" was a genuinely correct default
for pre-existing rows; there is no correct default here). Going forward, the API
layer (schemas.InterviewRoundRequest) requires interviewer_email on every new round
-- this is an API-level guarantee, not a DB-level one, the same relationship
required_language/min_language_cefr already have between migration 0006 and their
own request schema.

notification_status/notification_error mirror extraction_status/extraction_warnings'
own "advisory outcome of a side effect, not part of the round's own identity"
treatment (migration 0008) -- NOT NULL with a constant default, since "no
notification has been attempted yet" is exactly what 'pending' means for every
pre-existing row, including for the never-run notification logic itself.

notification_sequence is added now (default 0) but not yet used by any application
logic -- it exists purely so migration 0010 doesn't need to reappear when phase 2
(ICS invitations) needs an integer SEQUENCE for reschedule/cancel updates. Structural
only in this phase, per this task's own explicit scope.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.interview_rounds
            ADD COLUMN interviewer_email TEXT,
            ADD COLUMN location TEXT,
            ADD COLUMN notification_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (notification_status IN ('pending', 'sent', 'failed')),
            ADD COLUMN notification_error TEXT,
            ADD COLUMN notification_sequence INTEGER NOT NULL DEFAULT 0
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recruitment.interview_rounds
            DROP COLUMN IF EXISTS notification_sequence,
            DROP COLUMN IF EXISTS notification_error,
            DROP COLUMN IF EXISTS notification_status,
            DROP COLUMN IF EXISTS location,
            DROP COLUMN IF EXISTS interviewer_email
    """)
