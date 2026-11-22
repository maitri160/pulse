"""drop redundant content_hash index

The only query on content_hash is _find_original: WHERE content_hash = ? AND
duplicate_of_id IS NULL. The partial unique index uq_articles_original_content_hash
covers exactly that (checked with EXPLAIN), so the plain index only cost an extra
write on every insert.

Revision ID: 96726efea6cc
Revises: fa13b34449bc
Create Date: 2026-11-22 13:47:04.478322

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "96726efea6cc"
down_revision: str | Sequence[str] | None = "fa13b34449bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the plain index on content_hash."""
    op.drop_index("ix_articles_content_hash", table_name="articles")


def downgrade() -> None:
    """Put the plain index back, exactly as the first migration created it."""
    op.create_index("ix_articles_content_hash", "articles", ["content_hash"], unique=False)
