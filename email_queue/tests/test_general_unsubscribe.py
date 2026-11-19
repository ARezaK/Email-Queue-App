from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from email_queue.api import queue_email
from email_queue.models import EmailUnsubscribe, QueuedEmail
from email_queue.schemas import PasswordResetContext
from email_queue.sending import send_queued_email
from email_queue.types import ALL_CATEGORY, EmailTypeConfig
from email_queue.unsubscribe import (
    get_email_category,
    is_unsubscribed,
    record_unsubscribe,
    unsubscribe_match,
    unsubscribe_skip_text,
)


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


class ReservedCategoryTest(TestCase):
    def test_dataclass_config_rejects_all(self):
        with self.assertRaisesMessage(ValueError, "reserved"):
            EmailTypeConfig(subject="Hi", category="all")

    def test_dataclass_config_rejects_all_any_casing(self):
        with self.assertRaisesMessage(ValueError, "reserved"):
            EmailTypeConfig(subject="Hi", category=" All ")

    def test_dataclass_config_accepts_other_categories(self):
        self.assertEqual(EmailTypeConfig(subject="Hi", category="marketing").category, "marketing")
        self.assertEqual(EmailTypeConfig(subject="Hi").category, "notification")

    @override_settings(EMAIL_QUEUE_TYPES={"promo": {"subject": "Hi", "category": "All"}})
    def test_dict_config_rejected_by_get_email_category(self):
        with self.assertRaisesMessage(ValueError, "reserved"):
            get_email_category("promo")

    @override_settings(EMAIL_QUEUE_TYPES={"promo": {"subject": "Hi", "category": "Marketing Emails"}})
    def test_dict_config_other_category_still_works(self):
        self.assertEqual(get_email_category("promo"), "marketing_emails")


class UnsubscribeReasonFieldTest(TestCase):
    def test_default_reason_is_link(self):
        row = EmailUnsubscribe.objects.create(email="a@example.com", category="marketing")
        self.assertEqual(row.reason, "link")

    def test_reason_choices(self):
        self.assertEqual(
            [value for value, _label in EmailUnsubscribe.REASON_CHOICES],
            ["link", "bounce", "admin"],
        )
        row = EmailUnsubscribe.objects.create(email="a@example.com", category="all", reason="bounce")
        self.assertEqual(row.get_reason_display(), "Email bounced")


class UnsubscribeReasonMigrationTest(TransactionTestCase):
    """Rows that existed before 0006 read back with reason 'link'."""

    def test_existing_rows_get_link(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("email_queue", "0005_emailreplyevent")])
        old_apps = executor.loader.project_state([("email_queue", "0005_emailreplyevent")]).apps
        OldUnsubscribe = old_apps.get_model("email_queue", "EmailUnsubscribe")
        OldUnsubscribe.objects.create(email="old@example.com", category="marketing")

        executor = MigrationExecutor(connection)
        executor.migrate([("email_queue", "0006_emailunsubscribe_reason")])

        self.assertEqual(EmailUnsubscribe.objects.get(email="old@example.com").reason, "link")

    def tearDown(self):
        # Leave the schema fully migrated for whatever test runs next.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class RecordUnsubscribeReasonTest(TestCase):
    def test_default_reason_is_link(self):
        row = record_unsubscribe("a@example.com", "marketing")
        self.assertEqual(row.reason, "link")

    def test_reason_stored_on_create(self):
        row = record_unsubscribe("A@Example.com", "all", reason="bounce")

        self.assertEqual(row.email, "a@example.com")
        self.assertEqual(row.category, "all")
        self.assertEqual(row.reason, "bounce")

    def test_first_reason_kept_on_repeat(self):
        record_unsubscribe("a@example.com", "all", reason="bounce")
        row = record_unsubscribe("a@example.com", "all", reason="link")

        self.assertEqual(row.reason, "bounce")
        self.assertEqual(EmailUnsubscribe.objects.filter(email="a@example.com", category="all").count(), 1)

    def test_repeat_still_refreshes_user_and_time(self):
        first = record_unsubscribe("a@example.com", "marketing")
        self.assertIsNone(first.user)
        user = User.objects.create_user(username="a", email="a@example.com")

        again = record_unsubscribe("a@example.com", "marketing")

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.user, user)
        self.assertGreaterEqual(again.unsubscribed_at, first.unsubscribed_at)

    def test_unknown_reason_rejected(self):
        with self.assertRaisesMessage(ValueError, "reason"):
            record_unsubscribe("a@example.com", "marketing", reason="because")


CONTEXT = {"user_name": "Test", "reset_link": "https://example.com/reset", "expires_hours": 24}
RENDERED = {"subject": "Hello", "text_body": "Body text", "html_body": "<p>Body html</p>"}


@override_settings(
    DEFAULT_FROM_EMAIL="noreply@example.com",
    EMAIL_QUEUE_BASE_URL="https://example.com",
    EMAIL_QUEUE_TYPES={
        "promo": EmailTypeConfig(subject="Promo", category="marketing", require_verified_email=False),
        "receipt": EmailTypeConfig(
            subject="Receipt",
            category="notification",
            require_verified_email=False,
            skip_sending_if_unsubscribed=False,
        ),
    },
    EMAIL_QUEUE_CONTEXT_SCHEMAS={"promo": PasswordResetContext, "receipt": PasswordResetContext},
)
class GeneralUnsubscribeEnforcementTest(TestCase):
    def _queued(self, email_type="promo"):
        return QueuedEmail.objects.create(
            to_email="a@example.com", email_type=email_type, context=CONTEXT, scheduled_for=timezone.now()
        )

    def _send(self, queued_email):
        mocked_email = Mock()
        with patch("email_queue.sending.render_email", return_value=dict(RENDERED)) as mock_render, patch(
            "email_queue.sending.EmailMultiAlternatives", return_value=mocked_email
        ):
            success = send_queued_email(queued_email)
        queued_email.refresh_from_db()
        return success, mock_render

    def test_queue_skips_address_unsubscribed_from_all(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="all", reason="bounce")

        queued = queue_email(to_email="a@example.com", email_type="promo", context=CONTEXT)

        self.assertEqual(queued.status, "skipped")
        self.assertEqual(queued.failure_reason, "Recipient unsubscribed from all emails")

    def test_queue_skip_text_for_category_row_unchanged(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="marketing")

        queued = queue_email(to_email="a@example.com", email_type="promo", context=CONTEXT)

        self.assertEqual(queued.status, "skipped")
        self.assertEqual(queued.failure_reason, "Recipient unsubscribed from marketing emails")

    def test_queue_not_skipped_without_rows(self):
        queued = queue_email(to_email="a@example.com", email_type="promo", context=CONTEXT)

        self.assertEqual(queued.status, "queued")
        self.assertEqual(queued.failure_reason, "")

    def test_send_skips_when_all_row_added_after_queueing(self):
        queued = self._queued()
        EmailUnsubscribe.objects.create(email="a@example.com", category="all", reason="bounce")

        success, mock_render = self._send(queued)

        self.assertFalse(success)
        self.assertEqual(queued.status, "skipped")
        self.assertEqual(queued.failure_reason, "Recipient unsubscribed from all emails")
        mock_render.assert_not_called()

    def test_send_skip_text_for_category_row_unchanged(self):
        queued = self._queued()
        EmailUnsubscribe.objects.create(email="a@example.com", category="marketing")

        success, _ = self._send(queued)

        self.assertFalse(success)
        self.assertEqual(queued.failure_reason, "Recipient unsubscribed from marketing emails")

    def test_type_that_ignores_unsubscribes_still_queues_and_sends(self):
        EmailUnsubscribe.objects.create(email="a@example.com", category="all", reason="bounce")

        queued = queue_email(to_email="a@example.com", email_type="receipt", context=CONTEXT)
        self.assertEqual(queued.status, "queued")

        success, _ = self._send(queued)
        self.assertTrue(success)
        self.assertEqual(queued.status, "sent")
