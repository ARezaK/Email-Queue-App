import json

from django.test import TestCase, override_settings
from django.utils import timezone

from email_queue.models import EmailReplyEvent, QueuedEmail
from email_queue.reply_stop import generate_reply_stop_token
from email_queue.types import EmailTypeConfig


@override_settings(
    ROOT_URLCONF="email_queue.urls",
    SECRET_KEY="test-secret",
    EMAIL_QUEUE_TYPES={
        "renewal_reminder_7_days": EmailTypeConfig(
            subject="Renew now",
            category="renewal",
            auto_stop_on_reply=True,
            auto_stop_scope="category",
            allow_inactive=True,
            require_verified_email=False,
        ),
    },
)
class ReplyStopWebhookTest(TestCase):
    def _payload(self, *, message_id: str, token: str, headers: dict | None = None):
        return {
            "message_id": message_id,
            "token": token,
            "from": "user@example.com",
            "to": f"email-reply+{token}@example.com",
            "subject": "Re: renew",
            "headers": headers or {},
        }

    def test_rejects_missing_or_invalid_bearer(self):
        token = generate_reply_stop_token(
            to_email="user@example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
        )

        response_missing = self.client.post(
            "/email-queue/webhooks/reply-stop/",
            data=json.dumps(self._payload(message_id="msg-auth-1", token=token)),
            content_type="application/json",
        )
        self.assertEqual(response_missing.status_code, 401)

        response_bad = self.client.post(
            "/email-queue/webhooks/reply-stop/",
            data=json.dumps(self._payload(message_id="msg-auth-2", token=token)),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong-secret",
        )
        self.assertEqual(response_bad.status_code, 401)

    def test_auto_reply_is_ignored(self):
        queued_email = QueuedEmail.objects.create(
            to_email="user@example.com",
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        token = generate_reply_stop_token(
            to_email="user@example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
            queued_email_id=queued_email.id,
        )

        response = self.client.post(
            "/email-queue/webhooks/reply-stop/",
            data=json.dumps(
                self._payload(
                    message_id="msg-auto-1",
                    token=token,
                    headers={"auto_submitted": "auto-replied"},
                )
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["action"], "ignored_auto_reply")
        self.assertEqual(body["cancelled_count"], 0)
        self.assertTrue(EmailReplyEvent.objects.filter(message_id="msg-auto-1", action="ignored_auto_reply").exists())

    def test_valid_reply_processes_and_duplicate_is_idempotent(self):
        qe = QueuedEmail.objects.create(
            to_email="user@example.com",
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        token = generate_reply_stop_token(
            to_email="user@example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
            queued_email_id=qe.id,
        )
        payload = self._payload(message_id="msg-process-1", token=token)

        first = self.client.post(
            "/email-queue/webhooks/reply-stop/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )
        second = self.client.post(
            "/email-queue/webhooks/reply-stop/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )

        qe.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["action"], "category_stop")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["action"], "duplicate")
        self.assertEqual(second.json()["cancelled_count"], 0)
        self.assertEqual(qe.status, "cancelled")
