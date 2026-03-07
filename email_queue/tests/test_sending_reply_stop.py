from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from email_queue.models import QueuedEmail
from email_queue.sending import send_queued_email
from email_queue.types import EmailTypeConfig


@override_settings(
    DEFAULT_FROM_EMAIL="noreply@example.com",
    SITE_URL="https://example.com",
    EMAIL_QUEUE_TYPES={
        "renewal_reminder_7_days": EmailTypeConfig(
            subject="Renew now",
            category="renewal",
            allow_inactive=True,
            require_verified_email=False,
            auto_stop_on_reply=True,
            auto_stop_scope="email_type",
        )
    },
)
class SendingReplyStopTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_sets_reply_to_when_auto_stop_enabled(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="renewal_reminder_7_days",
            context={"user_name": "Test"},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives",
            return_value=mocked_email,
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Renew now",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }
            success = send_queued_email(queued_email)

        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")
        self.assertIn("reply_to", mock_email_cls.call_args.kwargs)
        reply_to = mock_email_cls.call_args.kwargs["reply_to"][0]
        self.assertTrue(reply_to.startswith("email-reply+"))
        self.assertTrue(reply_to.endswith("@replies.example.com"))

    @override_settings(
        EMAIL_QUEUE_TYPES={
            "renewal_reminder_7_days": EmailTypeConfig(
                subject="Renew now",
                category="renewal",
                allow_inactive=True,
                require_verified_email=False,
                auto_stop_on_reply=False,
            )
        }
    )
    def test_does_not_set_reply_to_when_auto_stop_disabled(self):
        queued_email = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="renewal_reminder_7_days",
            context={"user_name": "Test"},
            scheduled_for=timezone.now(),
        )

        mocked_email = Mock()
        with patch("email_queue.sending.render_email") as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives",
            return_value=mocked_email,
        ) as mock_email_cls:
            mock_render.return_value = {
                "subject": "Renew now",
                "text_body": "Body text",
                "html_body": "<p>Body html</p>",
            }
            success = send_queued_email(queued_email)

        self.assertTrue(success)
        self.assertEqual(queued_email.status, "sent")
        self.assertNotIn("reply_to", mock_email_cls.call_args.kwargs)
