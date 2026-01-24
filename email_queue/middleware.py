"""
Email click tracking middleware.

Tracks clicks from email campaigns using UTM parameters without blocking requests.
Uses background threading for database writes to ensure zero performance impact.
"""

import logging
import threading
from typing import Callable

from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> str | None:
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs, first one is the client
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def _record_email_click_async(
    email_id: int, user_id: int | None, ip_address: str | None, user_agent: str, landing_url: str
):
    """
    Record email click in database (runs in background thread).

    This function is called in a separate thread to avoid blocking the request.
    """
    from .models import EmailClick, QueuedEmail

    try:
        # Verify email exists
        queued_email = QueuedEmail.objects.filter(id=email_id).first()
        if not queued_email:
            logger.warning(f"Email click tracking: QueuedEmail {email_id} not found")
            return

        # Get user if authenticated
        user = None
        if user_id:
            user = User.objects.filter(id=user_id).first()

        # Create click record
        EmailClick.objects.create(
            queued_email=queued_email,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else "",  # Truncate long user agents
            landing_url=landing_url[:500],  # Truncate long URLs
        )
        logger.info(f"Recorded email click: email_id={email_id}, user_id={user_id}, url={landing_url}")

    except Exception as e:
        # Log but don't raise - we never want tracking to break the site
        logger.error(f"Error recording email click: {e}", exc_info=True)


class EmailClickTrackingMiddleware:
    """
    Middleware to track email clicks via UTM parameters.

    Detects when users click links from emails (utm_source=email)
    and records the click asynchronously without blocking the request.

    Works in both sync and async Django contexts.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """
        Process request and track email clicks if present.

        Tracking happens in a background thread so it never blocks the response.
        """
        # Check if this is an email click (before processing response)
        should_track, tracking_data = self._should_track_click(request)

        # Process the request normally
        response = self.get_response(request)

        # If we should track, do it asynchronously AFTER getting the response
        if should_track:
            self._track_click_async(tracking_data)

        return response

    def _should_track_click(self, request: HttpRequest) -> tuple[bool, dict | None]:
        """
        Determine if this request should be tracked as an email click.

        Returns:
            (should_track, tracking_data) tuple
        """
        # Only track GET requests (actual page visits, not AJAX/POST)
        if request.method != "GET":
            return False, None

        # Check for email UTM parameters
        utm_source = request.GET.get("utm_source", "").lower()
        utm_campaign = request.GET.get("utm_campaign", "")

        if utm_source != "email" or not utm_campaign:
            return False, None

        # Validate email_id is numeric
        try:
            email_id = int(utm_campaign)
        except (ValueError, TypeError):
            logger.warning(f"Invalid utm_campaign value: {utm_campaign}")
            return False, None

        # Collect tracking data
        tracking_data = {
            "email_id": email_id,
            "user_id": request.user.id if request.user.is_authenticated else None,
            "ip_address": get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "landing_url": request.path,
        }

        # Optional: Store email_id in session for later attribution
        # (e.g., when user subscribes or completes a case)
        request.session["email_campaign_id"] = email_id
        request.session["email_campaign_time"] = timezone.now().isoformat()

        return True, tracking_data

    def _track_click_async(self, tracking_data: dict):
        """
        Record the click in a background thread.

        This ensures the database write never blocks the HTTP response.
        """
        # Spawn background thread to record click
        thread = threading.Thread(
            target=_record_email_click_async,
            kwargs=tracking_data,
            daemon=True,  # Thread dies when main process exits
        )
        thread.start()

        # Note: We don't join() the thread because we want to return immediately
        # The thread will complete in the background


# Optional: Async version for Django 4.1+ async views
class AsyncEmailClickTrackingMiddleware:
    """
    Async version of EmailClickTrackingMiddleware for Django 4.1+.

    Use this if your project uses async views and you want true async tracking.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self.sync_middleware = EmailClickTrackingMiddleware(get_response)

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        """
        Process request asynchronously.

        For sync views, falls back to sync middleware.
        For async views, tracks click in background task.
        """
        from django.utils.asyncio import sync_to_async

        # Check if should track
        should_track, tracking_data = self.sync_middleware._should_track_click(request)

        # Process request
        response = await self.get_response(request)

        # Track asynchronously if needed
        if should_track:
            # Use sync_to_async to run database operation
            await sync_to_async(_record_email_click_async, thread_sensitive=False)(**tracking_data)

        return response
