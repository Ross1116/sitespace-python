import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import main


def test_health_check_returns_healthy_when_database_is_connected(monkeypatch):
    monkeypatch.setattr(main, "assert_database_connection", lambda _engine: None)
    monkeypatch.setattr(main, "engine", object())

    payload = asyncio.run(main.health_check())

    assert payload["status"] == "healthy"
    assert payload["database"] == "connected"


def test_health_check_returns_503_when_database_is_disconnected(monkeypatch):
    def _raise(_engine):
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "assert_database_connection", _raise)
    monkeypatch.setattr(main, "engine", object())

    response = asyncio.run(main.health_check())
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["status"] == "unhealthy"
    assert body["database"] == "disconnected"
