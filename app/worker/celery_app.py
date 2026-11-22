"""The Celery app: which broker to use, task settings, and the Beat schedule."""

from celery import Celery

from app.config import settings

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
    beat_schedule={
        "dispatch-due-feeds": {
            "task": "app.worker.tasks.dispatch_due_feeds",
            "schedule": float(settings.dispatch_interval_seconds),
        },
    },
)
