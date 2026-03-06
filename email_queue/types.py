from dataclasses import dataclass


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
    """

    subject: str
    category: str = "notification"
    allow_inactive: bool = False
    require_verified_email: bool = True
    require_not_unsubscribed: bool = True
