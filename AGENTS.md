# Repository Guidelines

## Project Structure & Module Organization
`email_queue/` is the Django app package. Core entry points are:
- `api.py` for queueing (`queue_email`)
- `sending.py` and `rendering.py` for delivery and template rendering
- `models.py` for `QueuedEmail` and related DB entities
- `middleware.py` for click tracking
- `types.py` and `schemas.py` for email-type config and context validation

Operational code lives in `email_queue/management/commands/send_queued_emails.py`.  
Tests are in `email_queue/tests/` with one module per behavior area (API, rendering, UTM, click tracking, command concurrency).  
Schema changes belong in `email_queue/migrations/`. Package metadata is in `setup.py`.

## Build, Test, and Development Commands
- `pip install -e .` installs this app in editable mode for local development.
- `python manage.py send_queued_emails` runs the queue worker from a host Django project.
- `python manage.py send_queued_emails --rate-limit=20 --max-retries=5 --retry-delay=60` runs worker with custom throughput/retry settings.
- `python manage.py test email_queue` runs the full test suite.
- `python manage.py test email_queue.tests.test_api` runs a focused module during iteration.

## Coding Style & Naming Conventions
Use Python 3 with 4-space indentation and PEP 8 style. Keep functions/modules focused and explicit (current code favors typed signatures and clear docstrings for public APIs).  
Naming patterns:
- modules/files: `snake_case.py`
- functions/variables: `snake_case`
- classes/tests: `PascalCase` (e.g., `QueueEmailAPITest`)
- constants/config maps: `UPPER_SNAKE_CASE` (e.g., `EMAIL_TYPES`)

## Testing Guidelines
Tests use Django’s test framework (`TestCase` and `TransactionTestCase`).  
Add tests in `email_queue/tests/test_<feature>.py`, and name methods `test_<behavior>`.  
For queue worker, include concurrency/idempotency coverage when behavior changes. For templates/tracking changes, include both happy path and edge cases.

## Commit & Pull Request Guidelines
Current history uses short, imperative subjects (e.g., `Avoid loading all users`). Follow that pattern:
- subject line in imperative mood, <= 72 chars
- one logical change per commit

PRs should include:
- concise summary of behavior change
- migration notes (if models changed)
- test evidence (exact command run and result)
- linked issue/reference when applicable
