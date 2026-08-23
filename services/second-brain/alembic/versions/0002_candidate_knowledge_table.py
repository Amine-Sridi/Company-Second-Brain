"""candidate_knowledge table with pgvector embedding

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE second_brain.candidate_knowledge (
            candidate_id        UUID        PRIMARY KEY,
            skills              JSONB       NOT NULL DEFAULT '[]',
            years_of_experience FLOAT,
            job_titles          JSONB       NOT NULL DEFAULT '[]',
            raw_text            TEXT        NOT NULL,
            source_version      TEXT        NOT NULL DEFAULT '1',
            embedding           vector(768) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX candidate_knowledge_embedding_hnsw
            ON second_brain.candidate_knowledge
            USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS second_brain.candidate_knowledge")
