from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from email_queue.api import queue_email
from email_queue.models import EmailUnsubscribe, QueuedEmail
from email_queue.unsubscribe import get_email_category


class QueueEmailAPITest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_queue_email_with_user(self):
        """Test queuing email to user's email"""
        qe = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
        )

        self.assertEqual(qe.to_email, self.user.email)
        self.assertEqual(qe.status, "queued")

    def test_queue_email_with_to_email(self):
        """Test queuing email with explicit to_email"""
        qe = queue_email(
            to_email="other@example.com",
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
        )

        self.assertEqual(qe.to_email, "other@example.com")

    def test_queue_email_idempotent_same_context(self):
        """Test that queuing same email with same context twice returns existing"""
        scheduled = timezone.now()
        context = {"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24}

        qe1 = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context=context,
            scheduled_for=scheduled,
        )

        qe2 = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context=context,  # Same context
            scheduled_for=scheduled,
        )

        self.assertEqual(qe1.id, qe2.id)
        # Only count password_reset emails (setUp creates user which triggers registration_welcome via signal)
        self.assertEqual(QueuedEmail.objects.filter(email_type="password_reset").count(), 1)

    def test_queue_email_different_context_creates_new(self):
        """Test that queuing same email with different context creates new email"""
        scheduled = timezone.now()

        qe1 = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com/token1", "expires_hours": 24},
            scheduled_for=scheduled,
        )

        qe2 = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={
                "user_name": "Test",
                "reset_link": "http://example.com/token2",
                "expires_hours": 24,
            },  # Different context
            scheduled_for=scheduled,
        )

        self.assertNotEqual(qe1.id, qe2.id)
        # Only count password_reset emails (setUp creates user which triggers registration_welcome via signal)
        self.assertEqual(QueuedEmail.objects.filter(email_type="password_reset").count(), 2)

    def test_queue_email_validation_error(self):
        """Test that invalid context raises ValidationError"""
        with self.assertRaises(ValidationError):
            queue_email(
                to_email=self.user.email,
                email_type="password_reset",
                context={"user_name": "Test"},  # Missing reset_link
            )

    def test_queue_email_unknown_type(self):
        """Test that unknown email_type raises ValueError"""
        with self.assertRaises(ValueError):
            queue_email(
                to_email=self.user.email,
                email_type="nonexistent_email",
                context={},
            )

    def test_queue_email_with_batch_id(self):
        """Test queuing email with batch_id"""
        qe = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
            batch_id="test_batch_123",
        )

        self.assertEqual(qe.batch_id, "test_batch_123")

    def test_queue_email_skips_unsubscribed_category(self):
        """Test queuing skips users unsubscribed from the email category"""
        EmailUnsubscribe.objects.create(
            user=self.user,
            email=self.user.email.lower(),
            category=get_email_category("password_reset"),
        )

        qe = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
        )

        self.assertEqual(qe.status, "skipped")
        self.assertIn("unsubscribed", qe.failure_reason.lower())

    def test_queue_email_allows_other_categories(self):
        """Test category-specific unsubscribe does not block other categories"""
        EmailUnsubscribe.objects.create(
            user=self.user,
            email=self.user.email.lower(),
            category="marketing",
        )

        qe = queue_email(
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
        )

        self.assertEqual(qe.status, "queued")
