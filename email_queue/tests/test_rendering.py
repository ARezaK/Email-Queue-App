from django.test import TestCase

from email_queue.rendering import render_email


class EmailRenderingTest(TestCase):
    def test_render_password_reset_email(self):
        """Test rendering password reset email"""
        context = {
            "user_name": "John",
            "reset_link": "http://example.com/reset/token123",
            "expires_hours": 24,
        }

        result = render_email("password_reset", context)

        self.assertIn("subject", result)
        self.assertIn("text_body", result)
        self.assertIn("html_body", result)

        # Check subject
        self.assertIn("password", result["subject"].lower())

        # Check text body contains context
        self.assertIn("http://example.com/reset/token123", result["text_body"])
        self.assertIn("24 hours", result["text_body"])

        # Check HTML body contains context (with UTM parameters if email_id provided)
        self.assertIsNotNone(result["html_body"])
        self.assertIn("http://example.com/reset/token123", result["html_body"])
        self.assertIn("24 hours", result["html_body"])

    def test_render_registration_welcome(self):
        """Test rendering registration welcome email"""
        context = {
            "user_name": "Jane",
            "site_url": "https://casebasedlearning.ai",
            "support_email": "support@casebasedlearning.ai",
            "tutorial_url": "/case/2",
        }

        result = render_email("registration_welcome", context)

        self.assertIn("Welcome", result["subject"])
        self.assertIn("Case Based Learning", result["text_body"])
        self.assertIn("/case/2", result["text_body"])

    def test_render_unknown_email_type(self):
        """Test that unknown email_type raises ValueError"""
        with self.assertRaises(ValueError):
            render_email("nonexistent_email", {})

    def test_subject_template_rendering(self):
        """Test that subject renders with context variables"""
        context = {
            "user_name": "Alice",
            "site_url": "https://casebasedlearning.ai",
            "support_email": "support@casebasedlearning.ai",
            "tutorial_url": "https://casebasedlearning.ai/case/2",
        }

        result = render_email("registration_welcome", context)

        # Subject should have user_name rendered
        self.assertIn("Alice", result["subject"])

    def test_subject_override(self):
        context = {
            "user_name": "Sam",
            "coupon_code": "NEWCUST",
            "discount_percentage": 15,
            "profile_url": "https://casebasedlearning.ai/profile#subscription",
            "subject_override": "Reminder: complete your subscription",
        }

        result = render_email("abandoned_checkout", context)

        self.assertEqual(result["subject"], "Reminder: complete your subscription")

    def test_render_subscription_confirmation_attending_includes_cme(self):
        context = {
            "user_name": "Riley",
            "start_date": "January 1, 2025",
            "expiration_date": "January 1, 2026",
            "account_url": "https://casebasedlearning.ai/profile",
            "cme_code": "CBLFREE",
            "show_cme": True,
        }

        result = render_email("subscription_confirmation", context)

        self.assertIn("Earn CME", result["text_body"])
        self.assertIn("CBLFREE", result["text_body"])
        self.assertIn("Earn CME", result["html_body"])
        self.assertIn("CBLFREE", result["html_body"])

    def test_render_subscription_confirmation_student_excludes_cme(self):
        context = {
            "user_name": "Avery",
            "start_date": "January 1, 2025",
            "expiration_date": "February 1, 2025",
            "account_url": "https://casebasedlearning.ai/profile",
            "cme_code": "CBLFREE",
            "show_cme": False,
        }

        result = render_email("subscription_confirmation", context)

        self.assertNotIn("Earn CME", result["text_body"])
        self.assertNotIn("CBLFREE", result["text_body"])
        self.assertNotIn("Earn CME", result["html_body"])
        self.assertNotIn("CBLFREE", result["html_body"])
