from django.core import signing
from django.test import SimpleTestCase, override_settings

from email_queue.reply_stop import (
    build_reply_to_address,
    decode_reply_stop_token,
    generate_reply_stop_token,
    get_reply_stop_base_address,
)


class ReplyStopTokenTest(SimpleTestCase):
    def test_token_round_trip(self):
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
        token = generate_reply_stop_token(
            to_email="test@example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
        )

        with self.assertRaises(signing.BadSignature):
            decode_reply_stop_token(f"{token}x")

    @override_settings(SITE_URL="https://example.com")
    def test_base_address_defaults_from_site_url(self):
        self.assertEqual(get_reply_stop_base_address(), "email-reply@replies.example.com")

    @override_settings(EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS="reply@replies.example.com")
    def test_base_address_uses_setting_override(self):
        self.assertEqual(get_reply_stop_base_address(), "reply@replies.example.com")

    @override_settings(SITE_URL="https://example.com")
    def test_reply_to_address_contains_plus_token(self):
        token = generate_reply_stop_token(
            to_email="test@example.com",
            email_type="renewal_reminder_7_days",
            category="renewal",
        )

        address = build_reply_to_address(token)
        self.assertTrue(address.startswith("email-reply+"))
        self.assertTrue(address.endswith("@replies.example.com"))
