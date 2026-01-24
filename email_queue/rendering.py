import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django.conf import settings
from django.template import Context, Template, TemplateDoesNotExist
from django.template.loader import get_template

logger = logging.getLogger(__name__)


def add_utm_parameters_to_html(html: str, email_id: int) -> str:
    """
    Add UTM tracking parameters to all links in HTML email.

    Args:
        html: HTML content
        email_id: QueuedEmail ID for tracking

    Returns:
        Modified HTML with UTM parameters added to all href attributes
    """
    if not html:
        return html

    utm_params = {
        "utm_source": "email",
        "utm_medium": "email",
        "utm_campaign": str(email_id),
    }

    def add_params_to_url(match):
        """Add UTM parameters to a matched href attribute."""
        original = match.group(0)
        url = match.group(1)

        # Skip empty hrefs, anchors, and mailto/tel links
        if not url or url.startswith("#") or url.startswith("mailto:") or url.startswith("tel:"):
            return original

        try:
            # Parse URL
            parsed = urlparse(url)

            # Parse existing query parameters
            query_params = parse_qs(parsed.query, keep_blank_values=True)

            # Add UTM parameters (don't overwrite if they exist)
            for key, value in utm_params.items():
                if key not in query_params:
                    query_params[key] = [value]

            # Rebuild query string
            # Flatten single-item lists for cleaner URLs
            flattened = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
            new_query = urlencode(flattened, doseq=True)

            # Rebuild URL
            new_parsed = parsed._replace(query=new_query)
            new_url = urlunparse(new_parsed)

            return f'href="{new_url}"'

        except Exception as e:
            logger.warning(f"Failed to add UTM params to URL {url}: {e}")
            return original

    # Match all href attributes: href="..." or href='...'
    # Use a regex that captures the URL inside quotes
    pattern = r'href=["\']([^"\']*)["\']'
    modified_html = re.sub(pattern, add_params_to_url, html)

    return modified_html


def render_email(email_type: str, context: dict, email_id: int | None = None) -> dict:
    """
    Render email templates for given type and context.

    Args:
        email_type: Email type identifier (must exist in settings.EMAIL_QUEUE_TYPES)
        context: Template context dictionary
        email_id: Optional QueuedEmail ID for UTM tracking (if provided, adds UTM params to HTML links)

    Returns:
        Dictionary with:
            - subject: Rendered subject line (str)
            - text_body: Rendered text body (str)
            - html_body: Rendered HTML body (str | None) with UTM parameters if email_id provided

    Raises:
        ValueError: If email_type is unknown
        TemplateDoesNotExist: If required text template doesn't exist
    """
    email_types = getattr(settings, "EMAIL_QUEUE_TYPES", {})

    if email_type not in email_types:
        raise ValueError(f"Unknown email_type: {email_type}. Must be one of: {list(email_types.keys())}")

    config = email_types[email_type]

    # Render subject (it's a string template, not a file)
    subject_source = context.get("subject_override") or config.subject
    subject_template = Template(subject_source)
    subject = subject_template.render(Context(context)).strip().replace("\n", " ")

    # Render text body (required)
    text_template_path = f"email_queue/{email_type}/body.txt"
    text_template = get_template(text_template_path)
    text_body = text_template.render(context)

    # Render HTML body (optional)
    html_body = None
    html_template_path = f"email_queue/{email_type}/body.html"
    try:
        html_template = get_template(html_template_path)
        html_body = html_template.render(context)

        # Add UTM tracking parameters to HTML links
        if html_body and email_id is not None:
            html_body = add_utm_parameters_to_html(html_body, email_id)

    except TemplateDoesNotExist:
        logger.debug(f"No HTML template found for {email_type}, text-only email")

    return {"subject": subject, "text_body": text_body, "html_body": html_body}
