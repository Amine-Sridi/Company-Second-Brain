"""application interview state + interview_webhook_events

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27

Extends the Application state machine with two states between 'selected' and the
recruiter's decision: 'invited' (RWS asked VoiceHR to create an interview) and
'interviewed' (VoiceHR's signed webhook reported a terminal outcome). The final
accept/reject decision can now be made from 'selected', 'invited', or 'interviewed' --
see transition_application in db.py.

interview_webhook_events is an append-only receipt log for every VoiceHR webhook
delivery, keyed by delivery_id (UNIQUE) so a duplicate delivery is rejected at the
database level rather than re-processed. application_id is a plain UUID column, not a
foreign key: a webhook can legitimately reference an external_reference_id that does
not (or no longer) correspond to a real application (unknown/malformed/stale), and this
table must still be able to log that fact rather than have the insert itself fail.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE recruitment.applications DROP CONSTRAINT applications_status_check")
    op.execute("""
        ALTER TABLE recruitment.applications ADD CONSTRAINT applications_status_check
            CHECK (status IN ('selected', 'invited', 'interviewed', 'accepted', 'rejected'))
    """)

    op.execute("""
        ALTER TABLE recruitment.applications
            ADD COLUMN invite_attempt_number INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN invited_at TIMESTAMPTZ,
            ADD COLUMN interview_status TEXT
                CHECK (interview_status IN ('completed', 'inconclusive', 'failed')
                       OR interview_status IS NULL),
            ADD COLUMN interview_assessment JSONB,
            ADD COLUMN interview_completed_at TIMESTAMPTZ
    """)

    op.execute("""
        CREATE TABLE recruitment.interview_webhook_events (
            id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            delivery_id            TEXT        NOT NULL UNIQUE,
            application_id         UUID,
            external_reference_id  TEXT        NOT NULL,
            interview_status       TEXT        NOT NULL,
            assessment             JSONB       NOT NULL,
            processed              BOOLEAN     NOT NULL,
            received_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX interview_webhook_events_application_id_idx
            ON recruitment.interview_webhook_events (application_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recruitment.interview_webhook_events")
    op.execute("""
        ALTER TABLE recruitment.applications
            DROP COLUMN IF EXISTS invite_attempt_number,
            DROP COLUMN IF EXISTS invited_at,
            DROP COLUMN IF EXISTS interview_status,
            DROP COLUMN IF EXISTS interview_assessment,
            DROP COLUMN IF EXISTS interview_completed_at
    """)
    op.execute("ALTER TABLE recruitment.applications DROP CONSTRAINT applications_status_check")
    op.execute("""
        ALTER TABLE recruitment.applications ADD CONSTRAINT applications_status_check
            CHECK (status IN ('selected', 'accepted', 'rejected'))
    """)
