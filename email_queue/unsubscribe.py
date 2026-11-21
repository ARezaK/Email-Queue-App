from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from .models import EmailUnsubscribe
from .types import ALL_CATEGORY

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
    category = normalize_category(_config_value(config, "category", DEFAULT_UNSUBSCRIBE_CATEGORY))
    if category == ALL_CATEGORY:
        # Dict configs skip EmailTypeConfig's own check.
        raise ValueError(
            f"Email type '{email_type}' uses the reserved category 'all'. 'all' means unsubscribed from "
            "every email and cannot be used as an email type's category."
        )
    return category


def should_skip_unsubscribed(email_type: str) -> bool:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    return bool(_config_value(config, "skip_sending_if_unsubscribed", True))


def should_include_unsubscribe_footer(email_type: str) -> bool:
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})
    config = email_types.get(email_type)
    include_footer = _config_value(config, "include_unsubscribe_footer", None)
    if include_footer is None:
        # Default footer behavior mirrors unsubscribe skipping unless explicitly overridden.
        return should_skip_unsubscribed(email_type)
    return bool(include_footer)


def unsubscribe_match(email: str, category: str) -> str | None:
    """Which unsubscribe covers this address for this category: "all", the category, or None.

    "all" wins when both rows exist.
    """
    normalized_category = normalize_category(category)
    matched = set(
        EmailUnsubscribe.objects.filter(
            email=normalize_email(email),
            category__in=[normalized_category, ALL_CATEGORY],
        ).values_list("category", flat=True)
    )
    if ALL_CATEGORY in matched:
        return ALL_CATEGORY
    if normalized_category in matched:
        return normalized_category
    return None


def is_unsubscribed(email: str, category: str) -> bool:
    return unsubscribe_match(email, category) is not None


BOUNCED_SKIP_TEXT = "Recipient address bounced"


def has_bounced(email: str) -> bool:
    """The address has an 'all' unsubscribe recorded because mail to it bounced.

    Email types that ignore unsubscribes (skip_sending_if_unsubscribed=False) are still not sent to
    such an address: there is nobody there to receive them.
    """
    return EmailUnsubscribe.objects.filter(
        email=normalize_email(email), category=ALL_CATEGORY, reason=EmailUnsubscribe.REASON_BOUNCE
    ).exists()


def skip_text_for(email: str, email_type: str) -> str:
    """Why an email of this type must not go to this address, or "" when it may be sent."""
    if should_skip_unsubscribed(email_type):
        match = unsubscribe_match(email, get_email_category(email_type))
        return unsubscribe_skip_text(match) if match else ""
    return BOUNCED_SKIP_TEXT if has_bounced(email) else ""


def unsubscribe_skip_text(match: str) -> str:
    """The text stored on a skipped QueuedEmail for an unsubscribe_match() result.

    `match` must be a non-None result of unsubscribe_match().
    """
    if match == ALL_CATEGORY:
        return "Recipient unsubscribed from all emails"
    return f"Recipient unsubscribed from {match.replace('_', ' ')} emails"


def record_unsubscribe(email: str, category: str, reason: str = EmailUnsubscribe.REASON_LINK):
    """Record that `email` is unsubscribed from `category` ("all" stops every email).

    `reason` is stored when the row is created. A repeat call keeps the first reason and only
    refreshes the linked user and the timestamp.
    """
    normalized_email = normalize_email(email)
    normalized_category = normalize_category(category)

    if not normalized_email:
        raise ValueError("Email is required to unsubscribe")
    if reason not in dict(EmailUnsubscribe.REASON_CHOICES):
        allowed = ", ".join(value for value, _label in EmailUnsubscribe.REASON_CHOICES)
        raise ValueError(f"Unknown unsubscribe reason '{reason}'. Allowed values: {allowed}")

    user = get_user_model().objects.filter(email__iexact=normalized_email).first()
    unsubscribe, created = EmailUnsubscribe.objects.get_or_create(
        email=normalized_email,
        category=normalized_category,
        defaults={"user": user, "reason": reason},
    )
    if not created:
        unsubscribe.user = user
        unsubscribe.save(update_fields=["user", "unsubscribed_at"])
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

    text_footer = (
        "\n\n---\n"
        f"Unsubscribe: {unsubscribe_url}\n"
    )
    text_with_footer = f"{text_body}{text_footer}"

    if not html_body:
        return text_with_footer, html_body

    safe_url = escape(unsubscribe_url)
    html_footer = (
        '<hr style="margin-top:24px;border:none;border-top:1px solid #d0d0d0;">'
        '<p style="font-size:12px;line-height:1.5;color:#666;margin-top:12px;">'
        f'<a href="{safe_url}">Unsubscribe</a>.'
        "</p>"
    )
    return text_with_footer, f"{html_body}{html_footer}"
