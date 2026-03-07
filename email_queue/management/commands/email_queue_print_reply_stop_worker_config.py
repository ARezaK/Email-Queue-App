import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_queue.cloudflare import (
    CloudflareAPIError,
    DEFAULT_REPLY_STOP_SCRIPT_NAME,
    default_reply_stop_base_address_from_site_url,
    get_webhook_url,
    resolve_reply_forward_to,
)


class Command(BaseCommand):
    help = "Print expected Cloudflare Worker config for reply-stop integration"

    def add_arguments(self, parser):
        parser.add_argument("--script-name", default=DEFAULT_REPLY_STOP_SCRIPT_NAME, help="Worker script name")
        parser.add_argument("--reply-base-address", help="Optional override, e.g. email-reply@replies.example.com")
        parser.add_argument("--reply-forward-to", help="Optional override for support forwarding destination")

    def handle(self, *args, **options):
        try:
            script_name = options.get("script_name") or DEFAULT_REPLY_STOP_SCRIPT_NAME
            reply_base_address = (
                (options.get("reply_base_address") or "").strip().lower()
                or (getattr(settings, "EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS", "") or "").strip().lower()
                or default_reply_stop_base_address_from_site_url()
            )
            reply_forward_to = resolve_reply_forward_to(options.get("reply_forward_to"))
            webhook_url = get_webhook_url()
            bearer = (getattr(settings, "SECRET_KEY", "") or "").strip()
            if not bearer:
                raise CloudflareAPIError("SECRET_KEY must be set to build worker auth token")

            self.stdout.write("Reply-stop worker configuration")
            self.stdout.write(f"SCRIPT_NAME={script_name}")
            self.stdout.write(f"REPLY_BASE_ADDRESS={reply_base_address}")
            self.stdout.write(f"REPLY_FORWARD_TO={reply_forward_to}")
            self.stdout.write(f"WEBHOOK_URL={webhook_url}")
            self.stdout.write(f"WEBHOOK_BEARER_TOKEN={bearer}")

            sample_payload = {
                "message_id": "<provider-message-id>",
                "token": "<signed-token>",
                "from": "user@example.com",
                "to": f"{reply_base_address.split('@')[0]}+<token>@{reply_base_address.split('@')[1]}",
                "subject": "Re: your subscription",
                "headers": {"auto_submitted": "", "x_autoreply": ""},
                "received_at": "2026-03-06T12:34:56Z",
            }
            self.stdout.write("Sample webhook payload:")
            self.stdout.write(json.dumps(sample_payload, indent=2, sort_keys=True))
        except CloudflareAPIError as exc:
            raise CommandError(str(exc)) from exc
