"""initial empty migration

Revision ID: 0001
Revises:
Create Date: 2026-08-22

Intentionally empty. Proves the Alembic harness (connection, role, schema) works end-to-end
before any real tables are defined. The candidates table lands in a later migration once
Resume Intelligence's actual source is available (see handoff.md).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
