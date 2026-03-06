import logging
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import QueuedEmail
from .schemas import validate_email_context
from .sending import send_queued_email
from .unsubscribe import get_email_category, is_unsubscribed, should_enforce_unsubscribe

logger = logging.getLogger(__name__)


def queue_email(
    *,
    to_email: str | list[str],
    email_type: str,
    context: dict,
    scheduled_for: datetime | None = None,
    send_now: bool = False,
    batch_id: str | None = None,
    expires_at: datetime | None = None,
) -> QueuedEmail | list[QueuedEmail]:
    """
    Queue an email for sending.

    Args:
        to_email: Email address or list of email addresses
        email_type: Email type identifier (must exist in settings.EMAIL_QUEUE_TYPES)
        context: Template context (validated against Pydantic schema)
        scheduled_for: When to send (default: now)
        send_now: If True, send immediately via send_queued_email (default: False)
        batch_id: Optional batch identifier for grouping
        expires_at: Optional expiration time for time-sensitive emails

    Returns:
        QueuedEmail instance (or list of instances if to_email is a list)

    Raises:
        ValueError: If email_type unknown
        pydantic.ValidationError: If context doesn't match schema
    """
    # Handle list of email addresses
    if isinstance(to_email, list):
        queued_emails = []
        for email_addr in to_email:
            queued_email = queue_email(
                to_email=email_addr,
                email_type=email_type,
                context=context,
                scheduled_for=scheduled_for,
                send_now=send_now,
                batch_id=batch_id,
                expires_at=expires_at,
            )
            queued_emails.append(queued_email)
        return queued_emails

    # Validate email type exists
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    if email_type not in email_types:
        raise ValueError(f"Unknown email_type: {email_type}. Must be one of: {list(email_types.keys())}")

    # Validate context against schema
    validated_context = validate_email_context(email_type, context)

    # Set defaults
    if scheduled_for is None:
        scheduled_for = timezone.now()

    category = get_email_category(email_type)
    unsubscribe_enforced = should_enforce_unsubscribe(email_type)
    is_recipient_unsubscribed = unsubscribe_enforced and is_unsubscribed(to_email, category)
    unsubscribe_reason = f"Recipient unsubscribed from {category.replace('_', ' ')} emails"

    # Try to create email (idempotent - returns existing if duplicate)
    try:
        with transaction.atomic():
            queued_email = QueuedEmail.objects.create(
                to_email=to_email,
                email_type=email_type,
                context=validated_context,
                scheduled_for=scheduled_for,
                status="skipped" if is_recipient_unsubscribed else "queued",
                batch_id=batch_id or "",
                expires_at=expires_at,
                failure_reason=unsubscribe_reason if is_recipient_unsubscribed else "",
            )
            if is_recipient_unsubscribed:
                logger.info(
                    f"Skipped queueing email {queued_email.id} ({email_type}) to {to_email}: {unsubscribe_reason}"
                )
            else:
                logger.info(f"Queued email {queued_email.id} ({email_type}) to {to_email}")
    except IntegrityError:
        # Duplicate - return existing
        queued_email = QueuedEmail.objects.get(
            to_email=to_email,
            email_type=email_type,
            scheduled_for=scheduled_for,
            context=validated_context,
        )
        if is_recipient_unsubscribed and queued_email.status in ["queued", "failed"]:
            queued_email.status = "skipped"
            queued_email.failure_reason = unsubscribe_reason
            queued_email.save(update_fields=["status", "failure_reason"])
        logger.info(f"Email already queued: {queued_email.id} ({email_type}) to {to_email}")

    # Send immediately if requested
    # Only immediate-send freshly queued rows. Failed rows are retried
    # by the worker command based on retry_delay/max_attempt rules.
    if send_now and queued_email.status == "queued":
        logger.info(f"Sending email {queued_email.id} immediately (send_now=True)")
        send_queued_email(queued_email)

    return queued_email
