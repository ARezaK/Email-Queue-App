from django.test import TestCase

from email_queue.models import EmailUnsubscribe
from email_queue.types import ALL_CATEGORY
from email_queue.unsubscribe import is_unsubscribed, unsubscribe_match, unsubscribe_skip_text


class UnsubscribeMatchTest(TestCase):
    def test_all_category_constant(self):
        self.assertEqual(ALL_CATEGORY, "all")

    def test_category_row_matches_only_its_category(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="marketing")

        self.assertEqual(unsubscribe_match("a@example.com", "marketing"), "marketing")
        self.assertIsNone(unsubscribe_match("a@example.com", "notification"))
        self.assertTrue(is_unsubscribed("a@example.com", "marketing"))
        self.assertFalse(is_unsubscribed("a@example.com", "notification"))

    def test_all_row_matches_every_category(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="all")

        self.assertEqual(unsubscribe_match("a@example.com", "marketing"), "all")
        self.assertEqual(unsubscribe_match("a@example.com", "notification"), "all")
        self.assertTrue(is_unsubscribed("a@example.com", "anything_else"))

    def test_all_wins_when_both_rows_exist(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="marketing")
        EmailUnsubscribe.objects.create(email="a@example.com", category="all")

        self.assertEqual(unsubscribe_match("a@example.com", "marketing"), "all")

    def test_match_normalises_address_and_category(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="marketing_emails")

        self.assertEqual(unsubscribe_match("  A@Example.com ", "Marketing Emails"), "marketing_emails")

    def test_no_row_no_match(self):
        self.assertIsNone(unsubscribe_match("nobody@example.com", "marketing"))
        self.assertFalse(is_unsubscribed("nobody@example.com", "marketing"))

    def test_other_address_not_affected(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="all")

        self.assertIsNone(unsubscribe_match("b@example.com", "marketing"))

    def test_skip_text(self):
        self.assertEqual(unsubscribe_skip_text("all"), "Recipient unsubscribed from all emails")
        self.assertEqual(
            unsubscribe_skip_text("marketing_emails"), "Recipient unsubscribed from marketing emails emails"
        )
