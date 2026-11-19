"""Minimal settings so this package's tests run without a host project.

Usage: python -m django test email_queue.tests.test_unsubscribe --settings=email_queue.tests.settings

Some modules do not pass with these settings and should be run from a host project:
test_api, test_rendering and test_utm_tracking need a host project's email types and templates,
and the concurrency test in test_send_queued_emails_command needs a database with real row
locking (not SQLite).
"""

SECRET_KEY = "email-queue-tests"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "email_queue",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ROOT_URLCONF = "email_queue.tests.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DEFAULT_FROM_EMAIL = "noreply@example.com"
USE_TZ = True
