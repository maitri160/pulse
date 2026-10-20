import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db import engine

app = FastAPI(title="Pulse")

redis_client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)


def check_db() -> None:
    """Raise if Postgres can't run a trivial query."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def check_redis() -> None:
    """Raise if Redis doesn't answer PING."""
    redis_client.ping()


@app.get("/health")
def health() -> JSONResponse:
    """Report whether each dependency is reachable.

    Returns 200 if everything is up, 503 (Service Unavailable) if anything is down,
    so Docker or a load balancer can tell from the status code alone.
    """
    checks = {"db": check_db, "redis": check_redis}
    results = {}
    for name, check in checks.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:  # any failure means "not healthy"; we report, not crash
            results[name] = f"error: {type(exc).__name__}"

    healthy = all(status == "ok" for status in results.values())
    return JSONResponse(status_code=200 if healthy else 503, content=results)
