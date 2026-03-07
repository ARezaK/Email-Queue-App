from dataclasses import dataclass

AUTO_STOP_SCOPE_CATEGORY = "category"
AUTO_STOP_SCOPE_EMAIL_TYPE = "email_type"
AUTO_STOP_ALLOWED_SCOPES = {AUTO_STOP_SCOPE_CATEGORY, AUTO_STOP_SCOPE_EMAIL_TYPE}


@dataclass(frozen=True)
class EmailTypeConfig:
    """
    Configuration for a specific email type.

    Args:
        subject: Email subject line (can use Django template variables like {{ user_name }})
        category: Unsubscribe category for this email type (e.g., "marketing", "notification")
        allow_inactive: Whether to send to inactive users (default: False)
        require_verified_email: Whether to require verified email (default: True)
        require_not_unsubscribed: Whether to check unsubscribe status (default: True)
        auto_stop_on_reply: Whether inbound replies should trigger automatic send suppression (default: False)
        auto_stop_scope: Suppression scope when auto-stop is enabled ("category" or "email_type")
    """

    subject: str
    category: str = "notification"
    allow_inactive: bool = False
    require_verified_email: bool = True
    require_not_unsubscribed: bool = True
    auto_stop_on_reply: bool = False
    auto_stop_scope: str = AUTO_STOP_SCOPE_CATEGORY

    def __post_init__(self):
        scope = (self.auto_stop_scope or AUTO_STOP_SCOPE_CATEGORY).strip().lower().replace(" ", "_")
        if scope not in AUTO_STOP_ALLOWED_SCOPES:
            allowed = ", ".join(sorted(AUTO_STOP_ALLOWED_SCOPES))
            raise ValueError(f"Invalid auto_stop_scope '{self.auto_stop_scope}'. Allowed values: {allowed}")
        object.__setattr__(self, "auto_stop_scope", scope)
