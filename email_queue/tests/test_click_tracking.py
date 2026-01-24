from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from email_queue.middleware import EmailClickTrackingMiddleware, get_client_ip
from email_queue.models import EmailClick, QueuedEmail


class GetClientIPTest(TestCase):
    """Test IP address extraction from requests."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_get_ip_from_remote_addr(self):
        """Test extracting IP from REMOTE_ADDR"""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.1"

        ip = get_client_ip(request)
        self.assertEqual(ip, "192.168.1.1")

    def test_get_ip_from_x_forwarded_for(self):
        """Test extracting IP from X-Forwarded-For (proxy)"""
        request = self.factory.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.1, 198.51.100.1"
        request.META["REMOTE_ADDR"] = "198.51.100.1"

        ip = get_client_ip(request)
        # Should return first IP (the client), not the proxy
        self.assertEqual(ip, "203.0.113.1")

    def test_get_ip_handles_missing(self):
        """Test handling missing IP headers"""
        request = self.factory.get("/")
        # Clear any IP headers that RequestFactory might set
        if "REMOTE_ADDR" in request.META:
            del request.META["REMOTE_ADDR"]

        ip = get_client_ip(request)
        self.assertIsNone(ip)


class EmailClickTrackingMiddlewareTest(TestCase):
    """Test email click tracking middleware."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

        # Create a test email
        self.queued_email = QueuedEmail.objects.create(
            to_email="test@example.com",
            email_type="password_reset",
            context={"user_name": "Test", "reset_link": "http://example.com", "expires_hours": 24},
        )

        # Create middleware instance
        self.get_response = Mock(return_value=Mock(status_code=200))
        self.middleware = EmailClickTrackingMiddleware(self.get_response)

    def test_should_track_click_with_email_utm(self):
        """Test that email UTM parameters are detected"""
        request = self.factory.get("/?utm_source=email&utm_campaign=123")
        request.user = self.user
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertTrue(should_track)
        self.assertEqual(data["email_id"], 123)
        self.assertEqual(data["user_id"], self.user.id)
        self.assertEqual(data["landing_url"], "/")

    def test_should_not_track_non_email_utm(self):
        """Test that non-email UTM sources are ignored"""
        request = self.factory.get("/?utm_source=google&utm_campaign=123")
        request.user = self.user
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertFalse(should_track)
        self.assertIsNone(data)

    def test_should_not_track_missing_campaign(self):
        """Test that missing utm_campaign is ignored"""
        request = self.factory.get("/?utm_source=email")
        request.user = self.user
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertFalse(should_track)

    def test_should_not_track_post_requests(self):
        """Test that POST requests are not tracked"""
        request = self.factory.post("/?utm_source=email&utm_campaign=123")
        request.user = self.user
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertFalse(should_track)

    def test_should_not_track_invalid_campaign_id(self):
        """Test that non-numeric campaign IDs are rejected"""
        request = self.factory.get("/?utm_source=email&utm_campaign=invalid")
        request.user = self.user
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertFalse(should_track)

    def test_tracks_anonymous_users(self):
        """Test that anonymous users are tracked"""
        request = self.factory.get("/?utm_source=email&utm_campaign=123")
        request.user = Mock(is_authenticated=False)
        request.session = {}

        should_track, data = self.middleware._should_track_click(request)

        self.assertTrue(should_track)
        self.assertIsNone(data["user_id"])

    def test_stores_campaign_in_session(self):
        """Test that campaign ID is stored in session for attribution"""
        request = self.factory.get("/?utm_source=email&utm_campaign=456")
        request.user = self.user
        request.session = {}

        self.middleware._should_track_click(request)

        self.assertEqual(request.session["email_campaign_id"], 456)
        self.assertIn("email_campaign_time", request.session)

    def test_middleware_processes_request(self):
        """Test that middleware processes request and calls get_response"""
        request = self.factory.get("/pricing/")
        request.user = self.user
        request.session = {}
        request.META["REMOTE_ADDR"] = "192.168.1.1"

        response = self.middleware(request)

        self.get_response.assert_called_once_with(request)
        self.assertEqual(response.status_code, 200)

    @patch("email_queue.middleware.threading.Thread")
    def test_click_tracked_asynchronously(self, mock_thread):
        """Test that clicks are tracked in background thread"""
        request = self.factory.get(f"/?utm_source=email&utm_campaign={self.queued_email.id}")
        request.user = self.user
        request.session = {}
        request.META["REMOTE_ADDR"] = "192.168.1.1"
        request.META["HTTP_USER_AGENT"] = "Mozilla/5.0"

        self.middleware(request)

        # Verify thread was started
        mock_thread.assert_called_once()
        thread_instance = mock_thread.return_value
        thread_instance.start.assert_called_once()

    def test_click_recorded_in_database(self):
        """Test that click is actually recorded in database"""
        from email_queue.middleware import _record_email_click_async

        # Call the recording function directly (synchronously) instead of through middleware
        # This avoids threading issues in tests
        _record_email_click_async(
            email_id=self.queued_email.id,
            user_id=self.user.id,
            ip_address="203.0.113.1",
            user_agent="Mozilla/5.0 Test",
            landing_url="/library/",
        )

        # Verify click was recorded
        clicks = EmailClick.objects.filter(queued_email=self.queued_email)
        self.assertEqual(clicks.count(), 1)

        click = clicks.first()
        self.assertEqual(click.user, self.user)
        self.assertEqual(click.ip_address, "203.0.113.1")
        self.assertEqual(click.landing_url, "/library/")
        self.assertIn("Mozilla", click.user_agent)

    def test_click_recorded_for_anonymous_user(self):
        """Test that anonymous clicks are recorded"""
        from email_queue.middleware import _record_email_click_async

        # Call directly without threading
        _record_email_click_async(
            email_id=self.queued_email.id,
            user_id=None,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            landing_url="/",
        )

        clicks = EmailClick.objects.filter(queued_email=self.queued_email)
        self.assertEqual(clicks.count(), 1)
        self.assertIsNone(clicks.first().user)

    def test_handles_nonexistent_email_gracefully(self):
        """Test that tracking handles nonexistent email IDs gracefully"""
        from email_queue.middleware import _record_email_click_async

        # Should not raise exception
        _record_email_click_async(
            email_id=99999,
            user_id=self.user.id,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            landing_url="/",
        )

        # No click should be recorded
        self.assertEqual(EmailClick.objects.count(), 0)

    def test_truncates_long_user_agent(self):
        """Test that long user agents are truncated"""
        from email_queue.middleware import _record_email_click_async

        _record_email_click_async(
            email_id=self.queued_email.id,
            user_id=self.user.id,
            ip_address="192.168.1.1",
            user_agent="A" * 1000,  # Very long user agent
            landing_url="/",
        )

        click = EmailClick.objects.first()
        self.assertLessEqual(len(click.user_agent), 500)

    def test_truncates_long_landing_url(self):
        """Test that long URLs are truncated"""
        from email_queue.middleware import _record_email_click_async

        long_url = "/page/" + "a" * 1000
        _record_email_click_async(
            email_id=self.queued_email.id,
            user_id=self.user.id,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            landing_url=long_url,
        )

        click = EmailClick.objects.first()
        self.assertLessEqual(len(click.landing_url), 500)
