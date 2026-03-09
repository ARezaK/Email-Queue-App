from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from email_queue.models import EmailUnsubscribe, QueuedEmail
from email_queue.sending import send_queued_email
from email_queue.types import EmailTypeConfig


@override_settings(
    DEFAULT_FROM_EMAIL="noreply@example.com",
    EMAIL_QUEUE_BASE_URL="https://example.com",
    EMAIL_QUEUE_TYPES={
        "password_reset": EmailTypeConfig(
            subject="Reset your password",
            category="notification",
            allow_inactive=True,
            require_verified_email=False,
            skip_sending_if_unsubscribed=True,
        )
    },
)
class SendQueuedEmailUnsubscribeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_send_skips_unsubscribed_recipient(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24},
            scheduled_for=timezone.now(),
        )
        EmailUnsubscribe.objects.create(email=self.user.email.lower(), category="notification", user=self.user)

        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives"
        ) as mock_email:
            success = send_queued_email(queued_email)

        queued_email.refresh_from_db()
        self.assertFalse(success)
        self.assertEqual(queued_email.status, "skipped")
        self.assertIn("unsubscribed", queued_email.failure_reason.lower())
        mock_render.assert_not_called()
        mock_email.assert_not_called()

    def test_send_adds_unsubscribe_footer(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives", return_value=mocked_email
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Reset your password",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }

            success = send_queued_email(queued_email)

        queued_email.refresh_from_db()
        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")

        body = mock_email_cls.call_args.kwargs["body"]
        self.assertIn("Unsubscribe:", body)
        self.assertIn("email-queue/unsubscribe", body)

        html_body = mocked_email.attach_alternative.call_args.args[0]
        self.assertIn(">Unsubscribe</a>", html_body)

    @override_settings(
        EMAIL_QUEUE_TYPES={
            "password_reset": EmailTypeConfig(
                subject="Reset your password",
                category="notification",
                allow_inactive=True,
                require_verified_email=False,
                skip_sending_if_unsubscribed=True,
                include_unsubscribe_footer=False,
            )
        }
    )
    def test_send_can_disable_unsubscribe_footer(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives", return_value=mocked_email
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Reset your password",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }

            success = send_queued_email(queued_email)

        queued_email.refresh_from_db()
        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")

        body = mock_email_cls.call_args.kwargs["body"]
        self.assertNotIn("Unsubscribe:", body)

        html_body = mocked_email.attach_alternative.call_args.args[0]
        self.assertNotIn(">Unsubscribe</a>", html_body)

    @override_settings(
        EMAIL_QUEUE_TYPES={
            "password_reset": EmailTypeConfig(
                subject="Reset your password",
                category="notification",
                allow_inactive=True,
                require_verified_email=False,
                skip_sending_if_unsubscribed=False,
            )
        }
    )
    def test_footer_default_follows_skip_sending_if_unsubscribed_when_not_overridden(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives", return_value=mocked_email
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Reset your password",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }

            success = send_queued_email(queued_email)

        queued_email.refresh_from_db()
        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")

        body = mock_email_cls.call_args.kwargs["body"]
        self.assertNotIn("Unsubscribe:", body)

    @override_settings(
        EMAIL_QUEUE_TYPES={
            "password_reset": EmailTypeConfig(
                subject="Reset your password",
                category="notification",
                allow_inactive=True,
                require_verified_email=False,
                skip_sending_if_unsubscribed=False,
                include_unsubscribe_footer=True,
            )
        }
    )
    def test_footer_can_be_forced_on_even_when_unsubscribe_enforcement_disabled(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives", return_value=mocked_email
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Reset your password",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }

            success = send_queued_email(queued_email)

        queued_email.refresh_from_db()
        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")

        body = mock_email_cls.call_args.kwargs["body"]
        self.assertIn("Unsubscribe:", body)
