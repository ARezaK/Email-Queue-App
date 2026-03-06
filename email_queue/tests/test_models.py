from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from email_queue.models import EmailUnsubscribe, QueuedEmail


class QueuedEmailModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_create_queued_email(self):
        """Test creating a queued email"""
        qe = QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com"},
            scheduled_for=timezone.now(),
        )

        self.assertEqual(qe.status, "queued")
        self.assertEqual(qe.attempt_count, 0)
        self.assertIsNone(qe.sent_at)

    def test_unique_constraint(self):
        """Test unique constraint prevents duplicates"""
        scheduled = timezone.now()
        context = {"test": "data"}

        QueuedEmail.objects.create(
            user=self.user,
            to_email=self.user.email,
            email_type="password_reset",
            context=context,
            scheduled_for=scheduled,
        )

        # Trying to create duplicate with same to_email, email_type, scheduled_for, and context should raise IntegrityError
        with self.assertRaises(IntegrityError):
            QueuedEmail.objects.create(
                user=self.user,
                to_email=self.user.email,
                email_type="password_reset",
                context=context,  # Same context
                scheduled_for=scheduled,  # Same scheduled time
            )

    def test_str_representation(self):
        """Test string representation"""
        qe = QueuedEmail.objects.create(
            user=self.user,
            to_email="test@example.com",
            email_type="test_email",
            context={},
        )

        self.assertIn("test_email", str(qe))
        self.assertIn("test@example.com", str(qe))
        self.assertIn("queued", str(qe))

    def test_unsubscribe_unique_per_category(self):
        """Same email can unsubscribe per category, but not duplicate same category"""
        EmailUnsubscribe.objects.create(user=self.user, email=self.user.email.lower(), category="marketing")
        EmailUnsubscribe.objects.create(user=self.user, email=self.user.email.lower(), category="notification")

        with self.assertRaises(IntegrityError):
            EmailUnsubscribe.objects.create(user=self.user, email=self.user.email.lower(), category="marketing")
