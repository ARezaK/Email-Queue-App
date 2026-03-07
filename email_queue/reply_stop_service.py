from django.conf import settings
from django.db import IntegrityError, transaction

from .models import EmailReplyEvent, QueuedEmail
from .reply_stop import (
    AUTO_STOP_SCOPE_CATEGORY,
    AUTO_STOP_SCOPE_EMAIL_TYPE,
    decode_reply_stop_token,
    get_auto_stop_scope,
    is_auto_stop_on_reply,
)
from .unsubscribe import get_email_category, normalize_category, record_unsubscribe


class ReplyStopService:
    """
    Process inbound reply-stop events from webhook payloads.

    Idempotency is enforced by unique provider message_id.
    """

    CANCELLABLE_STATUSES = ["queued", "failed"]
    _PRECEDENCE_AUTO_VALUES = {"bulk", "junk", "list", "auto_reply", "auto-reply"}

    def process_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")

        message_id = str(payload.get("message_id", "")).strip()
        token = str(payload.get("token", "")).strip()
        if not message_id:
            raise ValueError("message_id is required")
        if not token:
            raise ValueError("token is required")

        token_payload = decode_reply_stop_token(token)
        token_email = token_payload["to_email"]
        token_email_type = token_payload["email_type"]
        token_category = token_payload["category"]

        event = self._create_event(
            message_id=message_id,
            payload=payload,
            token_email=token_email,
            token_email_type=token_email_type,
            token_category=token_category,
        )
        if event is None:
            return {
                "status": "processed",
                "action": EmailReplyEvent.ACTION_DUPLICATE,
                "cancelled_count": 0,
                "unsubscribed": False,
            }

        if self._is_auto_reply(payload):
            return self._finish(event, action=EmailReplyEvent.ACTION_IGNORED_AUTO_REPLY, cancelled_count=0)

        if not is_auto_stop_on_reply(token_email_type):
            return self._finish(event, action=EmailReplyEvent.ACTION_IGNORED_NOT_ENABLED, cancelled_count=0)

        scope = get_auto_stop_scope(token_email_type)
        allowed_scopes = self._allowed_scopes()
        if scope not in allowed_scopes:
            return self._finish(event, action=EmailReplyEvent.ACTION_IGNORED_NOT_ENABLED, cancelled_count=0)

        if scope == AUTO_STOP_SCOPE_CATEGORY:
            # Use the current category mapping so behavior follows current config.
            category = get_email_category(token_email_type)
            record_unsubscribe(token_email, category)
            cancelled_count = self._cancel_for_category(token_email, category)
            event.token_category = category
            return self._finish(
                event,
                action=EmailReplyEvent.ACTION_CATEGORY_STOP,
                cancelled_count=cancelled_count,
                unsubscribed=True,
            )

        if scope == AUTO_STOP_SCOPE_EMAIL_TYPE:
            cancelled_count = self._cancel_for_email_type(token_email, token_email_type)
            return self._finish(
                event,
                action=EmailReplyEvent.ACTION_EMAIL_TYPE_STOP,
                cancelled_count=cancelled_count,
                unsubscribed=False,
            )

        return self._finish(event, action=EmailReplyEvent.ACTION_IGNORED_NOT_ENABLED, cancelled_count=0)

    def _create_event(self, *, message_id: str, payload: dict, token_email: str, token_email_type: str, token_category: str):
        try:
            with transaction.atomic():
                return EmailReplyEvent.objects.create(
                    message_id=message_id,
                    from_email=str(payload.get("from", "")).strip().lower(),
                    to_email=str(payload.get("to", "")).strip().lower(),
                    subject=str(payload.get("subject", "")).strip(),
                    token_email=token_email,
                    token_email_type=token_email_type,
                    token_category=token_category,
                    action=EmailReplyEvent.ACTION_IGNORED_NOT_ENABLED,
                    cancelled_count=0,
                    raw_payload=payload,
                )
        except IntegrityError:
            return None

    def _finish(self, event: EmailReplyEvent, *, action: str, cancelled_count: int, unsubscribed: bool = False) -> dict:
        event.action = action
        event.cancelled_count = cancelled_count
        event.save(update_fields=["action", "cancelled_count", "token_category"])
        return {
            "status": "processed",
            "action": action,
            "cancelled_count": cancelled_count,
            "unsubscribed": unsubscribed,
        }

    def _allowed_scopes(self) -> set[str]:
        configured = getattr(settings, "EMAIL_QUEUE_REPLY_STOP_ALLOWED_SCOPES", None)
        if configured is None:
            configured = [AUTO_STOP_SCOPE_CATEGORY, AUTO_STOP_SCOPE_EMAIL_TYPE]

        normalized = {
            str(scope).strip().lower().replace(" ", "_")
            for scope in configured
            if str(scope).strip()
        }
        if not normalized:
            return {AUTO_STOP_SCOPE_CATEGORY, AUTO_STOP_SCOPE_EMAIL_TYPE}
        return normalized

    def _cancel_for_category(self, to_email: str, category: str) -> int:
        email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
        category_key = normalize_category(category)
        matching_types = [name for name in email_types if get_email_category(name) == category_key]
        if not matching_types:
            return 0

        return QueuedEmail.objects.filter(
            to_email__iexact=to_email,
            email_type__in=matching_types,
            status__in=self.CANCELLABLE_STATUSES,
        ).update(
            status="cancelled",
            failure_reason=f"Cancelled by reply auto-stop (category: {category_key})",
        )

    def _cancel_for_email_type(self, to_email: str, email_type: str) -> int:
        return QueuedEmail.objects.filter(
            to_email__iexact=to_email,
            email_type=email_type,
            status__in=self.CANCELLABLE_STATUSES,
        ).update(
            status="cancelled",
            failure_reason=f"Cancelled by reply auto-stop (email type: {email_type})",
        )

    def _is_auto_reply(self, payload: dict) -> bool:
        headers = payload.get("headers")
        if not isinstance(headers, dict):
            return False

        normalized_headers = {
            str(key).strip().lower().replace("-", "_"): str(value).strip().lower()
            for key, value in headers.items()
            if str(key).strip()
        }

        auto_submitted = normalized_headers.get("auto_submitted", "")
        if auto_submitted and auto_submitted != "no":
            return True

        x_autoreply = normalized_headers.get("x_autoreply", "")
        if x_autoreply:
            return True

        x_auto_response_suppress = normalized_headers.get("x_auto_response_suppress", "")
        if x_auto_response_suppress:
            return True

        precedence = normalized_headers.get("precedence", "")
        if precedence in self._PRECEDENCE_AUTO_VALUES:
            return True

        return False
