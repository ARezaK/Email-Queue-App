from django.test import TestCase, override_settings
from django.utils import timezone

from email_queue.models import EmailReplyEvent, EmailUnsubscribe, QueuedEmail
from email_queue.reply_stop import generate_reply_stop_token
from email_queue.reply_stop_service import ReplyStopService
from email_queue.types import EmailTypeConfig


@override_settings(
    EMAIL_QUEUE_TYPES={
        "renewal_reminder_30_days": EmailTypeConfig(
            subject="Renewal reminder",
            category="renewal",
            auto_stop_on_reply=True,
            auto_stop_scope="category",
            allow_inactive=True,
            require_verified_email=False,
        ),
        "renewal_reminder_7_days": EmailTypeConfig(
            subject="Renewal reminder",
            category="renewal",
            auto_stop_on_reply=False,
            allow_inactive=True,
            require_verified_email=False,
        ),
        "weekly_digest": EmailTypeConfig(
            subject="Weekly digest",
            category="marketing",
            auto_stop_on_reply=True,
            auto_stop_scope="email_type",
            allow_inactive=True,
            require_verified_email=False,
        ),
        "feature_announcement": EmailTypeConfig(
            subject="Feature announcement",
            category="marketing",
            auto_stop_on_reply=False,
            allow_inactive=True,
            require_verified_email=False,
        ),
    }
)
class ReplyStopServiceTest(TestCase):
    def setUp(self):
        self.service = ReplyStopService()
        self.to_email = "user@example.com"

    def test_category_scope_unsubscribes_and_cancels_category_rows(self):
        qe1 = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="renewal_reminder_30_days",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        qe2 = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="failed",
        )
        qe3 = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="weekly_digest",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )

        token = generate_reply_stop_token(
            to_email=self.to_email,
            email_type="renewal_reminder_30_days",
            category="renewal",
            queued_email_id=qe1.id,
        )
        result = self.service.process_payload(
            {
                "message_id": "msg-category-1",
                "token": token,
                "from": self.to_email,
                "to": "email-reply+token@example.com",
                "subject": "please stop",
                "headers": {},
            }
        )

        qe1.refresh_from_db()
        qe2.refresh_from_db()
        qe3.refresh_from_db()

        self.assertEqual(result["action"], "category_stop")
        self.assertEqual(result["cancelled_count"], 2)
        self.assertTrue(result["unsubscribed"])
        self.assertEqual(qe1.status, "cancelled")
        self.assertEqual(qe2.status, "cancelled")
        self.assertEqual(qe3.status, "queued")
        self.assertTrue(EmailUnsubscribe.objects.filter(email=self.to_email, category="renewal").exists())
        self.assertTrue(EmailReplyEvent.objects.filter(message_id="msg-category-1", action="category_stop").exists())

    def test_email_type_scope_cancels_only_matching_type(self):
        qe1 = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="weekly_digest",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        qe2 = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="feature_announcement",
            context={},
            scheduled_for=timezone.now(),
            status="failed",
        )

        token = generate_reply_stop_token(
            to_email=self.to_email,
            email_type="weekly_digest",
            category="marketing",
            queued_email_id=qe1.id,
        )
        result = self.service.process_payload(
            {
                "message_id": "msg-type-1",
                "token": token,
                "from": self.to_email,
                "to": "email-reply+token@example.com",
                "subject": "pause this",
                "headers": {},
            }
        )

        qe1.refresh_from_db()
        qe2.refresh_from_db()

        self.assertEqual(result["action"], "email_type_stop")
        self.assertEqual(result["cancelled_count"], 1)
        self.assertFalse(result["unsubscribed"])
        self.assertEqual(qe1.status, "cancelled")
        self.assertEqual(qe2.status, "failed")
        self.assertFalse(EmailUnsubscribe.objects.filter(email=self.to_email, category="marketing").exists())

    def test_non_opt_in_type_is_ignored(self):
        qe = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="feature_announcement",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        token = generate_reply_stop_token(
            to_email=self.to_email,
            email_type="feature_announcement",
            category="marketing",
            queued_email_id=qe.id,
        )

        result = self.service.process_payload(
            {
                "message_id": "msg-ignored-1",
                "token": token,
                "from": self.to_email,
                "to": "email-reply+token@example.com",
                "subject": "re",
                "headers": {},
            }
        )

        qe.refresh_from_db()
        self.assertEqual(result["action"], "ignored_not_enabled")
        self.assertEqual(result["cancelled_count"], 0)
        self.assertFalse(result["unsubscribed"])
        self.assertEqual(qe.status, "queued")

    def test_duplicate_message_id_does_not_apply_again(self):
        qe = QueuedEmail.objects.create(
            to_email=self.to_email,
            email_type="weekly_digest",
            context={},
            scheduled_for=timezone.now(),
            status="queued",
        )
        token = generate_reply_stop_token(
            to_email=self.to_email,
            email_type="weekly_digest",
            category="marketing",
            queued_email_id=qe.id,
        )
        payload = {
            "message_id": "msg-duplicate-1",
            "token": token,
            "from": self.to_email,
            "to": "email-reply+token@example.com",
            "subject": "re",
            "headers": {},
        }

        first = self.service.process_payload(payload)
        second = self.service.process_payload(payload)

        qe.refresh_from_db()
        self.assertEqual(first["action"], "email_type_stop")
        self.assertEqual(second["action"], "duplicate")
        self.assertEqual(second["cancelled_count"], 0)
        self.assertEqual(qe.status, "cancelled")
