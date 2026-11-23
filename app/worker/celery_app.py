"""The Celery app: which broker to use, task settings, and the Beat schedule."""

from celery import Celery

from app.config import settings

EMBED_TICK_EXPIRES_SECONDS = 300.0  # see the "embed-articles" Beat entry below

celery_app = Celery("pulse", broker=settings.redis_url, include=["app.worker.tasks"])

celery_app.conf.update(
    timezone="UTC",
    # We never read task return values, so don't store them anywhere.
    task_ignore_result=True,
    # Acknowledge a task only AFTER it finishes, so tasks a worker has taken but not finished
    # go back to Redis if the WHOLE worker dies (after Redis's visibility timeout, 1 h by
    # default). If only one child process dies (OOM kill, hard time limit), Celery still acks
    # the task (task_reject_on_worker_lost is off), so it is NOT retried. Either way, the feed
    # is recovered by its 15-min lease expiring (see claim_due_sources). Rerunning a fetch is
    # safe because ingest is idempotent.
    task_acks_late=True,
    # Each worker process takes one task at a time, so a slow feed doesn't hold others back.
    worker_prefetch_multiplier=1,
    # Hard stop for a stuck task. Shorter than the fetch lease (see config.py), so a
    # feed is never fetched by two workers at once.
    task_time_limit=settings.task_time_limit_seconds,
    broker_connection_retry_on_startup=True,
    # Embedding gets its own queue, read by the `embedder` container with ONE process.
    # The model (PyTorch + MiniLM, ~400 MB) then loads once, instead of once in each of the
    # fetch worker's 4 processes. Everything else stays on the default queue ("celery").
    task_routes={"app.worker.tasks.embed_articles": {"queue": "embed"}},
    beat_schedule={
        "dispatch-due-feeds": {
            "task": "app.worker.tasks.dispatch_due_feeds",
            "schedule": float(settings.dispatch_interval_seconds),
        },
        "embed-articles": {
            "task": "app.worker.tasks.embed_articles",
            "schedule": float(settings.embed_interval_seconds),
            # If the embedder is down, Beat keeps queueing a tick every minute. Drop stale
            # ticks instead of running a pile of them back to back later (no work is lost:
            # the pending rows are in Postgres). 300 s, not 60: expiry compares Beat's clock
            # with the worker's, so this leaves margin if they ever run on different machines.
            "options": {"expires": EMBED_TICK_EXPIRES_SECONDS},
        },
    },
)
