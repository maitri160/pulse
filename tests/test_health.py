"""Tests for /health. These expect `docker compose up -d db redis` to be running."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_health_ok_when_services_up():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"db": "ok", "redis": "ok"}


def test_health_503_when_redis_down(monkeypatch):
    def broken_redis():
        raise ConnectionError("redis is down")

    # Swap the real check for one that fails, so we don't have to stop Redis.
    monkeypatch.setattr(main, "check_redis", broken_redis)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"db": "ok", "redis": "error: ConnectionError"}
