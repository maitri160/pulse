from app.worker import tasks
from app.worker.celery_app import celery_app


def queue_of(task) -> str:
    return celery_app.amqp.router.route({}, task.name)["queue"].name


def test_embedding_runs_on_its_own_queue_and_fetching_stays_on_default():
    # task_routes matches by task NAME, so a typo there would silently send embedding to
    # the fetch workers (each of which would then load its own copy of the model).
    assert queue_of(tasks.embed_articles) == "embed"
    assert queue_of(tasks.fetch_source) == "celery"
    assert queue_of(tasks.dispatch_due_feeds) == "celery"


def test_beat_schedules_both_periodic_tasks():
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled == {tasks.dispatch_due_feeds.name, tasks.embed_articles.name}
