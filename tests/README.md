# Sitespace FastAPI Test Suite

Last updated: 2026-03-08

This directory contains HTTP-level integration-style test scripts and a lightweight runner.

## Prerequisites

1. Start the API server:

```bash
uvicorn app.main:app --host localhost --port 8080
```

2. Install dependencies used by tests:

```bash
pip install requests
```

## Run Tests

Run all modules:

```bash
python tests/run_tests.py
```

Run selected modules:

```bash
python tests/run_tests.py auth
python tests/run_tests.py assets booking
```

List available modules:

```bash
python tests/run_tests.py --list
```

Wait for server before running:

```bash
python tests/run_tests.py --wait
```

Skip initial health check:

```bash
python tests/run_tests.py --no-health-check
```

## Current Test Modules

The active module map in `tests/run_tests.py` is:

- `auth` -> `tests.test_auth`
- `assets` -> `tests.test_assets`
- `booking` -> `tests.test_slot_booking`
- `site_project` -> `tests.test_site_project`
- `subcontractor` -> `tests.test_subcontractor`
- `file_upload` -> `tests.test_file_upload`

Note: `forgot_password` is no longer an active runner module.

## File Layout

```text
tests/
  __init__.py
  config.py
  utils.py
  run_tests.py
  test_auth.py
  test_assets.py
  test_slot_booking.py
  test_site_project.py
  test_subcontractor.py
  test_file_upload.py
  README.md
```

## Troubleshooting

1. `Server health check failed`: ensure API is running at `http://localhost:8080`.
2. `ModuleNotFoundError: requests`: install `requests` in your active environment.
3. Connection errors: verify host/port and local firewall/proxy settings.

## Notes

- Tests use real HTTP requests via `requests`.
- These are not isolated DB unit tests; run against a suitable local environment.
- Add new modules by extending `TEST_MODULES` in `tests/run_tests.py`.
