from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone

from email_queue.models import QueuedEmail
from email_queue.reply_stop import (
    build_reply_to_address,
    decode_reply_stop_token,
    generate_reply_stop_token,
    get_reply_stop_base_address,
)


class ReplyStopTokenTest(TestCase):
    def test_legacy_token_round_trip(self):
        token = generate_reply_stop_token(
            to_email="Test@Example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
        )

        payload = decode_reply_stop_token(token)
        self.assertEqual(payload["to_email"], "test@example.com")
        self.assertEqual(payload["email_type"], "renewal_reminder_7_days")
        self.assertEqual(payload["category"], "renewal")

    def test_bad_token_rejected(self):
        queued_email = QueuedEmail.objects.create(
            to_email="test@example.com",
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="sent",
        )
        token = generate_reply_stop_token(
            to_email=queued_email.to_email,
            email_type=queued_email.email_type,
            category="renewal",
            queued_email_id=queued_email.id,
        )

        with self.assertRaises(signing.BadSignature):
            decode_reply_stop_token(f"{token}x")

    def test_compact_token_round_trip(self):
        queued_email = QueuedEmail.objects.create(
            to_email="Test@Example.com",
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="sent",
        )

        token = generate_reply_stop_token(
            to_email=queued_email.to_email,
            email_type=queued_email.email_type,
            category="renewal",
            queued_email_id=queued_email.id,
        )
        payload = decode_reply_stop_token(token)
        self.assertEqual(payload["to_email"], "test@example.com")
        self.assertEqual(payload["email_type"], "renewal_reminder_7_days")
        self.assertEqual(payload["category"], "notification")

    @override_settings(SITE_URL="https://example.com")
    def test_base_address_defaults_from_site_url(self):
        self.assertEqual(get_reply_stop_base_address(), "email-reply@replies.example.com")

    @override_settings(EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS="reply@replies.example.com")
    def test_base_address_uses_setting_override(self):
        self.assertEqual(get_reply_stop_base_address(), "reply@replies.example.com")

    @override_settings(SITE_URL="https://example.com")
    def test_reply_to_address_contains_plus_token(self):
        queued_email = QueuedEmail.objects.create(
            to_email="test@example.com",
            email_type="renewal_reminder_7_days",
            context={},
            scheduled_for=timezone.now(),
            status="sent",
        )

        token = generate_reply_stop_token(
            to_email=queued_email.to_email,
            email_type=queued_email.email_type,
            category="renewal",
            queued_email_id=queued_email.id,
        )

        address = build_reply_to_address(token)
        self.assertTrue(address.startswith("email-reply+"))
        self.assertTrue(address.endswith("@replies.example.com"))
        local_part = address.split("@", 1)[0]
        self.assertLessEqual(len(local_part), 64)
