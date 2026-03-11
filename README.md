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

2.1 (Optional, for click tracking) add middleware:
```python
MIDDLEWARE = [
    # ...
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "email_queue.middleware.EmailClickTrackingMiddleware",
]
```

Place it after session/auth middleware so user/session attribution works.

2.2 Ensure template loading can find app templates:
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
- `https://example.com` (recommended)
- `example.com` (not recommended for reply-stop webhook integration)

5. Run migrations:
```bash
python manage.py migrate
```

6. Run the worker on a schedule:
```bash
python manage.py send_queued_emails
```

7. (Optional) Set up reply-stop for auto-cancellation on reply — see [Reply-Stop](#reply-stop-auto-stop-on-reply) section below.

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

### 6.1 Unsubscribe Examples

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

## Reply-Stop (Auto-Stop on Reply)

Reply-stop automatically cancels future queued emails when a recipient replies. This is useful for multi-step reminder campaigns where a reply indicates the user has engaged and further reminders are unnecessary.

### How It Works

1. Outbound emails include a tokenized `Message-ID` header and a `Reply-To` address pointing to a Cloudflare-managed reply subdomain.
2. When the recipient replies, Cloudflare Email Routing delivers the inbound email to a Worker script.
3. The Worker extracts the reply-stop token (from `In-Reply-To`/`References` headers or plus-addressing), then POSTs a webhook to your Django backend.
4. Django decodes the token, creates an `EmailReplyEvent` audit record, and cancels matching queued emails.
5. The reply is also forwarded to your support inbox so normal conversation can continue.

### Step-by-Step Setup Guide

#### 1. Configure Email Types

Enable reply-stop on the email types that should auto-cancel when replied to:

```python
# settings.py
EMAIL_QUEUE_TYPES = {
    "renewal_reminder_7_days": EmailTypeConfig(
        subject="Your subscription expires soon",
        category="renewal",
        auto_stop_on_reply=True,
        auto_stop_scope="category",  # or "email_type"
    ),
    "renewal_reminder_3_days": EmailTypeConfig(
        subject="Subscription expiring in 3 days",
        category="renewal",
        auto_stop_on_reply=True,
        auto_stop_scope="category",
    ),
}
```

#### 2. Configure Django Settings

```python
# settings.py

# Required: absolute URL with scheme (webhook URL is derived from this)
SITE_URL = "https://api.example.com"

# Optional overrides (sensible defaults are used if omitted):
# EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS = "email-reply@replies.example.com"
#   Default: email-reply@replies.<SITE_URL host>
# EMAIL_QUEUE_REPLY_FORWARD_TO = "support@example.com"
#   Default: DEFAULT_FROM_EMAIL
# EMAIL_QUEUE_REPLY_STOP_ALLOWED_SCOPES = ["category", "email_type"]
#   Default: both scopes allowed
```

#### 3. Include URL Routes

The webhook endpoint must be reachable at `/email-queue/webhooks/reply-stop/`:

```python
# urls.py
urlpatterns = [
    path("", include("email_queue.urls")),
]
```

#### 4. Run Migrations

```bash
python manage.py migrate
```

This creates the `EmailReplyEvent` and `EmailUnsubscribe` tables.

#### 5. Create a Cloudflare API Token

Go to Cloudflare Dashboard > My Profile > API Tokens > Create Token.

Required permissions (scoped to your zone/account):

| Scope | Permission | Purpose |
|-------|-----------|---------|
| Zone > Zone | Read | Look up zone by domain name |
| Zone > DNS | Read + Edit | Create MX/TXT records for reply subdomain |
| Zone > Email Routing Rules | Read + Edit | Create/update email routing rule |
| Zone > Zone Settings | Read | Check email routing DNS status (optional) |
| Account > Email Routing Addresses | Read + Write | Register destination forwarding address |
| Account > Workers Scripts | Read + Edit | Deploy worker script and set secrets |
| Account > Account Settings | Read | Auto-discover account ID (optional) |

Set the token as an environment variable:

```bash
export CLOUDFLARE_API_TOKEN="your-token-here"
```

#### 6. Run the Setup Command

```bash
python manage.py email_queue_setup_reply_stop_cloudflare --zone example.com
```

This single command:
- Creates MX and SPF TXT records on `replies.example.com` for Cloudflare Email Routing
- Registers your forwarding destination address in Cloudflare
- Deploys the Worker script (`email-queue-reply-stop`)
- Enables Worker observability (logs persisted at 100% sampling)
- Sets the `WEBHOOK_BEARER_TOKEN` Worker secret (from Django `SECRET_KEY`)
- Creates an email routing rule: `email-reply@replies.example.com` -> Worker

**Manual step**: check your forwarding inbox for a Cloudflare verification email and click the link.

Optional flags:
- `--account-id <id>`: Required if your token can access multiple Cloudflare accounts.
- `--script-name <name>`: Override default worker name (default: `email-queue-reply-stop`).
- `--reply-base-address <addr>`: Override reply address (default: `email-reply@replies.<zone>`).
- `--reply-forward-to <email>`: Override forwarding destination.
- `--dry-run`: Preview what would be created without making changes.

#### 7. Verify the Setup

```bash
python manage.py email_queue_check_reply_stop_cloudflare --zone example.com
```

This checks DNS records, worker script existence, routing rules, and destination addresses.

#### 8. Test End-to-End

1. Queue a test email with `auto_stop_on_reply=True` and send it.
2. Reply to the email from the recipient's inbox.
3. Check Django admin for an `EmailReplyEvent` record at `/admin/email_queue/emailreplyevent/`.
4. Verify that matching queued emails were cancelled.

To debug, check Worker logs in Cloudflare Dashboard > Workers & Pages > `email-queue-reply-stop` > Logs. The worker logs a detailed summary line for every invocation including `webhook_attempted`, `webhook_status`, `webhook_ok`, `webhook_error`, and `webhook_skipped` fields.

```bash
# Print the webhook URL and bearer token the worker should use
python manage.py email_queue_print_reply_stop_worker_config
```

### Auto-Stop Scope Reference

`auto_stop_scope` controls how broadly a reply cancels queued emails:

| Scope | Cancels | Persists unsubscribe | Best for |
|-------|---------|---------------------|----------|
| `"category"` | All queued emails in the same category for that recipient | Yes (blocks future sends in category) | Multi-step campaigns where all related reminders should stop together |
| `"email_type"` | Only queued emails of the exact same email type | No | Narrow cancellation where other types in the same category should continue |

For both scopes, cancellation applies to currently queued/failed rows only. Already-sent emails are unaffected.

### Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS` | `email-reply@replies.<SITE_URL host>` | Reply-To address on outbound emails |
| `EMAIL_QUEUE_REPLY_FORWARD_TO` | `DEFAULT_FROM_EMAIL` | Inbox where replies are forwarded for human follow-up |
| `EMAIL_QUEUE_REPLY_STOP_ALLOWED_SCOPES` | `["category", "email_type"]` | Which scopes the webhook will process (others are ignored) |

### Architecture Notes

- **Token format**: Compact `v1.<base36_id>.<HMAC_signature>` — short enough to avoid address-length bounces.
- **Token recovery**: Worker tries three methods in order: plus-addressing (`email-reply+TOKEN@...`), `In-Reply-To`/`References` Message-ID header, subject tag (`[eqr:TOKEN]`). Message-ID fallback means plus-addressing support is not required.
- **Idempotency**: `EmailReplyEvent.message_id` has a unique constraint. Duplicate deliveries are safely ignored.
- **Resilience**: Worker always forwards replies to the support inbox, even if the webhook fails.
- **Auth**: Webhook is protected by Bearer token (Django `SECRET_KEY`), validated with `hmac.compare_digest` for timing-attack safety.
- **Observability**: Worker logs a structured summary for every invocation. Setup enables Cloudflare Worker observability with 100% log sampling and persistence.

### Troubleshooting

| Symptom | Check | Likely cause |
|---------|-------|-------------|
| No `EmailReplyEvent` rows | CF dashboard > Worker logs > `webhook_skipped` field | `WEBHOOK_BEARER_TOKEN` secret missing or empty |
| `webhook_status=401` | Compare CF secret value with Django `SECRET_KEY` | Bearer token mismatch (re-run setup to re-set) |
| `webhook_error=...` | Worker logs | Network error reaching backend (check `SITE_URL`) |
| `webhook_attempted=false` | Worker logs > `webhook_skipped` | Token not found in reply headers, or bearer secret not bound |
| Worker runs but no webhook, very fast execution (< 5ms) | Check worker uses `event.waitUntil()` | Without `waitUntil`, async operations are terminated before completing |
| Reply not forwarded | Worker logs > `forward_error` | Destination address not verified in Cloudflare |

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
