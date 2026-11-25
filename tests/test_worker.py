from app.worker import tasks
from app.worker.celery_app import celery_app


def queue_of(task) -> str:
    return celery_app.amqp.router.route({}, task.name)["queue"].name


def test_embedding_runs_on_its_own_queue_and_fetching_stays_on_default():
    # task_routes matches by task NAME, so a typo there would silently send embedding to
    # the fetch workers (each of which would then load its own copy of the model).
    assert queue_of(tasks.embed_and_cluster) == "embed"
    assert queue_of(tasks.fetch_source) == "celery"
    assert queue_of(tasks.dispatch_due_feeds) == "celery"


def test_beat_schedules_both_periodic_tasks():
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled == {tasks.dispatch_due_feeds.name, tasks.embed_and_cluster.name}


def test_stale_embed_ticks_expire_with_clock_margin():
    # Expiry is checked on the worker's clock against Beat's send time; 300 s leaves room
    # for clock drift between machines (at 60 s, a 1-minute drift would drop every tick).
    entry = celery_app.conf.beat_schedule["embed-and-cluster"]
    assert entry["options"]["expires"] == 300.0


def test_embed_and_cluster_embeds_before_clustering(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "get_embedder", lambda: None)
    monkeypatch.setattr(tasks, "embed_pending", lambda s, e: calls.append("embed") or 2)
    monkeypatch.setattr(tasks, "cluster_pending", lambda s: calls.append("cluster") or 3)
    assert tasks.embed_and_cluster() == (2, 3)
    assert calls == ["embed", "cluster"]
