from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from email_queue.models import EmailUnsubscribe


class EmailUnsubscribeAdminTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="boss", email="boss@example.com", password="pw")
        self.client.force_login(self.staff)
        self.add_url = reverse("admin:email_queue_emailunsubscribe_add")
        self.list_url = reverse("admin:email_queue_emailunsubscribe_changelist")

    def test_add_form_starts_with_reason_admin_and_all_hint(self):
        response = self.client.get(self.add_url)

        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assertEqual(form["reason"].value(), "admin")
        self.assertContains(response, "to stop every email to this address")

    def test_add_normalises_and_links_user(self):
        jane = User.objects.create_user(username="jane", email="Jane@Example.com")

        response = self.client.post(
            self.add_url, {"email": "  JANE@example.com ", "category": "All", "reason": "admin", "user": ""}
        )

        self.assertEqual(response.status_code, 302)
        row = EmailUnsubscribe.objects.get()
        self.assertEqual(row.email, "jane@example.com")
        self.assertEqual(row.category, "all")
        self.assertEqual(row.reason, "admin")
        self.assertEqual(row.user, jane)

    def test_add_keeps_chosen_user(self):
        other = User.objects.create_user(username="other", email="other@example.com")

        self.client.post(
            self.add_url, {"email": "x@example.com", "category": "marketing", "reason": "admin", "user": other.pk}
        )

        self.assertEqual(EmailUnsubscribe.objects.get().user, other)

    def test_add_without_matching_user_leaves_user_blank(self):
        self.client.post(self.add_url, {"email": "x@example.com", "category": "all", "reason": "bounce", "user": ""})

        row = EmailUnsubscribe.objects.get()
        self.assertIsNone(row.user)
        self.assertEqual(row.reason, "bounce")

    def test_duplicate_with_different_casing_is_a_form_error(self):
        EmailUnsubscribe.objects.create(email="jane@example.com", category="all", reason="bounce")

        response = self.client.post(
            self.add_url, {"email": "Jane@Example.com", "category": "ALL", "reason": "admin", "user": ""}
        )

        self.assertEqual(response.status_code, 200)  # form shown again, not a server error
        self.assertTrue(response.context["adminform"].form.errors)
        self.assertContains(response, "already exists")
        self.assertEqual(EmailUnsubscribe.objects.count(), 1)

    def test_list_shows_reason_column_and_filter(self):
        EmailUnsubscribe.objects.create(email="jane@example.com", category="all", reason="bounce")

        response = self.client.get(self.list_url)
        self.assertContains(response, "Email bounced")

        filtered = self.client.get(self.list_url, {"reason__exact": "link"})
        self.assertEqual(filtered.status_code, 200)
        self.assertNotContains(filtered, "jane@example.com")

    def test_delete_resubscribes(self):
        row = EmailUnsubscribe.objects.create(email="jane@example.com", category="all", reason="bounce")

        self.client.post(reverse("admin:email_queue_emailunsubscribe_delete", args=[row.pk]), {"post": "yes"})

        self.assertFalse(EmailUnsubscribe.objects.exists())
