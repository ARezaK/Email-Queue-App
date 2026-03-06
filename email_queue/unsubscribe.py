from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from .models import EmailUnsubscribe

# Salt scopes Django's signing to the unsubscribe feature so tokens
# from other signed workflows cannot be reused here.
UNSUBSCRIBE_TOKEN_SALT = "email_queue.unsubscribe"
DEFAULT_UNSUBSCRIBE_CATEGORY = "notification"
DEFAULT_UNSUBSCRIBE_PATH = "/email-queue/unsubscribe/{token}/"


def normalize_email(email: str) -> str:
    # Canonicalize recipient addresses so matching is consistent
    # regardless of caller input casing or surrounding whitespace.
    return (email or "").strip().lower()


def normalize_category(category: str | None) -> str:
    # Store categories in a stable key format used by links, DB rows,
    # and config lookups (e.g., "Marketing Emails" -> "marketing_emails").
    value = (category or DEFAULT_UNSUBSCRIBE_CATEGORY).strip().lower()
    return value.replace(" ", "_")


def _config_value(config, key: str, default):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _get_base_url() -> str:
    # Prefer explicit email-queue settings; fall back to SITE_URL when available.
    return (
        getattr(settings, "EMAIL_QUEUE_BASED_URL", None)
        or getattr(settings, "EMAIL_QUEUE_BASE_URL", None)
        or getattr(settings, "SITE_URL", "")
    ).rstrip("/")


def get_email_category(email_type: str) -> str:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    category = _config_value(config, "category", DEFAULT_UNSUBSCRIBE_CATEGORY)
    return normalize_category(category)


def should_enforce_unsubscribe(email_type: str) -> bool:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    return bool(_config_value(config, "require_not_unsubscribed", True))


def is_unsubscribed(email: str, category: str) -> bool:
    return EmailUnsubscribe.objects.filter(
        email=normalize_email(email),
        category=normalize_category(category),
    ).exists()


def record_unsubscribe(email: str, category: str):
    normalized_email = normalize_email(email)
    normalized_category = normalize_category(category)

    if not normalized_email:
        raise ValueError("Email is required to unsubscribe")

    user = get_user_model().objects.filter(email__iexact=normalized_email).first()
    unsubscribe, _ = EmailUnsubscribe.objects.update_or_create(
        email=normalized_email,
        category=normalized_category,
        defaults={"user": user},
    )
    return unsubscribe


def generate_unsubscribe_token(email: str, category: str) -> str:
    payload = {
        "email": normalize_email(email),
        "category": normalize_category(category),
    }
    # Used when creating outbound unsubscribe links.
    return signing.dumps(payload, salt=UNSUBSCRIBE_TOKEN_SALT, compress=True)


def decode_unsubscribe_token(token: str) -> dict[str, str]:
    # Used when validating incoming unsubscribe requests.
    payload = signing.loads(token, salt=UNSUBSCRIBE_TOKEN_SALT)
    if not isinstance(payload, dict):
        raise signing.BadSignature("Invalid token payload")

    email = normalize_email(payload.get("email", ""))
    category = normalize_category(payload.get("category"))
    if not email:
        raise signing.BadSignature("Invalid token payload")

    return {"email": email, "category": category}


def build_unsubscribe_url(token: str) -> str:
    try:
        path = reverse("email_queue_unsubscribe", kwargs={"token": token})
    except NoReverseMatch:
        try:
            path = reverse("email_queue:email_queue_unsubscribe", kwargs={"token": token})
        except NoReverseMatch:
            path = DEFAULT_UNSUBSCRIBE_PATH.format(token=token)

    base_url = _get_base_url()
    if not base_url:
        return path
    return f"{base_url}{path}"


def add_unsubscribe_footer(text_body: str, html_body: str | None, email: str, category: str) -> tuple[str, str | None]:
    token = generate_unsubscribe_token(email, category)
    unsubscribe_url = build_unsubscribe_url(token)
    category_label = normalize_category(category).replace("_", " ")

    text_footer = (
        "\n\n---\n"
        f"Unsubscribe: {unsubscribe_url}\n"
    )
    text_with_footer = f"{text_body}{text_footer}"

    if not html_body:
        return text_with_footer, html_body

    safe_url = escape(unsubscribe_url)
    safe_category_label = escape(category_label)
    html_footer = (
        '<hr style="margin-top:24px;border:none;border-top:1px solid #d0d0d0;">'
        '<p style="font-size:12px;line-height:1.5;color:#666;margin-top:12px;">'
        f'<a href="{safe_url}">Unsubscribe</a>.'
        "</p>"
    )
    return text_with_footer, f"{html_body}{html_footer}"
