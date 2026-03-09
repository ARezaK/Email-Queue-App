import base64
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing

from .unsubscribe import get_email_category, normalize_email

REPLY_STOP_TOKEN_SALT = "email_queue.reply_stop"
REPLY_STOP_DEFAULT_LOCAL_PART = "email-reply"
REPLY_STOP_DEFAULT_SUBDOMAIN = "replies"
REPLY_STOP_COMPACT_VERSION = "v1"
REPLY_STOP_COMPACT_SIG_BYTES = 10
AUTO_STOP_SCOPE_CATEGORY = "category"
AUTO_STOP_SCOPE_EMAIL_TYPE = "email_type"


def _config_value(config, key: str, default):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def is_auto_stop_on_reply(email_type: str) -> bool:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    return bool(_config_value(config, "auto_stop_on_reply", False))


def get_auto_stop_scope(email_type: str) -> str:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    return str(_config_value(config, "auto_stop_scope", AUTO_STOP_SCOPE_CATEGORY)).strip().lower().replace(" ", "_")


def _host_from_site_url(site_url: str) -> str:
    if not site_url:
        return ""

    parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
    return (parsed.hostname or "").strip().lower()


def get_reply_stop_base_address(zone_apex_domain: str | None = None) -> str:
    configured = (getattr(settings, "EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS", "") or "").strip().lower()
    if configured:
        return configured

    if zone_apex_domain:
        domain = zone_apex_domain.strip().lower()
        return f"{REPLY_STOP_DEFAULT_LOCAL_PART}@{REPLY_STOP_DEFAULT_SUBDOMAIN}.{domain}"

    host = _host_from_site_url(getattr(settings, "SITE_URL", ""))
    if host:
        return f"{REPLY_STOP_DEFAULT_LOCAL_PART}@{REPLY_STOP_DEFAULT_SUBDOMAIN}.{host}"

    raise ValueError("Could not resolve reply-stop base address from settings or SITE_URL")


def _encode_signed_payload(payload: dict) -> str:
    signed = signing.dumps(payload, salt=REPLY_STOP_TOKEN_SALT, compress=True)
    encoded = base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_signed_payload(token: str) -> dict:
    if not token:
        raise signing.BadSignature("Invalid token")

    padded = token + "=" * (-len(token) % 4)
    try:
        signed = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise signing.BadSignature("Invalid token encoding") from exc

    payload = signing.loads(signed, salt=REPLY_STOP_TOKEN_SALT)
    if not isinstance(payload, dict):
        raise signing.BadSignature("Invalid token payload")
    return payload


def _compact_token_key() -> bytes:
    secret = (getattr(settings, "SECRET_KEY", "") or "").strip()
    if not secret:
        raise ValueError("SECRET_KEY is required to build reply-stop tokens")
    return hashlib.sha256(f"{REPLY_STOP_TOKEN_SALT}:{secret}".encode("utf-8")).digest()


def _compact_token_signature(*, queued_email_id: int, to_email: str, email_type: str) -> str:
    message = f"{queued_email_id}:{normalize_email(to_email)}:{(email_type or '').strip()}".encode("utf-8")
    digest = hmac.new(_compact_token_key(), message, hashlib.sha256).digest()[:REPLY_STOP_COMPACT_SIG_BYTES]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _to_base36(value: int) -> str:
    if value <= 0:
        raise ValueError("queued_email_id must be a positive integer")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    remaining = value
    while remaining:
        remaining, index = divmod(remaining, 36)
        result.append(alphabet[index])
    return "".join(reversed(result))


def _from_base36(value: str) -> int:
    try:
        parsed = int(value, 36)
    except ValueError as exc:
        raise signing.BadSignature("Invalid token payload") from exc
    if parsed <= 0:
        raise signing.BadSignature("Invalid token payload")
    return parsed


def _decode_compact_token(token: str) -> dict[str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise signing.BadSignature("Invalid token payload")

    _, queued_email_id_part, provided_signature = parts
    if not queued_email_id_part or not provided_signature:
        raise signing.BadSignature("Invalid token payload")

    queued_email_id = _from_base36(queued_email_id_part)

    from .models import QueuedEmail

    try:
        queued_email = QueuedEmail.objects.only("to_email", "email_type").get(id=queued_email_id)
    except QueuedEmail.DoesNotExist as exc:
        raise signing.BadSignature("Invalid token payload") from exc

    to_email = normalize_email(queued_email.to_email)
    email_type = (queued_email.email_type or "").strip()
    if not to_email or not email_type:
        raise signing.BadSignature("Invalid token payload")

    expected_signature = _compact_token_signature(
        queued_email_id=queued_email_id,
        to_email=to_email,
        email_type=email_type,
    )
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise signing.BadSignature("Invalid token payload")

    return {
        "to_email": to_email,
        "email_type": email_type,
        # Use current config mapping for category to match runtime behavior.
        "category": get_email_category(email_type),
    }


def generate_reply_stop_token(
    *,
    to_email: str,
    email_type: str,
    category: str | None = None,
    queued_email_id: int | None = None,
) -> str:
    normalized_email = normalize_email(to_email)
    normalized_email_type = (email_type or "").strip()
    normalized_category = (category or get_email_category(normalized_email_type)).strip().lower()

    if queued_email_id is not None:
        parsed_id = int(queued_email_id)
        id_part = _to_base36(parsed_id)
        signature = _compact_token_signature(
            queued_email_id=parsed_id,
            to_email=normalized_email,
            email_type=normalized_email_type,
        )
        return f"{REPLY_STOP_COMPACT_VERSION}.{id_part}.{signature}"

    # Backward-compatible fallback for contexts without queued_email_id.
    payload = {
        "to_email": normalized_email,
        "email_type": normalized_email_type,
        "category": normalized_category,
        "issued_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    return _encode_signed_payload(payload)


def decode_reply_stop_token(token: str) -> dict[str, str]:
    if (token or "").startswith(f"{REPLY_STOP_COMPACT_VERSION}."):
        return _decode_compact_token(token)

    payload = _decode_signed_payload(token)

    to_email = normalize_email(payload.get("to_email", ""))
    email_type = (payload.get("email_type", "") or "").strip()
    category = (payload.get("category", "") or "").strip().lower()
    if not to_email or not email_type or not category:
        raise signing.BadSignature("Invalid token payload")

    return {
        "to_email": to_email,
        "email_type": email_type,
        "category": category,
    }


def build_reply_to_address(token: str, zone_apex_domain: str | None = None) -> str:
    base_address = get_reply_stop_base_address(zone_apex_domain=zone_apex_domain)
    local, sep, domain = base_address.partition("@")
    if not sep or not local or not domain:
        raise ValueError(f"Invalid reply-stop base address: {base_address}")
    return f"{local}+{token}@{domain}"


def build_reply_stop_message_id(token: str, zone_apex_domain: str | None = None) -> str:
    """
    Embed the reply-stop token in Message-ID so inbound replies can recover it
    from In-Reply-To/References even when plus-addressing is unavailable.
    """
    if not token:
        raise ValueError("Token is required")

    base_address = get_reply_stop_base_address(zone_apex_domain=zone_apex_domain)
    _, sep, domain = base_address.partition("@")
    if not sep or not domain:
        raise ValueError(f"Invalid reply-stop base address: {base_address}")

    return f"<email-queue-reply+{token}@{domain}>"
