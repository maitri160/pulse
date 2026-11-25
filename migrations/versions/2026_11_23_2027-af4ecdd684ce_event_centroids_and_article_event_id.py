"""event centroids and article event_id

events.centroid and events.last_article_at are NOT NULL with no default: safe only because
the events table is still empty (checked before writing this; nothing filled it before M8).
No vector index on centroid: clustering searches only events with an article in the last
3 days (a few hundred to a few thousand), where an exact scan takes milliseconds, and an
approximate (HNSW) index combined with that WHERE filter can silently miss matches.
(Autogenerate drafted this, but forgot the pgvector import and left the foreign key
unnamed, so its downgrade couldn't drop it. Fixed by hand.)

Revision ID: af4ecdd684ce
Revises: 79d439d82c20
Create Date: 2026-11-23 20:27:50.684091

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "af4ecdd684ce"
down_revision: str | Sequence[str] | None = "79d439d82c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_articles_event_id_events"


def upgrade() -> None:
    """Give events a centroid and a recency time; link articles to their event."""
    op.add_column("events", sa.Column("centroid", Vector(384), nullable=False))
    op.add_column(
        "events", sa.Column("last_article_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index("ix_events_last_article_at", "events", ["last_article_at"])

    op.add_column("articles", sa.Column("event_id", sa.Integer(), nullable=True))
    op.create_foreign_key(FK_NAME, "articles", "events", ["event_id"], ["id"])
    op.create_index("ix_articles_event_id", "articles", ["event_id"])


def downgrade() -> None:
    """Undo upgrade(), in reverse order."""
    op.drop_index("ix_articles_event_id", table_name="articles")
    op.drop_constraint(FK_NAME, "articles", type_="foreignkey")
    op.drop_column("articles", "event_id")

    op.drop_index("ix_events_last_article_at", table_name="events")
    op.drop_column("events", "last_article_at")
    op.drop_column("events", "centroid")
