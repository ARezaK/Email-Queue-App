from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class QueuedEmail(models.Model):
    """
    Queue for all outbound emails with scheduling, tracking, and retry support.
    """

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("skipped", "Skipped"),
    ]

    # Recipient information
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="queued_emails")
    to_email = models.CharField(max_length=254)

    # Email details
    email_type = models.CharField(max_length=50, db_index=True)
    context = models.JSONField(default=dict)

    # Scheduling
    scheduled_for = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Retry tracking
    attempt_count = models.IntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    # Batch tracking
    batch_id = models.CharField(max_length=100, blank=True, db_index=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "email_queue_queued_email"
        verbose_name = "Queued Email"
        verbose_name_plural = "Queued Emails"
        ordering = ["scheduled_for"]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="emailq_status_sched_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["to_email", "email_type", "scheduled_for", "context"],
                name="emailq_toemail_type_sched_ctx_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.email_type} to {self.to_email} ({self.status})"


class EmailClick(models.Model):
    """
    Track clicks on email links using UTM parameters.

    Captures when users click through from emails to the site,
    enabling attribution and campaign effectiveness analysis.
    """

    queued_email = models.ForeignKey(QueuedEmail, on_delete=models.CASCADE, related_name="clicks")
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL)
    clicked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    landing_url = models.CharField(max_length=500)

    class Meta:
        db_table = "email_queue_email_click"
        verbose_name = "Email Click"
        verbose_name_plural = "Email Clicks"
        ordering = ["-clicked_at"]
        indexes = [
            models.Index(fields=["queued_email", "-clicked_at"], name="emailclick_qe_time_idx"),
            models.Index(fields=["user", "-clicked_at"], name="emailclick_user_time_idx"),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        return f"{user_str} clicked {self.queued_email.email_type} at {self.clicked_at}"


class EmailUnsubscribe(models.Model):
    """
    Track unsubscribe preferences by email category.
    """

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_unsubscribes")
    email = models.CharField(max_length=254, db_index=True)
    category = models.CharField(max_length=50, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    unsubscribed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "email_queue_email_unsubscribe"
        verbose_name = "Email Unsubscribe"
        verbose_name_plural = "Email Unsubscribes"
        ordering = ["-unsubscribed_at"]
        indexes = [
            models.Index(fields=["category", "-unsubscribed_at"], name="emailunsub_cat_time_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["email", "category"], name="emailunsub_email_category_uniq"),
        ]

    def __str__(self):
        return f"{self.email} unsubscribed from {self.category}"


class EmailReplyEvent(models.Model):
    """
    Audit trail for inbound reply-stop webhook events.

    message_id is unique to make processing idempotent.
    """

    ACTION_IGNORED_AUTO_REPLY = "ignored_auto_reply"
    ACTION_IGNORED_NOT_ENABLED = "ignored_not_enabled"
    ACTION_CATEGORY_STOP = "category_stop"
    ACTION_EMAIL_TYPE_STOP = "email_type_stop"
    ACTION_DUPLICATE = "duplicate"

    ACTION_CHOICES = [
        (ACTION_IGNORED_AUTO_REPLY, "Ignored Auto Reply"),
        (ACTION_IGNORED_NOT_ENABLED, "Ignored Not Enabled"),
        (ACTION_CATEGORY_STOP, "Category Stop"),
        (ACTION_EMAIL_TYPE_STOP, "Email Type Stop"),
        (ACTION_DUPLICATE, "Duplicate"),
    ]

    message_id = models.CharField(max_length=255, unique=True)
    from_email = models.CharField(max_length=254, blank=True)
    to_email = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    token_email = models.CharField(max_length=254, blank=True)
    token_email_type = models.CharField(max_length=50, blank=True, db_index=True)
    token_category = models.CharField(max_length=50, blank=True, db_index=True)
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    cancelled_count = models.IntegerField(default=0)
    processed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    raw_payload = models.JSONField(default=dict)

    class Meta:
        db_table = "email_queue_email_reply_event"
        verbose_name = "Email Reply Event"
        verbose_name_plural = "Email Reply Events"
        ordering = ["-processed_at"]
        indexes = [
            models.Index(fields=["action", "-processed_at"], name="emailreply_action_time_idx"),
        ]

    def __str__(self):
        return f"{self.message_id} ({self.action})"
