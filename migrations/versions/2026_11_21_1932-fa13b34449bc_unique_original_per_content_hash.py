"""unique original per content hash

Written by hand: autogenerate is unreliable at spotting the WHERE clause of a
partial index, so it could miss this index or recreate it on every run.

Revision ID: fa13b34449bc
Revises: a65df8cb8309
Create Date: 2026-11-21 19:32:00.605219

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa13b34449bc"
down_revision: str | Sequence[str] | None = "a65df8cb8309"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow at most one original (duplicate_of_id IS NULL) per content_hash."""
    op.create_index(
        "uq_articles_original_content_hash",
        "articles",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("duplicate_of_id IS NULL"),
    )


def downgrade() -> None:
    """Remove the one-original-per-hash rule."""
    op.drop_index("uq_articles_original_content_hash", table_name="articles")
