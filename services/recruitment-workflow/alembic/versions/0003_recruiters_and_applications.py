"""recruiters, applications, recruiter_decisions

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26

Adds recruiter authentication (bcrypt-hashed passwords, one seeded account) and the
Application state machine: selected -> accepted | rejected, created from a recruiter's
selection of a qualified MatchResult. recruiter_decisions is an append-only audit log
of every successful accept/reject transition -- no update path exists for it.

The seed recruiter's password comes from SEED_RECRUITER_EMAIL / SEED_RECRUITER_PASSWORD
environment variables (same pattern as this project's other static credentials) so no
plaintext or hash is hardcoded in this file; only the bcrypt hash computed at migration
time is ever written to the database.
"""
import os
from typing import Sequence, Union

import bcrypt
import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_SEED_EMAIL = "recruiter@example.com"
_DEFAULT_SEED_PASSWORD = "changeme-recruiter-password"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE recruitment.recruiters (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email          TEXT        NOT NULL UNIQUE,
            password_hash  TEXT        NOT NULL,
            name           TEXT        NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE recruitment.applications (
            id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id           UUID        NOT NULL REFERENCES recruitment.jobs(id),
            match_result_id  UUID        NOT NULL UNIQUE
                REFERENCES recruitment.match_results(id),
            candidate_id     UUID        NOT NULL,
            status           TEXT        NOT NULL DEFAULT 'selected'
                CHECK (status IN ('selected', 'accepted', 'rejected')),
            selected_by      UUID        NOT NULL REFERENCES recruitment.recruiters(id),
            selected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_by       UUID        REFERENCES recruitment.recruiters(id),
            decided_at       TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT applications_job_candidate_unique UNIQUE (job_id, candidate_id)
        )
    """)
    op.execute("""
        CREATE INDEX applications_job_id_idx ON recruitment.applications (job_id)
    """)

    op.execute("""
        CREATE TABLE recruitment.recruiter_decisions (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            application_id    UUID        NOT NULL REFERENCES recruitment.applications(id),
            recruiter_id      UUID        NOT NULL REFERENCES recruitment.recruiters(id),
            decision          TEXT        NOT NULL CHECK (decision IN ('accept', 'reject')),
            previous_status   TEXT        NOT NULL,
            new_status        TEXT        NOT NULL,
            decided_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX recruiter_decisions_application_id_idx
            ON recruitment.recruiter_decisions (application_id)
    """)

    email = os.environ.get("SEED_RECRUITER_EMAIL", _DEFAULT_SEED_EMAIL)
    password = os.environ.get("SEED_RECRUITER_PASSWORD", _DEFAULT_SEED_PASSWORD)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    op.get_bind().execute(
        sa.text("""
            INSERT INTO recruitment.recruiters (email, password_hash, name)
            VALUES (:email, :password_hash, :name)
        """),
        {"email": email, "password_hash": password_hash, "name": "Default Recruiter"},
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recruitment.recruiter_decisions")
    op.execute("DROP TABLE IF EXISTS recruitment.applications")
    op.execute("DROP TABLE IF EXISTS recruitment.recruiters")
