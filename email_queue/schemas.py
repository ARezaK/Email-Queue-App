from django.conf import settings
from pydantic import BaseModel, Field


class RegistrationWelcomeContext(BaseModel):
    """Context for registration welcome email."""

    user_name: str = Field(..., description="User's first name or display name")
    site_url: str = Field(..., description="Website URL")
    support_email: str = Field(..., description="Support email address")
    tutorial_url: str = Field(..., description="URL to tutorial case")


class PasswordResetContext(BaseModel):
    """Context for password reset email."""

    user_name: str = Field(..., description="User's first name or display name")
    reset_link: str = Field(..., description="Password reset URL")
    expires_hours: int = Field(24, description="Hours until link expires")


class SubscriptionCanceledContext(BaseModel):
    """Context for subscription canceled email."""

    user_name: str = Field(..., description="User's first name or display name")
    plan_name: str = Field(..., description="Name of canceled subscription plan")
    end_date: str = Field(..., description="Subscription end date (formatted string)")


class CaseCompletionContext(BaseModel):
    """Context for first case completion email."""

    user_name: str = Field(..., description="User's name or username")
    case_title: str = Field(..., description="Title of the completed case")
    support_email: str = Field(..., description="Support email address")
    library_url: str = Field(..., description="URL to case library")


class BlackFridayPromoContext(BaseModel):
    """Context for Black Friday promotional email."""

    user_name: str = Field(..., description="User's first name")
    discount_percentage: int = Field(..., description="Discount percentage (e.g., 10)")
    coupon_code: str = Field(..., description="Coupon code to use")
    end_date: str = Field(..., description="Promo end date (formatted string)")
    basic_regular: int = Field(..., description="Basic plan regular price")
    basic_discounted: int = Field(..., description="Basic plan discounted price")
    premium_regular: int = Field(..., description="Premium plan regular price")
    premium_discounted: int = Field(..., description="Premium plan discounted price")
    elite_regular: int = Field(..., description="Elite plan regular price")
    elite_discounted: int = Field(..., description="Elite plan discounted price")
    profile_url: str = Field(..., description="URL to profile/subscription page")
    support_email: str = Field(..., description="Support email address")


class SubscriptionConfirmationContext(BaseModel):
    """Context for subscription confirmation email."""

    user_name: str = Field(..., description="User's username or name")
    start_date: str = Field(..., description="Subscription start date (formatted)")
    expiration_date: str = Field(..., description="Subscription expiration date (formatted)")
    account_url: str = Field(..., description="Full URL to account/profile page")
    cme_code: str = Field(default="CBLFREE", description="CME code for learner.plus")
    show_cme: bool = Field(default=True, description="Whether to show CME content in the email")


class SubscriptionErrorContext(BaseModel):
    """Context for subscription error notification email."""

    customer_email: str = Field(..., description="Customer's email address")
    admin_email: str = Field(..., description="Admin email to contact")


class NewCaseAnnouncementContext(BaseModel):
    """Context for new case announcement email."""

    user_name: str = Field(..., description="User's first name or display name")
    case_id: int = Field(..., description="Case ID")
    case_title: str = Field(..., description="Case title")
    case_description: str = Field(..., description="Case description")
    case_url: str = Field(..., description="URL to case page")
    support_email: str = Field(..., description="Support email address")
    profile_url: str = Field(..., description="URL to profile/subscription page")


class AbandonedCheckoutContext(BaseModel):
    """Context for abandoned checkout reminder email."""

    user_name: str = Field(..., description="User's first name or display name")
    coupon_code: str = Field(..., description="Discount coupon code")
    discount_percentage: int = Field(..., description="Discount percentage (e.g., 15)")
    profile_url: str = Field(..., description="URL to profile/subscription page")
    subject_override: str | None = Field(default=None, description="Optional subject override")


class CMEReceiptContext(BaseModel):
    """Context for CME receipt email."""

    user_name: str = Field(..., description="User's first name or display name")
    receipt_url: str = Field(..., description="Hosted receipt or invoice URL")
    receipt_pdf: str | None = Field(default=None, description="Direct link to receipt PDF")
    support_email: str = Field(..., description="Support email address")


def validate_email_context(email_type: str, context: dict) -> dict:
    """
    Validate context against schema for email type.

    Args:
        email_type: The email type identifier
        context: The context dictionary to validate

    Returns:
        Validated and cleaned context dictionary

    Raises:
        ValueError: If email_type is unknown or no schema defined
        pydantic.ValidationError: If context doesn't match schema
    """
    context_schemas = getattr(settings, "EMAIL_QUEUE_CONTEXT_SCHEMAS", {})

    if email_type not in context_schemas:
        raise ValueError(f"Unknown email_type: {email_type}. " f"Must be one of: {list(context_schemas.keys())}")

    schema = context_schemas[email_type]
    validated = schema.model_validate(context)
    return validated.model_dump()
