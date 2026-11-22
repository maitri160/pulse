"""add article embedding

Adds a nullable vector(384) column. Nullable, so existing rows need no backfill to
migrate; the embed task fills them in afterwards. No vector index yet: nothing searches
articles by vector. M8's event search gets its own index on the events table.
(Autogenerate drafted this but forgot to import pgvector; checked by hand.)

Revision ID: 79d439d82c20
Revises: 96726efea6cc
Create Date: 2026-11-22 17:54:23.934954

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "79d439d82c20"
down_revision: str | Sequence[str] | None = "96726efea6cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add articles.embedding (NULL until the embed task fills it)."""
    op.add_column("articles", sa.Column("embedding", Vector(384), nullable=True))


def downgrade() -> None:
    """Remove the column (and every stored embedding with it)."""
    op.drop_column("articles", "embedding")
