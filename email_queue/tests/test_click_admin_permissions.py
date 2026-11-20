from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from email_queue.models import EmailClick


class EmailClickAdminPermissionTest(TestCase):
    """Deleting click records follows the staff user's permissions, like every other model."""

    def setUp(self):
        self.model_admin = admin.site._registry[EmailClick]
        self.staff = get_user_model().objects.create_user(username="staff", password="x", is_staff=True)

    def _request(self):
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.get(pk=self.staff.pk)
        return request

    def test_staff_without_the_permission_cannot_delete(self):
        self.assertFalse(self.model_admin.has_delete_permission(self._request()))

    def test_staff_with_the_permission_can_delete(self):
        self.staff.user_permissions.add(Permission.objects.get(codename="delete_emailclick"))

        self.assertTrue(self.model_admin.has_delete_permission(self._request()))
