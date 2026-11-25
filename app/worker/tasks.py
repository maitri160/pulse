"""Celery tasks. Kept thin: they open a session/client and call plain, tested functions."""

import logging

from app.db import SessionLocal
from app.events.cluster import cluster_pending
from app.events.embed import embed_pending, get_embedder
from app.ingest.fetcher import make_http_client
from app.ingest.scheduling import claim_due_sources, run_source
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def dispatch_due_feeds() -> int:
    """Run by Beat every minute: queue one fetch_source task per due feed."""
    with SessionLocal() as session:
        source_ids = claim_due_sources(session)
        # Commit the leases BEFORE queueing. If queueing then fails, the leases simply
        # expire and the feeds are picked up again later. Nothing is lost.
        session.commit()

    for source_id in source_ids:
        fetch_source.delay(source_id)
    logger.info("Dispatched %d due feeds", len(source_ids))
    return len(source_ids)


@celery_app.task
def fetch_source(source_id: int) -> None:
    """Fetch one feed into the database."""
    with SessionLocal() as session, make_http_client() as client:
        stats = run_source(session, client, source_id)
    if stats is not None:
        logger.info("Source %d: %s", source_id, stats)


@celery_app.task
def embed_and_cluster() -> tuple[int, int]:
    """Run by Beat every minute, on the `embed` queue: embed new originals, then put them
    into events. Same task, so an article embedded this minute is clustered this minute."""
    with SessionLocal() as session:
        embedded = embed_pending(session, get_embedder())
        clustered = cluster_pending(session)
    if embedded or clustered:
        logger.info("Embedded %d, clustered %d articles", embedded, clustered)
    return embedded, clustered
