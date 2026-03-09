# Email Queue System

Centralized email queue management for Django with scheduling, tracking, retry logic, and admin interface.

## Features

- **Unified Queue**: All emails go through single `QueuedEmail` model
- **Scheduling**: Send now, delayed, or at specific time
- **Idempotency**: Prevents duplicate emails with DB constraints
- **Validation**: Pydantic schemas validate context before queuing
- **Retry Logic**: Automatic retries with configurable delays
- **Rate Limiting**: Control send rate to avoid provider limits
- **Batch Management**: Group and cancel emails from scripts
- **Admin Interface**: Preview, send, cancel emails from Django admin
- **Audit Trail**: Track all sends, failures, and attempts
- **Category Unsubscribes**: Per-category unsubscribe links (e.g., marketing vs notification)
- **UTM Tracking**: Automatic UTM parameters on all HTML email links for campaign analytics

## Integration Checklist

When adding this app to a Django project:

1. Add app to `INSTALLED_APPS`:
```python
INSTALLED_APPS = [
    # ...
    "email_queue",
]
```

2. Include URLs (unsubscribe + reply-stop webhook endpoints):
```python
urlpatterns = [
    # ...
    path("", include("email_queue.urls")),
]
```

2.1 Ensure template loading can find app templates:
```python
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,  # required for email_queue/templates/*
        # "DIRS": [...],    # optional project-level overrides
    },
]
```

If your project uses `APP_DIRS=False`, copy these templates into your project template directory:
- `email_queue/templates/email_queue/base.txt` -> `templates/email_queue/base.txt`
- `email_queue/templates/email_queue/base.html` -> `templates/email_queue/base.html`
- `email_queue/templates/admin/email_queue/preview_email.html` -> `templates/admin/email_queue/preview_email.html`

This keeps your current template-loader strategy unchanged while still enabling email rendering and admin preview.

3. Configure email types and context schemas in project settings:
```python
EMAIL_QUEUE_TYPES = {
    "renewal_reminder_7_days": EmailTypeConfig(
        subject="Your subscription expires soon",
        category="renewal",
        # optional override:
        # - if unset: follows skip_sending_if_unsubscribed
        # - True: always include footer
        # - False: never include footer
        include_unsubscribe_footer=True,
        auto_stop_on_reply=True,      # uses reply-stop address + tokenized Message-ID
        auto_stop_scope="category",   # or "email_type"
    ),
}

EMAIL_QUEUE_CONTEXT_SCHEMAS = {
    "renewal_reminder_7_days": RenewalReminderContext,
}
```

4. Set URL settings used for generated links/reply addresses:
```python
SITE_URL = "https://example.com"
# Optional overrides:
# EMAIL_QUEUE_BASED_URL = "https://example.com"
# EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS = "email-reply@replies.example.com"
# Default if unset:
# - Runtime sending uses email-reply@replies.<SITE_URL host>
# - Cloudflare setup/check defaults to email-reply@replies.<zone>
# EMAIL_QUEUE_REPLY_FORWARD_TO = "support@example.com"  # defaults to DEFAULT_FROM_EMAIL
# EMAIL_QUEUE_REPLY_STOP_ALLOWED_SCOPES = ["category", "email_type"]
```

`SITE_URL` should be an absolute URL (include scheme), for example:
- `https://guessthe.game` (recommended)
- `guessthe.game` (not recommended for reply-stop webhook integration)

5. Run migrations:
```bash
python manage.py migrate
```

6. Run the worker on a schedule:
```bash
python manage.py send_queued_emails
```

7. (Optional, for auto-stop-on-reply) configure Cloudflare routing + worker:
```bash
export CLOUDFLARE_API_TOKEN="<token>"
python manage.py email_queue_setup_reply_stop_cloudflare --zone example.com
python manage.py email_queue_check_reply_stop_cloudflare --zone example.com
```

## Quick Start

### Queue an Email

```python
from email_queue.api import queue_email

# Simple queued email
queue_email(
    to_email=user.email,
    email_type="password_reset",
    context={"user_name": user.first_name, "reset_link": reset_url},
)

# Send immediately
queue_email(
    to_email=user.email,
    email_type="password_reset",
    context={"user_name": user.first_name, "reset_link": reset_url},
    send_now=True,
    expires_at=timezone.now() + timedelta(hours=24),
)

# Batch email from management script
queue_email(
    to_email=user.email,
    email_type="promo_summer_sale",
    context={"discount_code": "SUMMER25"},
    scheduled_for=tomorrow_9am,
    batch_id="promo_2025_summer",
)
```

### Process Queue

```bash
# Default settings (10 emails/min, 3 retries, 5min delay)
python manage.py send_queued_emails

# Custom rate limit
python manage.py send_queued_emails --rate-limit=20

# Retry failed emails sooner
python manage.py send_queued_emails --retry-delay=60
```

### Set Up Cron

```cron
# Run every 5 minutes
*/5 * * * * cd /path/to/project && python manage.py send_queued_emails
```

## Adding New Email Types

### 1. Add to Configuration

In `email_queue/types.py`:

```python
EMAIL_TYPES["new_email_type"] = EmailTypeConfig(
    subject="Your subject with {{ variable }}",
    category="marketing",  # or "notification", etc.
    allow_inactive=False,
    require_verified_email=True,
)
```

### 2. Add Pydantic Schema

In `email_queue/schemas.py`:

```python
class NewEmailTypeContext(BaseModel):
    variable: str
    another_field: int = 0

EMAIL_CONTEXT_SCHEMAS["new_email_type"] = NewEmailTypeContext
```

### 3. Create Templates

Create directory:
- In a host project: `templates/email_queue/new_email_type/`
- In this package repo: `email_queue/templates/email_queue/new_email_type/`

Base templates used by email bodies live at:
- `email_queue/templates/email_queue/base.txt`
- `email_queue/templates/email_queue/base.html`

Create `body.txt`:
```django
{% extends "email_queue/base.txt" %}

{% block content %}
Your text content with {{ variable }}
{% endblock %}
```

Create `body.html` (optional):
```django
{% extends "email_queue/base.html" %}

{% block content %}
<p>Your HTML content with {{ variable }}</p>
{% endblock %}
```

Admin preview template path:
- `email_queue/templates/admin/email_queue/preview_email.html`
- You can override it at project level with `templates/admin/email_queue/preview_email.html`.

### 4. Test

```python
from email_queue.api import queue_email

queue_email(
    to_email=user.email,
    email_type="new_email_type",
    context={"variable": "test"},
)
```

### 5. Wire Unsubscribe URLs

Include app URLs in your project:

```python
urlpatterns = [
    path("", include("email_queue.urls")),
]
```

Optional setting for absolute links in email footers:

```python
EMAIL_QUEUE_BASED_URL = "https://your-domain.com"
# or EMAIL_QUEUE_BASE_URL (legacy alias)
# falls back to SITE_URL automatically if neither is set
```

### 6. Category Unsubscribe Behavior

- Set `category` per email type in `EmailTypeConfig`.
- Footer default when `include_unsubscribe_footer` is unset:
  - `skip_sending_if_unsubscribed=True` -> footer included
  - `skip_sending_if_unsubscribed=False` -> footer not included
- `include_unsubscribe_footer=True` forces footer on; `False` forces footer off.
- Clicking the link records preference in `EmailUnsubscribe`.
- Future emails in that category are skipped (`status="skipped"`).
- Other categories continue to send normally.

### 6.1 Auto-Stop Scope Differences and Consequences

`auto_stop_scope` controls how far a reply-based stop action goes when `auto_stop_on_reply=True`.

`auto_stop_scope="category"`:
- A reply to one email type stops all queued emails in that category for that recipient.
- Future emails in that category are also blocked (category unsubscribe record is persisted).
- Best for multi-step campaigns where all related reminders should stop together.

Consequence:
- More aggressive. A single reply can stop unrelated email types if they share the same category.

`auto_stop_scope="email_type"`:
- A reply stops only queued emails for that exact email type.
- Other email types in the same category can continue.
- No category unsubscribe is persisted by default in v1.

Consequence:
- Narrower and safer, but future sends of that email type are not globally blocked unless additional suppression logic is added.
- For both scopes, cancellation applies to currently queued/failed rows only. Already sent rows are unaffected.

### 6.2 Reply-Stop Settings Reference

- `EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS`:
  Optional. Default at runtime: `email-reply@replies.<SITE_URL host>`.
  Setup/check commands default to: `email-reply@replies.<zone>`.
- `EMAIL_QUEUE_REPLY_FORWARD_TO`:
  Optional forwarding inbox for user replies. Defaults to `DEFAULT_FROM_EMAIL`.
- `EMAIL_QUEUE_REPLY_STOP_ALLOWED_SCOPES`:
  Optional allowlist for webhook processing. Default: `["category", "email_type"]`.
  If a configured email type uses a scope outside this allowlist, webhook processing skips stop actions for that event.

### 7. Unsubscribe Examples

```python
# settings.py
from email_queue.types import EmailTypeConfig

EMAIL_QUEUE_TYPES = {
    "promo_summer_sale": EmailTypeConfig(
        subject="Summer sale is live",
        category="marketing",
        skip_sending_if_unsubscribed=True,
    ),
    "new_case_announcement": EmailTypeConfig(
        subject="New case: {{ case_title }}",
        category="notification",
        skip_sending_if_unsubscribed=True,
    ),
    "password_reset": EmailTypeConfig(
        subject="Reset your password",
        category="account",
        skip_sending_if_unsubscribed=False,  # always send transactional resets
    ),
}
```

```python
# If user unsubscribed from marketing, this will be skipped.
queue_email(
    to_email=user.email,
    email_type="promo_summer_sale",
    context={"discount_code": "SUMMER25"},
)

# This still sends because category is different ("notification").
queue_email(
    to_email=user.email,
    email_type="new_case_announcement",
    context={"case_title": "Acute Chest Pain"},
)
```

### 8. Cloudflare Setup Inputs (for reply-stop setup)

Prerequisite:
- You must have at least one domain configured in Cloudflare (a Cloudflare "zone").
  Reason: Email Routing and Worker routing rules are attached to a Cloudflare zone, so reply-stop cannot be provisioned without one.
  If your primary app domain is not on Cloudflare, you can use a separate Cloudflare-managed domain for reply handling and set `EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS` to that domain.

If you use Cloudflare setup/check commands, you will need:

- `--zone <domain>`: Recommended input (example: `example.com`).
  Commands can resolve `zone_id` automatically from this.
- `--zone-id <zone>`: Optional explicit override if you already have it.
  You can copy this from Cloudflare Dashboard -> Domain Overview -> API section.
- `--account-id <acct>`: Optional.
  If omitted, commands auto-discover account when token can access exactly one account; otherwise they fail with an instruction to provide `--account-id`.
  You can copy this from Cloudflare Dashboard -> Account Home -> right sidebar/API section.
- `--script-name <worker>`: Optional. Defaults to `email-queue-reply-stop`.
  This is your Worker script identifier; keep default unless your org needs a naming standard.
- Cloudflare API token: required for API-based setup/check calls.
  Recommended env var: `CLOUDFLARE_API_TOKEN`.
  Even with `--zone-id` and `--account-id`, API token is still required.
  Recommended minimum permissions (scoped to the target zone/account):
  - `Zone -> Zone -> Read`
  - `Zone -> DNS -> Read`
  - `Zone -> DNS -> Edit` (setup creates missing MX/TXT for reply subdomain only)
  - `Zone -> Email Routing Rules -> Read`
  - `Zone -> Email Routing Rules -> Edit`
  - `Zone -> Zone Settings -> Read` (used for optional email routing DNS status check)
  - `Account -> Email Routing Addresses -> Read`
  - `Account -> Email Routing Addresses -> Write` (create destination forwarding address)
  - `Account -> Workers Scripts -> Read`
  - `Account -> Workers Scripts -> Edit` (setup deploys/updates worker script + secret)
  - `Account -> Account Settings -> Read` (for account auto-discovery when `--account-id` is omitted)

Run setup/check/print commands:

```bash
# 1) Setup or update routing rule
python manage.py email_queue_setup_reply_stop_cloudflare --zone example.com

# 2) Verify integration state
python manage.py email_queue_check_reply_stop_cloudflare --zone example.com

# 3) Print worker config contract
python manage.py email_queue_print_reply_stop_worker_config
```

Notes:
- `--script-name` is optional (default: `email-queue-reply-stop`).
- If token can access exactly one account, `--account-id` is optional.
- If token can access multiple accounts, pass `--account-id`.
- Default reply address for setup/check is `email-reply@replies.<zone>` to avoid apex MX changes.
- Setup never modifies/deletes existing DNS records. It only creates missing MX/TXT records for the reply subdomain.
- Setup skips DNS automation when reply base address uses apex domain.
- Setup command automatically:
  - creates missing MX/TXT on `replies.<zone>` when needed
  - creates destination forwarding address if missing
  - deploys/updates worker script
  - sets worker secret `WEBHOOK_BEARER_TOKEN` from Django `SECRET_KEY`
  - upserts the worker routing rule
- Expected manual step: click Cloudflare destination verification email.
- Worker behavior: it always forwards the full inbound reply to your destination inbox, even if webhook delivery fails.
- Worker extracts reply-stop token from `Reply-To` plus alias when available, and falls back to `In-Reply-To`/`References` Message-ID token.
- Because of Message-ID fallback, auto-stop does not require Cloudflare plus-addressing support.
- If `Zone Settings -> Read` is missing, DNS inspection may return `403`; setup/check still continue with a warning.
- `SITE_URL` must include scheme (`https://...`) because webhook URL is derived from it.

## Admin Interface

Access at: `/admin/email_queue/queuedemail/`

### Features

- **Filters**: status, email_type, dates
- **Search**: email address, username, batch_id
- **Preview**: See rendered email before sending
- **Actions**:
  - Send Now: Force send selected emails
  - Cancel: Cancel selected emails
  - Cancel Batch: Cancel all emails in same batch

## Architecture

### Models

- `QueuedEmail`: Main model tracking all queued emails
- `EmailUnsubscribe`: Per-email, per-category unsubscribe preferences
- `EmailReplyEvent`: Inbound reply-stop audit log with idempotency key (`message_id`)

### Configuration

- `types.py`: Email type registry with eligibility rules
- `schemas.py`: Pydantic validation schemas

### Core Functions

- `api.queue_email()`: Queue new email (validates, deduplicates)
- `rendering.render_email()`: Render templates with context
- `sending.send_queued_email()`: Send one email (checks eligibility, updates status)

### Management Command

- `send_queued_emails`: Worker that processes queue with rate limiting

## UTM Tracking

All HTML emails automatically include UTM parameters for campaign tracking and analytics.

### How It Works

When an email is sent, the system automatically appends the following UTM parameters to **all links** in the HTML body:

- `utm_source=email`
- `utm_medium=email`
- `utm_campaign={queued_email.id}` (unique ID for this specific email)

### Example

**Original link in template:**
```html
<a href="https://casebasedlearning.ai/library">View Cases</a>
```

**Link in sent email:**
```html
<a href="https://casebasedlearning.ai/library?utm_source=email&utm_medium=email&utm_campaign=12345">View Cases</a>
```

### Important Notes

- **Text-only emails**: UTM parameters are NOT added to text emails (only HTML)
- **Link text unchanged**: Users see the original link text; UTM params are only in the href
- **Existing params preserved**: If a link already has query parameters, they're preserved
- **Skipped links**: Anchor links (#), mailto:, and tel: links are not modified
- **Existing UTM params**: If a link already has UTM parameters, they won't be overwritten
- **Admin preview**: The preview in Django admin shows links with UTM parameters included

### Analytics Integration

Use the `utm_campaign` parameter (which contains the `QueuedEmail.id`) to:
- Track which specific email drove traffic/conversions
- Correlate email sends with user actions in your analytics
- Measure campaign effectiveness
- Calculate ROI per email type

Example Google Analytics filter:
```
utm_source=email AND utm_medium=email
```

### Click Tracking

When users click links from emails, the middleware automatically records the click in the database **without blocking the request**.

**Features:**
- **Async Recording**: Clicks recorded in background thread (zero performance impact)
- **User Attribution**: Links click to user account (if authenticated)
- **Session Storage**: Campaign ID stored in session for later attribution (e.g., when user subscribes)
- **IP & User Agent**: Captures technical details for analytics
- **Admin Interface**: View click history at `/admin/email_queue/emailclick/`

**How It Works:**
1. User clicks email link with UTM parameters
2. Middleware detects `utm_source=email` and `utm_campaign={email_id}`
3. Click recorded in background thread (request not blocked)
4. Campaign ID stored in session: `request.session['email_campaign_id']`

**Using Session Attribution:**
```python
# In your subscription/conversion view
def create_subscription(request):
    subscription = Subscription.objects.create(user=request.user, ...)

    # Attribute to email campaign if user came from email
    if 'email_campaign_id' in request.session:
        subscription.attributed_email_id = request.session['email_campaign_id']
        subscription.save()

    return redirect('thank_you')
```

**Admin Interface:**
- View all clicks: `/admin/email_queue/emailclick/`
- Filter by email type, date, user
- See which emails drive the most traffic
- Analyze click patterns

**Performance:**
- Click recording happens in background thread
- **Zero impact** on page load time
- Database writes are non-blocking
- Works with both sync and async Django

## Testing

```bash
# Run all tests
python manage.py test email_queue

# Run specific test file
python manage.py test email_queue.tests.test_api

# Run UTM tracking tests
python manage.py test email_queue.tests.test_utm_tracking
```

If testing this package standalone (without a host Django project), create a temporary settings module and run targeted tests:

```bash
DJANGO_SETTINGS_MODULE=email_queue_test_settings python -m django test \
  email_queue.tests.test_reply_autostop_config \
  email_queue.tests.test_reply_stop \
  email_queue.tests.test_sending_reply_stop -v 2
```

## Troubleshooting

### Email Not Sending

1. Check status in admin: `/admin/email_queue/queuedemail/`
2. Look for `failure_reason` field
3. Check if user meets eligibility (active, verified email)
4. Check if email expired (`expires_at`)

### Duplicate Emails

Unique constraint on `(to_email, email_type, scheduled_for, context)` prevents duplicates.
If same email queued twice, returns existing record.

### Rate Limiting Issues

Adjust `--rate-limit` parameter based on your email provider limits.
Most providers allow 10-100 emails/minute.

## Future Enhancements

- Email open/click tracking
- Per-type rate limits
- Priority queue
- Celery integration
- Bulk operation helpers
