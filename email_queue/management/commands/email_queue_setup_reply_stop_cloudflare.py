from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_queue.cloudflare import (
    build_reply_stop_worker_script,
    CloudflareAPIError,
    CloudflareClient,
    DEFAULT_REPLY_STOP_SCRIPT_NAME,
    default_reply_stop_base_address,
    get_webhook_url,
    resolve_account_id,
    resolve_api_token,
    resolve_reply_forward_to,
    resolve_zone,
)


class Command(BaseCommand):
    help = "Set up Cloudflare Email Routing + Worker rule for reply-stop integration"

    def add_arguments(self, parser):
        zone_group = parser.add_mutually_exclusive_group(required=True)
        zone_group.add_argument("--zone", help="Zone apex domain (recommended), e.g. example.com")
        zone_group.add_argument("--zone-id", help="Explicit Cloudflare zone ID")
        parser.add_argument("--account-id", help="Optional Cloudflare account ID")
        parser.add_argument("--script-name", default=DEFAULT_REPLY_STOP_SCRIPT_NAME, help="Worker script name")
        parser.add_argument("--api-token", help="Cloudflare API token (optional if CLOUDFLARE_API_TOKEN is set)")
        parser.add_argument("--reply-base-address", help="Optional override, e.g. email-reply@replies.example.com")
        parser.add_argument("--reply-forward-to", help="Optional override for support forwarding destination")
        parser.add_argument(
            "--worker-script-path",
            help="Optional path to a custom Worker script. If omitted, a default reply-stop worker is generated.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Print plan without mutating Cloudflare resources")

    def handle(self, *args, **options):
        try:
            api_token = resolve_api_token(options.get("api_token"))
            client = CloudflareClient(api_token)
            zone = resolve_zone(client, zone=options.get("zone"), zone_id=options.get("zone_id"))
            account_id = resolve_account_id(client, explicit_account_id=options.get("account_id"))

            script_name = options.get("script_name") or DEFAULT_REPLY_STOP_SCRIPT_NAME
            reply_base_address = (
                (options.get("reply_base_address") or "").strip().lower()
                or default_reply_stop_base_address(zone["name"])
            )
            reply_forward_to = resolve_reply_forward_to(options.get("reply_forward_to"))

            self.stdout.write(self.style.SUCCESS("Cloudflare reply-stop setup plan"))
            self.stdout.write(f"- Zone: {zone['name']} ({zone['id']})")
            self.stdout.write(f"- Account ID: {account_id}")
            self.stdout.write(f"- Worker script: {script_name}")
            self.stdout.write(f"- Reply base address: {reply_base_address}")
            self.stdout.write(f"- Forward destination: {reply_forward_to}")

            try:
                dns_status = client.get_email_routing_dns(zone["id"])
                records = dns_status.get("records", []) if isinstance(dns_status, dict) else []
                non_active = [
                    r for r in records if (r.get("status") or "").strip().lower() not in {"active", "verified", "valid"}
                ]
                if non_active:
                    self.stdout.write(
                        self.style.WARNING(
                            "Email routing DNS has pending/unverified records. Ensure DNS is active before production."
                        )
                    )
            except CloudflareAPIError as exc:
                self.stdout.write(self.style.WARNING(f"Could not inspect email routing DNS status: {exc}"))

            if options.get("dry_run"):
                try:
                    dns_plan = client.ensure_reply_subdomain_dns(
                        zone["id"],
                        zone_apex_domain=zone["name"],
                        reply_base_address=reply_base_address,
                        apply_changes=False,
                    )
                    for warning in dns_plan.get("warnings", []):
                        self.stdout.write(self.style.WARNING(warning))
                    if dns_plan.get("managed"):
                        self.stdout.write(
                            f"DNS dry-run: existing={dns_plan.get('existing', 0)} missing={dns_plan.get('missing', 0)} "
                            f"for {reply_base_address.split('@')[1]}"
                        )
                except CloudflareAPIError as exc:
                    self.stdout.write(self.style.WARNING(f"Could not inspect reply-domain DNS state: {exc}"))

                self.stdout.write(
                    self.style.WARNING(
                        "Dry run enabled: skipping destination address setup, worker deployment, and rule upsert"
                    )
                )
                return

            try:
                dns_result = client.ensure_reply_subdomain_dns(
                    zone["id"],
                    zone_apex_domain=zone["name"],
                    reply_base_address=reply_base_address,
                    apply_changes=True,
                )
            except CloudflareAPIError as exc:
                raise CloudflareAPIError(
                    "Could not manage reply-domain DNS records. "
                    "Token needs Zone -> DNS -> Read/Edit permissions. "
                    f"Original error: {exc}"
                ) from exc

            for warning in dns_result.get("warnings", []):
                self.stdout.write(self.style.WARNING(warning))
            if dns_result.get("managed"):
                self.stdout.write(
                    f"Reply-domain DNS ensured: created={dns_result.get('created', 0)} "
                    f"existing={dns_result.get('existing', 0)}"
                )
            else:
                self.stdout.write(self.style.WARNING("DNS automation skipped for reply domain"))

            try:
                destination, created = client.ensure_destination_address(account_id, reply_forward_to)
            except CloudflareAPIError as exc:
                raise CloudflareAPIError(
                    "Could not manage Cloudflare destination addresses. "
                    "Token needs Account -> Email Routing Addresses -> Read/Write permissions. "
                    f"Original error: {exc}"
                ) from exc
            destination_verified = bool(destination.get("verified"))
            if created:
                self.stdout.write(
                    self.style.WARNING(
                        f"Destination address created: {reply_forward_to}. Check inbox to verify it in Cloudflare."
                    )
                )
            elif destination_verified:
                self.stdout.write(f"Destination address verified: {reply_forward_to}")
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"Destination address exists but is not verified: {reply_forward_to}. "
                        "Check inbox to verify it in Cloudflare."
                    )
                )

            webhook_url = get_webhook_url()
            worker_script_path = options.get("worker_script_path")
            if worker_script_path:
                script_content = Path(worker_script_path).read_text(encoding="utf-8")
            else:
                script_content = build_reply_stop_worker_script(
                    webhook_url_fallback=webhook_url,
                    reply_forward_to_fallback=reply_forward_to,
                )

            client.deploy_worker_script(account_id, script_name, script_content)
            self.stdout.write(f"Worker script deployed: {script_name}")

            client.configure_worker_observability(account_id, script_name)
            self.stdout.write("Worker observability enabled (logs + traces)")

            bearer = (getattr(settings, "SECRET_KEY", "") or "").strip()
            if not bearer:
                raise CloudflareAPIError("SECRET_KEY must be set to configure webhook bearer token")
            client.upsert_worker_secret(account_id, script_name, "WEBHOOK_BEARER_TOKEN", bearer)
            self.stdout.write("Worker secret configured: WEBHOOK_BEARER_TOKEN")

            action, rule_id = client.upsert_worker_rule(zone["id"], reply_base_address, script_name)
            self.stdout.write(self.style.SUCCESS(f"Email routing rule {action}: {rule_id}"))

            self.stdout.write(
                self.style.WARNING(
                    "Manual step required: verify destination inbox address in Cloudflare dashboard if prompted."
                )
            )
        except CloudflareAPIError as exc:
            raise CommandError(str(exc)) from exc
