import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from .reply_stop import build_reply_to_address, generate_reply_stop_token, is_auto_stop_on_reply
from .rendering import render_email
from .unsubscribe import (
    add_unsubscribe_footer,
    get_email_category,
    is_unsubscribed,
    should_include_unsubscribe_footer,
    should_skip_unsubscribed,
)

logger = logging.getLogger(__name__)


def send_queued_email(queued_email) -> bool:
    """
    Attempt to send a single queued email.

    Checks user eligibility, expiration, renders templates, and sends.
    Updates queued_email status in database.

    Args:
        queued_email: QueuedEmail instance to send

    Returns:
        True if sent successfully, False otherwise
    """
    # Update status to 'sending' to prevent concurrent processing
    queued_email.status = "sending"
    queued_email.attempt_count += 1
    queued_email.last_attempt_at = timezone.now()
    queued_email.save(update_fields=["status", "attempt_count", "last_attempt_at"])

    try:
        email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
        config = email_types.get(queued_email.email_type)

        if not config:
            queued_email.status = "failed"
            queued_email.failure_reason = f"Unknown email_type: {queued_email.email_type}"
            queued_email.save(update_fields=["status", "failure_reason"])
            logger.error(f"Unknown email_type {queued_email.email_type} for email {queued_email.id}")
            return False

        # Check if email has expired
        if queued_email.expires_at and timezone.now() > queued_email.expires_at:
            queued_email.status = "skipped"
            queued_email.failure_reason = "Email expired"
            queued_email.save(update_fields=["status", "failure_reason"])
            logger.info(f"Skipped expired email {queued_email.id} ({queued_email.email_type})")
            return False

        unsubscribe_enforced = should_skip_unsubscribed(queued_email.email_type)
        include_unsubscribe_footer = should_include_unsubscribe_footer(queued_email.email_type)
        unsubscribe_category = get_email_category(queued_email.email_type)

        if unsubscribe_enforced and is_unsubscribed(queued_email.to_email, unsubscribe_category):
            queued_email.status = "skipped"
            queued_email.failure_reason = f"Recipient unsubscribed from {unsubscribe_category.replace('_', ' ')} emails"
            queued_email.save(update_fields=["status", "failure_reason"])
            logger.info(
                f"Skipped email {queued_email.id} - recipient unsubscribed from {unsubscribe_category} emails"
            )
            return False

        # Check user eligibility (if we can find a user with this email)
        try:
            user = User.objects.get(email=queued_email.to_email)

            # Check if user is active (unless allowed)
            if not config.allow_inactive and not user.is_active:
                queued_email.status = "skipped"
                queued_email.failure_reason = "User inactive"
                queued_email.save(update_fields=["status", "failure_reason"])
                logger.info(f"Skipped email {queued_email.id} - user {user.id} inactive")
                return False

            # Check if email is verified (if required)
            if config.require_verified_email:
                # Check django-allauth EmailAddress model
                if hasattr(user, "emailaddress_set"):
                    if not user.emailaddress_set.filter(verified=True).exists():
                        queued_email.status = "skipped"
                        queued_email.failure_reason = "Email not verified"
                        queued_email.save(update_fields=["status", "failure_reason"])
                        logger.info(f"Skipped email {queued_email.id} - email not verified")
                        return False

        except User.DoesNotExist:
            # No user found with this email - that's okay, continue sending
            # (emails can be sent to non-users like admins)
            pass

        # Render email with UTM tracking
        rendered = render_email(queued_email.email_type, queued_email.context, email_id=queued_email.id)
        if include_unsubscribe_footer:
            rendered["text_body"], rendered["html_body"] = add_unsubscribe_footer(
                rendered["text_body"],
                rendered["html_body"],
                queued_email.to_email,
                unsubscribe_category,
            )

        reply_to = None
        if is_auto_stop_on_reply(queued_email.email_type):
            try:
                reply_token = generate_reply_stop_token(
                    to_email=queued_email.to_email,
                    email_type=queued_email.email_type,
                    category=unsubscribe_category,
                    queued_email_id=queued_email.id,
                )
                reply_to = [build_reply_to_address(reply_token)]
            except Exception as exc:
                # Never fail delivery because reply-stop metadata cannot be generated.
                logger.warning(f"Could not build reply-stop Reply-To for email {queued_email.id}: {exc}")

        # Create and send email
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", settings.EMAIL_HOST_USER)
        # older versions of django did not have a default from email value set. newer ones do

        # check to see if the from_email is the default 'webmaster@localhost' and if so, replace it with EMAIL_HOST_USER
        if from_email == 'webmaster@localhost' and hasattr(settings, 'EMAIL_HOST_USER'):
            from_email = settings.EMAIL_HOST_USER
        elif from_email == 'webmaster@localhost':
            raise ValueError(
                "DEFAULT_FROM_EMAIL is set to 'webmaster@localhost' and EMAIL_HOST_USER is not defined in settings. Please set a valid DEFAULT_FROM_EMAIL in your settings."
            )

        email_kwargs = {
            "subject": rendered["subject"],
            "body": rendered["text_body"],
            "from_email": from_email,
            "to": [queued_email.to_email],
        }
        if reply_to:
            email_kwargs["reply_to"] = reply_to

        email = EmailMultiAlternatives(
            **email_kwargs,
        )

        if rendered["html_body"]:
            email.attach_alternative(rendered["html_body"], "text/html")

        email.send(fail_silently=False)

        # Mark as sent
        queued_email.status = "sent"
        queued_email.sent_at = timezone.now()
        queued_email.failure_reason = ""
        queued_email.save(update_fields=["status", "sent_at", "failure_reason"])

        logger.info(f"Successfully sent email {queued_email.id} ({queued_email.email_type}) to {queued_email.to_email}")
        return True

    except Exception as e:
        # Mark as failed
        queued_email.status = "failed"
        queued_email.failure_reason = str(e)[:500]  # Truncate long errors
        queued_email.save(update_fields=["status", "failure_reason"])

        logger.error(f"Failed to send email {queued_email.id} ({queued_email.email_type}): {e}", exc_info=True)
        return False
