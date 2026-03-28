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
