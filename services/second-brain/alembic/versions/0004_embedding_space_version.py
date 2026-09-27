"""embedding space versioning on candidate_knowledge

Adds `embedding_model_version` and `embedding_task_type`, and stops queries mixing
embedding generations.

Why this is a correctness fix and not bookkeeping:

Cosine similarity is only meaningful *within one embedding space*. A vector produced by a
different model -- or by the same model with a different `task_type` -- lives in a
different space. Querying across spaces does not error. It returns numbers, ranks
candidates, and looks entirely plausible while being semantically meaningless. That
ranking feeds `semantic_score`, which is half of `final_score` by default, and
`final_score` is persisted into an immutable `MatchResult` this project presents as
auditable.

The trap is that the obvious guard does not catch it. `nomic-embed-text` produces exactly
768 dimensions -- the same as this column -- so a dimension check passes cleanly. Nothing
in the schema recorded *which* space a stored vector belonged to: RWS's `MatchResult` has
an `embedding_model_version`, but that describes the score, not the vector.

The backfill value is not a guess. Every existing row was written by
`gemini-embedding-001` with no task_type set (see main.py's embed_text calls at the time
of this migration), so `NOT NULL DEFAULT 'gemini-embedding-001'` and a NULL task_type
describe those rows accurately.

Doing this now is cheap -- there are 9 vectors. At 50,000 it is a migration with a
rollback plan. See SCALING_AND_FUTURE_IMPROVEMENTS.md §5.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# What every pre-existing row was actually embedded with.
LEGACY_MODEL = "gemini-embedding-001"


def upgrade() -> None:
    op.add_column(
        "candidate_knowledge",
        sa.Column(
            "embedding_model_version",
            sa.Text(),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_MODEL}'"),
        ),
        schema="second_brain",
    )
    # Nullable on purpose: NULL means "no task_type was set", which is precisely the
    # state of every existing vector. An empty string would be a different claim.
    op.add_column(
        "candidate_knowledge",
        sa.Column("embedding_task_type", sa.Text(), nullable=True),
        schema="second_brain",
    )
    # Retrieval filters by the active space before ordering by distance, so this index
    # exists to keep that filter cheap as generations accumulate during a re-embed.
    op.create_index(
        "ix_candidate_knowledge_embedding_version",
        "candidate_knowledge",
        ["embedding_model_version"],
        schema="second_brain",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_knowledge_embedding_version",
        table_name="candidate_knowledge",
        schema="second_brain",
    )
    op.drop_column("candidate_knowledge", "embedding_task_type", schema="second_brain")
    op.drop_column("candidate_knowledge", "embedding_model_version", schema="second_brain")
