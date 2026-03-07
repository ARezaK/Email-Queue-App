import base64
from datetime import datetime, timezone
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing

from .unsubscribe import get_email_category, normalize_email

REPLY_STOP_TOKEN_SALT = "email_queue.reply_stop"
REPLY_STOP_DEFAULT_LOCAL_PART = "email-reply"
REPLY_STOP_DEFAULT_SUBDOMAIN = "replies"
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


def generate_reply_stop_token(*, to_email: str, email_type: str, category: str | None = None) -> str:
    payload = {
        "to_email": normalize_email(to_email),
        "email_type": (email_type or "").strip(),
        "category": (category or get_email_category(email_type)).strip().lower(),
        "issued_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    return _encode_signed_payload(payload)


def decode_reply_stop_token(token: str) -> dict[str, str]:
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
