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

Create directory: `templates/email_queue/new_email_type/`

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
EMAIL_QUEUE_BASE_URL = "https://your-domain.com"
# or EMAIL_QUEUE_BASED_URL (preferred alias)
# falls back to SITE_URL automatically if neither is set
```

### 6. Category Unsubscribe Behavior

- Set `category` per email type in `EmailTypeConfig`.
- Every send includes an unsubscribe link for that category.
- Clicking the link records preference in `EmailUnsubscribe`.
- Future emails in that category are skipped (`status="skipped"`).
- Other categories continue to send normally.

### 7. Unsubscribe Examples

```python
# settings.py
from email_queue.types import EmailTypeConfig

EMAIL_QUEUE_TYPES = {
    "promo_summer_sale": EmailTypeConfig(
        subject="Summer sale is live",
        category="marketing",
        require_not_unsubscribed=True,
    ),
    "new_case_announcement": EmailTypeConfig(
        subject="New case: {{ case_title }}",
        category="notification",
        require_not_unsubscribed=True,
    ),
    "password_reset": EmailTypeConfig(
        subject="Reset your password",
        category="account",
        require_not_unsubscribed=False,  # always send transactional resets
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

## Troubleshooting

### Email Not Sending

1. Check status in admin: `/admin/email_queue/queuedemail/`
2. Look for `failure_reason` field
3. Check if user meets eligibility (active, verified email)
4. Check if email expired (`expires_at`)

### Duplicate Emails

Unique constraint on `(user, email_type, scheduled_for)` prevents duplicates.
If same email queued twice, returns existing record.

### Rate Limiting Issues

Adjust `--rate-limit` parameter based on your email provider limits.
Most providers allow 10-100 emails/minute.

## Future Enhancements

- Unsubscribe management
- Email open/click tracking
- Per-type rate limits
- Priority queue
- Celery integration
- Bulk operation helpers
