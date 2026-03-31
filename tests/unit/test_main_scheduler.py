import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import main


def test_register_nightly_scheduler_job_adds_when_missing(monkeypatch):
    scheduler = MagicMock()
    scheduler.get_job.return_value = None

    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            NIGHTLY_LOOKAHEAD_HOUR=17,
            NIGHTLY_LOOKAHEAD_MINUTE=0,
            NIGHTLY_LOOKAHEAD_TIMEZONE="Australia/Adelaide",
        ),
    )

    main._register_nightly_scheduler_job(scheduler)

    scheduler.add_job.assert_called_once()
    scheduler.reschedule_job.assert_not_called()


def test_register_nightly_scheduler_job_reschedules_when_present(monkeypatch):
    scheduler = MagicMock()
    scheduler.get_job.return_value = object()

    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            NIGHTLY_LOOKAHEAD_HOUR=17,
            NIGHTLY_LOOKAHEAD_MINUTE=0,
            NIGHTLY_LOOKAHEAD_TIMEZONE="Australia/Adelaide",
        ),
    )

    main._register_nightly_scheduler_job(scheduler)

    scheduler.add_job.assert_not_called()
    scheduler.reschedule_job.assert_called_once_with(
        "nightly_lookahead_job",
        trigger="cron",
        hour=17,
        minute=0,
        timezone="Australia/Adelaide",
    )


def test_register_nightly_scheduler_job_ignores_duplicate_insert_race(monkeypatch):
    scheduler = MagicMock()
    scheduler.get_job.return_value = None

    class ConflictingIdError(Exception):
        pass

    scheduler.add_job.side_effect = ConflictingIdError("Job identifier already exists")

    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            NIGHTLY_LOOKAHEAD_HOUR=17,
            NIGHTLY_LOOKAHEAD_MINUTE=0,
            NIGHTLY_LOOKAHEAD_TIMEZONE="Australia/Adelaide",
        ),
    )

    main._register_nightly_scheduler_job(scheduler)

    scheduler.add_job.assert_called_once()
    scheduler.reschedule_job.assert_not_called()


def test_recover_stale_programme_uploads_on_startup_uses_configured_threshold(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(PROGRAMME_PROCESSING_STALE_MINUTES=45),
    )

    captured = {}

    def _recover(_db, *, stale_after, project_id=None, now=None):
        captured["db"] = _db
        captured["stale_after"] = stale_after
        captured["project_id"] = project_id
        captured["now"] = now
        return 2

    monkeypatch.setattr(main, "recover_stale_processing_uploads", _recover)

    recovered = main._recover_stale_programme_uploads_on_startup()

    assert recovered == 2
    assert captured["db"] is db
    assert captured["stale_after"].total_seconds() == 45 * 60
    assert captured["project_id"] is None
    assert captured["now"] is None
    db.close.assert_called_once()


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
