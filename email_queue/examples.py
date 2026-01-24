"""
Example integrations showing how to use email_queue in various scenarios.

These are not meant to be run directly, but serve as reference for integrating
the email queue into your Django project.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from email_queue.api import queue_email


# Example 1: User Registration
def send_welcome_email_on_registration(user: User):
    """
    Send welcome email when user registers.
    Called from your registration view/signal.
    """
    queue_email(
        to_email=user.email,
        email_type="registration_welcome",
        context={
            "user_name": user.first_name or user.username,
        },
    )


# Example 2: Password Reset (Immediate Send)
def send_password_reset_email(user: User, reset_token: str):
    """
    Send password reset email immediately with expiration.
    Called from password reset view.
    """
    from django.urls import reverse

    reset_link = f"https://casebasedlearning.ai{reverse('password_reset_confirm', args=[reset_token])}"

    queue_email(
        to_email=user.email,
        email_type="password_reset",
        context={
            "user_name": user.first_name or user.username,
            "reset_link": reset_link,
            "expires_hours": 24,
        },
        send_now=True,  # Send immediately, not queued
        expires_at=timezone.now() + timedelta(hours=24),
    )


# Example 3: Subscription Cancellation (Signal Handler)
def send_subscription_canceled_email(sender, instance, **kwargs):
    """
    Signal handler for subscription cancellation.
    Connect with: post_save.connect(send_subscription_canceled_email, sender=Subscription)
    """
    from cbl.models import Subscription

    if instance.status == "canceled":
        user = instance.user
        queue_email(
            to_email=user.email,
            email_type="subscription_canceled",
            context={
                "user_name": user.first_name or user.username,
                "plan_name": instance.get_subscription_type_display(),
                "end_date": instance.current_period_end.strftime("%B %d, %Y"),
            },
        )


# Example 4: Batch Promotional Email (Management Command)
def send_promotional_email_to_inactive_users():
    """
    Management command to send promotional email to inactive users.

    Run with: python manage.py send_promo_to_inactive
    """
    from datetime import timedelta

    from django.utils import timezone

    # Find users inactive for 30+ days
    cutoff = timezone.now() - timedelta(days=30)
    inactive_users = User.objects.filter(
        last_login__lt=cutoff,
        is_active=True,
    )

    batch_id = f"promo_inactive_{timezone.now().strftime('%Y%m%d')}"
    send_time = timezone.now() + timedelta(hours=2)  # Send in 2 hours

    for user in inactive_users:
        try:
            queue_email(
                to_email=user.email,
                email_type="promo_comeback",
                context={
                    "user_name": user.first_name or user.username,
                    "discount_code": "COMEBACK20",
                },
                scheduled_for=send_time,
                batch_id=batch_id,
            )
        except Exception as e:
            print(f"Failed to queue email for {user.email}: {e}")

    print(f"Queued emails for {inactive_users.count()} users in batch {batch_id}")


# Example 5: Scheduled Reminder Email
def schedule_case_completion_reminder(user: User, case_title: str, days_until: int):
    """
    Schedule a reminder email for incomplete case.
    Called when user starts but doesn't finish a case.
    """
    send_time = timezone.now() + timedelta(days=days_until)

    queue_email(
        to_email=user.email,
        email_type="case_reminder",
        context={
            "user_name": user.first_name or user.username,
            "case_title": case_title,
        },
        scheduled_for=send_time,
    )


# Example 6: Canceling Queued Emails
def cancel_pending_emails_for_user(user: User, email_type: str):
    """
    Cancel all pending emails of a specific type for a user.
    Useful when user completes an action before scheduled email.
    """
    from email_queue.models import QueuedEmail

    QueuedEmail.objects.filter(
        user=user,
        email_type=email_type,
        status__in=["queued", "failed"],
    ).update(status="cancelled", failure_reason="Cancelled programmatically")


# Example 7: Checking Email Status
def get_email_status(user: User, email_type: str) -> dict:
    """
    Check status of emails for a user.
    Useful for debugging or showing email history to user.
    """
    from email_queue.models import QueuedEmail

    emails = QueuedEmail.objects.filter(user=user, email_type=email_type).order_by("-created_at")[:10]

    return {
        "total": emails.count(),
        "sent": emails.filter(status="sent").count(),
        "queued": emails.filter(status="queued").count(),
        "failed": emails.filter(status="failed").count(),
        "recent": [
            {
                "status": email.status,
                "scheduled_for": email.scheduled_for,
                "sent_at": email.sent_at,
                "failure_reason": email.failure_reason,
            }
            for email in emails
        ],
    }
