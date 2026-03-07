from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_queue.cloudflare import (
    CloudflareAPIError,
    CloudflareClient,
    DEFAULT_REPLY_STOP_SCRIPT_NAME,
    default_reply_stop_base_address,
    resolve_account_id,
    resolve_api_token,
    resolve_reply_forward_to,
    resolve_zone,
)


class Command(BaseCommand):
    help = "Check Cloudflare reply-stop integration state"

    def add_arguments(self, parser):
        zone_group = parser.add_mutually_exclusive_group(required=True)
        zone_group.add_argument("--zone", help="Zone apex domain (recommended), e.g. example.com")
        zone_group.add_argument("--zone-id", help="Explicit Cloudflare zone ID")
        parser.add_argument("--account-id", help="Optional Cloudflare account ID")
        parser.add_argument("--script-name", default=DEFAULT_REPLY_STOP_SCRIPT_NAME, help="Worker script name")
        parser.add_argument("--api-token", help="Cloudflare API token (optional if CLOUDFLARE_API_TOKEN is set)")
        parser.add_argument("--reply-base-address", help="Optional override, e.g. email-reply@replies.example.com")
        parser.add_argument("--reply-forward-to", help="Optional override for support forwarding destination")

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

            failures = []
            warnings = []

            worker_script = client.get_worker_script(account_id, script_name)
            if not worker_script:
                failures.append(f"Worker script not found: {script_name}")

            rule = client.find_worker_rule(zone["id"], reply_base_address, script_name=script_name)
            if not rule:
                failures.append(
                    f"No email routing worker rule found for address {reply_base_address} and script {script_name}"
                )

            try:
                dns_status = client.ensure_reply_subdomain_dns(
                    zone["id"],
                    zone_apex_domain=zone["name"],
                    reply_base_address=reply_base_address,
                    apply_changes=False,
                )
                for warning in dns_status.get("warnings", []):
                    warnings.append(warning)
                if dns_status.get("managed") and dns_status.get("missing", 0) > 0:
                    failures.append(
                        f"Reply-domain DNS is missing {dns_status.get('missing', 0)} required MX/TXT records "
                        f"for {reply_base_address.split('@')[1]}. Run setup command."
                    )
            except CloudflareAPIError as exc:
                warnings.append(
                    "Could not inspect reply-domain DNS state "
                    "(requires Zone -> DNS -> Read). "
                    f"Original error: {exc}"
                )

            try:
                destinations = client.list_destination_addresses(account_id)
                destination = next(
                    (
                        address
                        for address in destinations
                        if (address.get("email") or "").strip().lower() == reply_forward_to.strip().lower()
                    ),
                    None,
                )
                if not destination:
                    failures.append(
                        f"Destination address not found in Cloudflare: {reply_forward_to}. "
                        "Run setup command to create it."
                    )
                elif not destination.get("verified"):
                    warnings.append(
                        f"Destination address exists but is not verified yet: {reply_forward_to}. "
                        "Click the Cloudflare verification email to activate forwarding."
                    )
            except CloudflareAPIError as exc:
                warnings.append(
                    "Could not inspect destination addresses "
                    "(requires Account -> Email Routing Addresses -> Read). "
                    f"Original error: {exc}"
                )

            try:
                dns_status = client.get_email_routing_dns(zone["id"])
                records = dns_status if isinstance(dns_status, list) else (dns_status.get("records", []) if isinstance(dns_status, dict) else [])
                if not records:
                    warnings.append("Cloudflare Email Routing DNS requirements list is empty.")
            except CloudflareAPIError as exc:
                warnings.append(f"Could not inspect email routing DNS status: {exc}")

            default_from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip().lower()
            if default_from_email == "webmaster@localhost":
                warnings.append("DEFAULT_FROM_EMAIL is webmaster@localhost and likely not monitored.")

            self.stdout.write(f"Zone: {zone['name']} ({zone['id']})")
            self.stdout.write(f"Account ID: {account_id}")
            self.stdout.write(f"Worker script: {script_name}")
            self.stdout.write(f"Reply base address: {reply_base_address}")
            self.stdout.write(f"Reply forward destination: {reply_forward_to}")

            for warning in warnings:
                self.stdout.write(self.style.WARNING(f"Warning: {warning}"))

            if failures:
                raise CommandError("Cloudflare reply-stop checks failed:\n- " + "\n- ".join(failures))

            self.stdout.write(self.style.SUCCESS("Cloudflare reply-stop checks passed"))
        except CloudflareAPIError as exc:
            raise CommandError(str(exc)) from exc
