from django.contrib.auth.models import Permission, User
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
        self.assertIn("'all'", form.fields["category"].help_text)

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

    def test_query_string_reason_beats_the_admin_default(self):
        response = self.client.get(self.add_url, {"reason": "bounce"})

        self.assertEqual(response.context["adminform"].form["reason"].value(), "bounce")

    def test_saving_an_existing_row_unchanged_is_not_a_duplicate(self):
        row = EmailUnsubscribe.objects.create(email="jane@example.com", category="all", reason="bounce")
        change_url = reverse("admin:email_queue_emailunsubscribe_change", args=[row.pk])

        response = self.client.post(
            change_url, {"email": "jane@example.com", "category": "all", "reason": "bounce", "user": ""}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailUnsubscribe.objects.count(), 1)

    def test_changing_a_row_to_collide_with_another_is_a_form_error(self):
        EmailUnsubscribe.objects.create(email="jane@example.com", category="all", reason="bounce")
        other = EmailUnsubscribe.objects.create(email="jane@example.com", category="marketing")
        change_url = reverse("admin:email_queue_emailunsubscribe_change", args=[other.pk])

        response = self.client.post(
            change_url, {"email": " JANE@example.com", "category": "All", "reason": "link", "user": ""}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        other.refresh_from_db()
        self.assertEqual(other.category, "marketing")

    def test_staff_with_only_view_and_add_can_add_a_row(self):
        """The permissions an automated staff account gets: view + add here, view on users."""
        limited = User.objects.create_user(username="limited", email="limited@example.com", is_staff=True)
        limited.user_permissions.set(
            Permission.objects.filter(codename__in=["view_emailunsubscribe", "add_emailunsubscribe", "view_user"])
        )
        jane = User.objects.create_user(username="jane", email="jane@example.com")
        self.client.force_login(limited)

        self.assertEqual(self.client.get(self.add_url).status_code, 200)
        response = self.client.post(self.add_url, {"email": "jane@example.com", "category": "all", "reason": "bounce"})

        self.assertEqual(response.status_code, 302)
        row = EmailUnsubscribe.objects.get()
        self.assertEqual((row.email, row.category, row.reason, row.user), ("jane@example.com", "all", "bounce", jane))
