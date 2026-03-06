from django.contrib.auth.models import User
from django.core import signing
from django.test import RequestFactory, TestCase, override_settings

from email_queue.models import EmailUnsubscribe
from email_queue.unsubscribe import (
    add_unsubscribe_footer,
    decode_unsubscribe_token,
    generate_unsubscribe_token,
)
from email_queue.views import unsubscribe_view


class EmailUnsubscribeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")
        self.factory = RequestFactory()

    def test_unsubscribe_token_round_trip(self):
        token = generate_unsubscribe_token(self.user.email, "marketing")
        payload = decode_unsubscribe_token(token)

        self.assertEqual(payload["email"], "test@example.com")
        self.assertEqual(payload["category"], "marketing")

    def test_unsubscribe_view_records_unsubscribe(self):
        token = generate_unsubscribe_token(self.user.email, "marketing")
        request = self.factory.get(f"/email-queue/unsubscribe/{token}/")

        response = unsubscribe_view(request, token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "You have been unsubscribed")
        self.assertTrue(
            EmailUnsubscribe.objects.filter(
                email=self.user.email.lower(),
                category="marketing",
            ).exists()
        )

    def test_unsubscribe_view_rejects_invalid_token(self):
        request = self.factory.get("/email-queue/unsubscribe/invalid-token/")
        response = unsubscribe_view(request, "invalid-token")

        self.assertEqual(response.status_code, 400)

    @override_settings(EMAIL_QUEUE_BASE_URL="https://casebasedlearning.ai")
    def test_add_unsubscribe_footer_adds_link(self):
        text_body, html_body = add_unsubscribe_footer(
            "Text content",
            "<p>HTML content</p>",
            self.user.email,
            "marketing",
        )

        self.assertIn("Unsubscribe:", text_body)
        self.assertIn("https://casebasedlearning.ai", text_body)
        self.assertIn(">Unsubscribe</a>", html_body)
        self.assertIn("email-queue/unsubscribe", html_body)

    @override_settings(
        SITE_URL="https://site-url.example",
        EMAIL_QUEUE_BASE_URL="",
        EMAIL_QUEUE_BASED_URL="",
    )
    def test_add_unsubscribe_footer_uses_site_url_fallback(self):
        text_body, html_body = add_unsubscribe_footer(
            "Text content",
            "<p>HTML content</p>",
            self.user.email,
            "marketing",
        )

        self.assertIn("https://site-url.example", text_body)
        self.assertIn("https://site-url.example", html_body)

    @override_settings(
        EMAIL_QUEUE_BASED_URL="https://based-url.example",
        EMAIL_QUEUE_BASE_URL="https://base-url.example",
        SITE_URL="https://site-url.example",
    )
    def test_add_unsubscribe_footer_prefers_email_queue_based_url(self):
        text_body, html_body = add_unsubscribe_footer(
            "Text content",
            "<p>HTML content</p>",
            self.user.email,
            "marketing",
        )

        self.assertIn("https://based-url.example", text_body)
        self.assertIn("https://based-url.example", html_body)
        self.assertNotIn("https://base-url.example", text_body)

    def test_decode_rejects_modified_token(self):
        token = generate_unsubscribe_token(self.user.email, "marketing")
        bad_token = f"{token}x"

        with self.assertRaises(signing.BadSignature):
            decode_unsubscribe_token(bad_token)
