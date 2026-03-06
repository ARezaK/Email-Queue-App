from django.conf import settings
from django.contrib import admin
from django.shortcuts import render
from django.urls import path
from django.utils.html import format_html

from .models import EmailClick, EmailUnsubscribe, QueuedEmail
from .rendering import render_email
from .sending import send_queued_email
from .unsubscribe import add_unsubscribe_footer, get_email_category, should_enforce_unsubscribe


@admin.register(QueuedEmail)
class QueuedEmailAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "email_type",
        "to_email",
        "status_badge",
        "scheduled_for",
        "failure_reason",
        "sent_at",
        "attempt_count",
        "batch_id",
    ]
    list_filter = ["status", "email_type", "created_at", "scheduled_for"]
    search_fields = ["to_email", "user__email", "user__username", "batch_id"]
    autocomplete_fields = ["user"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "sent_at",
        "last_attempt_at",
        "attempt_count",
        "preview_email_link",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Email Details",
            {"fields": ("user", "to_email", "email_type", "context")},
        ),
        (
            "Scheduling",
            {"fields": ("scheduled_for", "expires_at", "batch_id")},
        ),
        (
            "Status",
            {"fields": ("status", "sent_at", "attempt_count", "last_attempt_at", "failure_reason")},
        ),
        (
            "Metadata",
            {
                "fields": ("created_at", "updated_at", "preview_email_link"),
                "classes": ("collapse",),
            },
        ),
    )

    actions = ["send_now_action", "cancel_emails", "cancel_batch"]

    def status_badge(self, obj):
        colors = {
            "queued": "#ffc107",
            "sending": "#17a2b8",
            "sent": "#28a745",
            "failed": "#dc3545",
            "cancelled": "#6c757d",
            "skipped": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"

    def preview_email_link(self, obj):
        if obj.pk:
            url = f"/{getattr(settings, 'ADMIN_URL', 'admin')}/email_queue/queuedemail/{obj.pk}/preview/"
            return format_html('<a href="{}" target="_blank">Preview Email</a>', url)
        return "-"

    preview_email_link.short_description = "Preview"

    def send_now_action(self, request, queryset):
        """Force send selected emails immediately"""

        sent_count = 0
        failed_count = 0

        for email in queryset.filter(status__in=["queued", "failed"]):
            if send_queued_email(email):
                sent_count += 1
            else:
                failed_count += 1

        self.message_user(request, f"Sent {sent_count} emails successfully. {failed_count} failed/skipped.")

    send_now_action.short_description = "Send selected emails now"

    def cancel_emails(self, request, queryset):
        """Cancel selected emails"""
        count = queryset.filter(status__in=["queued", "failed"]).update(
            status="cancelled", failure_reason="Cancelled by admin"
        )
        self.message_user(request, f"Cancelled {count} emails.")

    cancel_emails.short_description = "Cancel selected emails"

    def cancel_batch(self, request, queryset):
        """Cancel all emails in the same batch as selected"""
        batch_ids = queryset.values_list("batch_id", flat=True).distinct()
        batch_ids = [bid for bid in batch_ids if bid]

        if not batch_ids:
            self.message_user(request, "No batch IDs found in selection.", level="warning")
            return

        count = QueuedEmail.objects.filter(batch_id__in=batch_ids, status__in=["queued", "failed"]).update(
            status="cancelled", failure_reason="Batch cancelled by admin"
        )

        self.message_user(request, f"Cancelled {count} emails across {len(batch_ids)} batch(es).")

    cancel_batch.short_description = "Cancel all emails in same batch"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:email_id>/preview/",
                self.admin_site.admin_view(self.preview_email_view),
                name="email_queue_queuedemail_preview",
            ),
        ]
        return custom_urls + urls

    def preview_email_view(self, request, email_id):
        """Custom view to preview rendered email"""
        queued_email = QueuedEmail.objects.get(pk=email_id)

        try:
            # Render with UTM parameters to show what will actually be sent
            rendered = render_email(queued_email.email_type, queued_email.context, email_id=queued_email.id)
            if should_enforce_unsubscribe(queued_email.email_type):
                rendered["text_body"], rendered["html_body"] = add_unsubscribe_footer(
                    rendered["text_body"],
                    rendered["html_body"],
                    queued_email.to_email,
                    get_email_category(queued_email.email_type),
                )

            context = {
                "queued_email": queued_email,
                "subject": rendered["subject"],
                "text_body": rendered["text_body"],
                "html_body": rendered["html_body"],
                "site_header": self.admin_site.site_header,
                "site_title": self.admin_site.site_title,
            }

            return render(request, "admin/email_queue/preview_email.html", context)

        except Exception as e:
            context = {
                "queued_email": queued_email,
                "error": str(e),
                "site_header": self.admin_site.site_header,
                "site_title": self.admin_site.site_title,
            }
            return render(request, "admin/email_queue/preview_email.html", context)


@admin.register(EmailClick)
class EmailClickAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "queued_email_link",
        "user_link",
        "landing_url",
        "clicked_at",
        "ip_address",
    ]
    list_filter = ["clicked_at", "queued_email__email_type"]
    search_fields = ["user__email", "user__username", "ip_address", "landing_url"]
    readonly_fields = ["queued_email", "user", "clicked_at", "ip_address", "user_agent", "landing_url"]
    date_hierarchy = "clicked_at"

    fieldsets = (
        (
            "Click Details",
            {"fields": ("queued_email", "user", "clicked_at", "landing_url")},
        ),
        (
            "Technical Details",
            {
                "fields": ("ip_address", "user_agent"),
                "classes": ("collapse",),
            },
        ),
    )

    def queued_email_link(self, obj):
        """Link to the queued email"""
        if obj.queued_email:
            url = f"/{getattr(settings, 'ADMIN_URL', 'admin')}/email_queue/queuedemail/{obj.queued_email.id}/change/"
            return format_html(
                '<a href="{}">{} (ID: {})</a>',
                url,
                obj.queued_email.email_type,
                obj.queued_email.id,
            )
        return "-"

    queued_email_link.short_description = "Email"

    def user_link(self, obj):
        """Link to the user"""
        if obj.user:
            url = f"/{getattr(settings, 'ADMIN_URL', 'admin')}/auth/user/{obj.user.id}/change/"
            return format_html('<a href="{}">{}</a>', url, obj.user.username)
        return "Anonymous"

    user_link.short_description = "User"

    def has_add_permission(self, request):
        """Disable manual creation of click records"""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow deletion for cleanup"""
        return True


@admin.register(EmailUnsubscribe)
class EmailUnsubscribeAdmin(admin.ModelAdmin):
    list_display = ["id", "email", "category", "user", "unsubscribed_at"]
    list_filter = ["category", "unsubscribed_at"]
    search_fields = ["email", "user__email", "user__username", "category"]
    readonly_fields = ["created_at", "unsubscribed_at"]
    date_hierarchy = "unsubscribed_at"
