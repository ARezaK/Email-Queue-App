import json
import os
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

CLOUDFLARE_API_BASE_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_REPLY_STOP_SCRIPT_NAME = "email-queue-reply-stop"
DEFAULT_REPLY_STOP_LOCAL_PART = "email-reply"
DEFAULT_REPLY_STOP_SUBDOMAIN = "replies"


class CloudflareAPIError(Exception):
    """Raised when Cloudflare API calls fail or return unexpected results."""


class CloudflareClient:
    def __init__(self, api_token: str, timeout_seconds: int = 30):
        if not api_token:
            raise CloudflareAPIError("Cloudflare API token is required")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None):
        url = f"{CLOUDFLARE_API_BASE_URL}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url=url,
            method=method,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise CloudflareAPIError(f"Cloudflare API request failed: {method} {path}: {exc}") from exc

        try:
            decoded = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise CloudflareAPIError(f"Invalid Cloudflare API JSON response for {method} {path}") from exc

        if not decoded.get("success", False):
            errors = decoded.get("errors") or []
            raise CloudflareAPIError(f"Cloudflare API error for {method} {path}: {errors}")

        return decoded.get("result")

    def get_zone_by_name(self, zone_name: str) -> dict:
        result = self._request("GET", "/zones", params={"name": zone_name, "per_page": 1})
        if not result:
            raise CloudflareAPIError(f"No zone found for domain: {zone_name}")
        return result[0]

    def get_zone_by_id(self, zone_id: str) -> dict:
        result = self._request("GET", f"/zones/{zone_id}")
        if not result:
            raise CloudflareAPIError(f"No zone found for id: {zone_id}")
        return result

    def list_accounts(self) -> list[dict]:
        result = self._request("GET", "/accounts", params={"per_page": 50})
        return result or []

    def get_worker_script(self, account_id: str, script_name: str) -> dict | None:
        # Cloudflare returns raw script content for this endpoint, not JSON.
        # Treat HTTP 200 as "exists" and 404 as "missing".
        url = f"{CLOUDFLARE_API_BASE_URL}/accounts/{account_id}/workers/scripts/{script_name}"
        request = Request(
            url=url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.api_token}",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds):
                return {"id": script_name}
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise CloudflareAPIError(
                f"Cloudflare API request failed: GET /accounts/{account_id}/workers/scripts/{script_name}: {exc}"
            ) from exc
        except Exception as exc:
            raise CloudflareAPIError(
                f"Cloudflare API request failed: GET /accounts/{account_id}/workers/scripts/{script_name}: {exc}"
            ) from exc

    def get_email_routing_dns(self, zone_id: str):
        return self._request("GET", f"/zones/{zone_id}/email/routing/dns")

    def list_dns_records(self, zone_id: str, *, name: str, record_type: str) -> list[dict]:
        result = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"name": name, "type": record_type, "per_page": 100},
        )
        return result or []

    def create_dns_record(
        self,
        zone_id: str,
        *,
        name: str,
        record_type: str,
        content: str,
        ttl: int = 1,
        priority: int | None = None,
    ) -> dict:
        payload = {
            "type": record_type,
            "name": name,
            "content": content,
            "ttl": ttl,
        }
        if priority is not None:
            payload["priority"] = priority
        return self._request("POST", f"/zones/{zone_id}/dns_records", payload=payload)

    def list_destination_addresses(self, account_id: str) -> list[dict]:
        result = self._request("GET", f"/accounts/{account_id}/email/routing/addresses")
        return result or []

    def create_destination_address(self, account_id: str, email: str) -> dict:
        return self._request(
            "POST",
            f"/accounts/{account_id}/email/routing/addresses",
            payload={"email": email},
        )

    def ensure_destination_address(self, account_id: str, email: str) -> tuple[dict, bool]:
        normalized = (email or "").strip().lower()
        if not normalized:
            raise CloudflareAPIError("Destination email address is required")

        for address in self.list_destination_addresses(account_id):
            if (address.get("email") or "").strip().lower() == normalized:
                return address, False

        created = self.create_destination_address(account_id, normalized)
        return created, True

    def list_email_routing_rules(self, zone_id: str) -> list[dict]:
        result = self._request("GET", f"/zones/{zone_id}/email/routing/rules")
        return result or []

    def _find_worker_rule(self, rules: list[dict], base_address: str, script_name: str | None = None) -> dict | None:
        expected_address = (base_address or "").strip().lower()
        for rule in rules:
            matchers = rule.get("matchers") or []
            matcher_values = {(matcher.get("value") or "").strip().lower() for matcher in matchers}
            if expected_address not in matcher_values:
                continue

            actions = rule.get("actions") or []
            worker_actions = [a for a in actions if (a.get("type") or "").lower() == "worker"]
            if not worker_actions:
                continue

            if script_name:
                matched = False
                for action in worker_actions:
                    value = action.get("value")
                    if isinstance(value, list):
                        matched = script_name in value
                    elif isinstance(value, str):
                        matched = script_name == value
                    if matched:
                        break
                if not matched:
                    continue

            return rule
        return None

    def find_worker_rule(self, zone_id: str, base_address: str, script_name: str | None = None) -> dict | None:
        rules = self.list_email_routing_rules(zone_id)
        return self._find_worker_rule(rules, base_address=base_address, script_name=script_name)

    def upsert_worker_rule(self, zone_id: str, base_address: str, script_name: str) -> tuple[str, str]:
        rules = self.list_email_routing_rules(zone_id)
        existing = self._find_worker_rule(rules, base_address=base_address)
        payload = {
            "name": f"email-queue-reply-stop-{base_address.split('@')[0]}",
            "enabled": True,
            "matchers": [
                {
                    "type": "literal",
                    "field": "to",
                    "value": base_address,
                }
            ],
            "actions": [
                {
                    "type": "worker",
                    "value": [script_name],
                }
            ],
        }

        if existing:
            result = self._request(
                "PUT",
                f"/zones/{zone_id}/email/routing/rules/{existing['id']}",
                payload=payload,
            )
            return "updated", result.get("id", existing["id"])

        result = self._request(
            "POST",
            f"/zones/{zone_id}/email/routing/rules",
            payload=payload,
        )
        return "created", result.get("id", "")

    def deploy_worker_script(self, account_id: str, script_name: str, script_content: str) -> dict:
        if not script_content.strip():
            raise CloudflareAPIError("Worker script content is empty")

        path = f"/accounts/{account_id}/workers/scripts/{script_name}"
        url = f"{CLOUDFLARE_API_BASE_URL}{path}"
        request = Request(
            url=url,
            method="PUT",
            data=script_content.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/javascript",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise CloudflareAPIError(f"Cloudflare API request failed: PUT {path}: {exc}") from exc

        try:
            decoded = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise CloudflareAPIError(f"Invalid Cloudflare API JSON response for PUT {path}") from exc

        if not decoded.get("success", False):
            errors = decoded.get("errors") or []
            raise CloudflareAPIError(f"Cloudflare API error for PUT {path}: {errors}")

        return decoded.get("result") or {}

    def configure_worker_observability(self, account_id: str, script_name: str) -> dict:
        return self._request(
            "PATCH",
            f"/accounts/{account_id}/workers/scripts/{script_name}/script-settings",
            payload={
                "observability": {
                    "enabled": True,
                    "head_sampling_rate": 1,
                },
            },
        )

    def upsert_worker_secret(self, account_id: str, script_name: str, secret_name: str, secret_value: str) -> dict:
        if not secret_name:
            raise CloudflareAPIError("Worker secret name is required")
        if not secret_value:
            raise CloudflareAPIError(f"Worker secret '{secret_name}' value is required")

        return self._request(
            "PUT",
            f"/accounts/{account_id}/workers/scripts/{script_name}/secrets",
            payload={
                "name": secret_name,
                "text": secret_value,
                "type": "secret_text",
            },
        )

    @staticmethod
    def _normalize_record_content(content: str) -> str:
        value = (content or "").strip().lower()
        if value.endswith("."):
            value = value[:-1]
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value

    def _required_reply_dns_records(
        self, zone_id: str, zone_apex_domain: str, reply_domain: str
    ) -> tuple[list[dict], list[dict]]:
        try:
            routing_dns = self.get_email_routing_dns(zone_id)
        except CloudflareAPIError:
            routing_dns = []

        required_mx = []
        required_txt = []

        if isinstance(routing_dns, list):
            for record in routing_dns:
                record_type = (record.get("type") or "").upper()
                if record_type == "MX":
                    required_mx.append(
                        {
                            "type": "MX",
                            "name": reply_domain,
                            "content": record.get("content") or "",
                            "priority": record.get("priority"),
                        }
                    )
                elif record_type == "TXT":
                    content = record.get("content") or ""
                    if (record.get("name") or "").strip().lower() == zone_apex_domain and "_spf.mx.cloudflare.net" in content:
                        required_txt.append(
                            {
                                "type": "TXT",
                                "name": reply_domain,
                                "content": content,
                                "priority": None,
                            }
                        )

        if not required_mx:
            required_mx = [
                {"type": "MX", "name": reply_domain, "content": "route1.mx.cloudflare.net", "priority": 10},
                {"type": "MX", "name": reply_domain, "content": "route2.mx.cloudflare.net", "priority": 20},
                {"type": "MX", "name": reply_domain, "content": "route3.mx.cloudflare.net", "priority": 30},
            ]

        if not required_txt:
            required_txt = [
                {
                    "type": "TXT",
                    "name": reply_domain,
                    "content": "v=spf1 include:_spf.mx.cloudflare.net ~all",
                    "priority": None,
                }
            ]

        return required_mx, required_txt

    def ensure_reply_subdomain_dns(
        self,
        zone_id: str,
        *,
        zone_apex_domain: str,
        reply_base_address: str,
        apply_changes: bool = True,
    ) -> dict:
        _, sep, reply_domain = (reply_base_address or "").strip().lower().partition("@")
        zone_apex = (zone_apex_domain or "").strip().lower()
        if not sep or not reply_domain:
            raise CloudflareAPIError(f"Invalid reply base address: {reply_base_address}")

        # Never auto-manage apex mail DNS.
        if reply_domain == zone_apex:
            return {
                "managed": False,
                "created": 0,
                "existing": 0,
                "missing": 0,
                "warnings": ["Reply base address uses apex domain; DNS automation skipped."],
            }

        if not reply_domain.endswith(f".{zone_apex}"):
            return {
                "managed": False,
                "created": 0,
                "existing": 0,
                "missing": 0,
                "warnings": [
                    f"Reply domain {reply_domain} is outside zone apex {zone_apex}; DNS automation skipped."
                ],
            }

        required_mx, required_txt = self._required_reply_dns_records(zone_id, zone_apex, reply_domain)
        existing_mx = self.list_dns_records(zone_id, name=reply_domain, record_type="MX")
        existing_txt = self.list_dns_records(zone_id, name=reply_domain, record_type="TXT")
        existing_all = existing_mx + existing_txt

        created = 0
        existing = 0
        missing = 0
        warnings = []

        def _has_match(existing_records: list[dict], required_record: dict) -> bool:
            required_content = self._normalize_record_content(required_record["content"])
            for record in existing_records:
                if (record.get("type") or "").upper() != required_record["type"]:
                    continue
                if (record.get("name") or "").strip().lower() != required_record["name"]:
                    continue
                if self._normalize_record_content(record.get("content") or "") == required_content:
                    return True
            return False

        for record in required_mx + required_txt:
            if _has_match(existing_all, record):
                existing += 1
                continue

            if apply_changes:
                self.create_dns_record(
                    zone_id,
                    name=record["name"],
                    record_type=record["type"],
                    content=record["content"],
                    priority=record.get("priority"),
                )
                created += 1
            else:
                missing += 1

        has_unknown_mx = any(
            self._normalize_record_content(record.get("content") or "")
            not in {self._normalize_record_content(item["content"]) for item in required_mx}
            for record in existing_mx
        )
        has_unknown_txt = any(
            self._normalize_record_content(record.get("content") or "")
            not in {self._normalize_record_content(item["content"]) for item in required_txt}
            for record in existing_txt
        )
        if has_unknown_mx or has_unknown_txt:
            warnings.append(
                f"Found existing custom MX/TXT records on {reply_domain}; setup did not modify or delete them."
            )

        return {
            "managed": True,
            "created": created,
            "existing": existing,
            "missing": missing,
            "warnings": warnings,
        }


def resolve_api_token(explicit_api_token: str | None = None) -> str:
    token = (explicit_api_token or "").strip() or os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        raise CloudflareAPIError("Cloudflare API token is required (set CLOUDFLARE_API_TOKEN or pass --api-token)")
    return token


def resolve_zone(client: CloudflareClient, *, zone: str | None = None, zone_id: str | None = None) -> dict:
    if zone_id:
        return client.get_zone_by_id(zone_id)
    if zone:
        return client.get_zone_by_name(zone)
    raise CloudflareAPIError("Provide either --zone or --zone-id")


def resolve_account_id(client: CloudflareClient, explicit_account_id: str | None = None) -> str:
    if explicit_account_id:
        return explicit_account_id

    accounts = client.list_accounts()
    if not accounts:
        raise CloudflareAPIError("No Cloudflare accounts accessible by token")
    if len(accounts) == 1:
        return accounts[0]["id"]

    raise CloudflareAPIError(
        "Multiple Cloudflare accounts are accessible by token; provide --account-id explicitly"
    )


def default_reply_stop_base_address(zone_apex_domain: str) -> str:
    zone = zone_apex_domain.strip().lower()
    return f"{DEFAULT_REPLY_STOP_LOCAL_PART}@{DEFAULT_REPLY_STOP_SUBDOMAIN}.{zone}"


def resolve_reply_forward_to(explicit_forward_to: str | None = None) -> str:
    override = (explicit_forward_to or "").strip()
    if override:
        return override

    configured = (getattr(settings, "EMAIL_QUEUE_REPLY_FORWARD_TO", "") or "").strip()
    if configured:
        return configured

    default_from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if default_from_email:
        return default_from_email

    raise CloudflareAPIError(
        "No reply forward destination found. Set EMAIL_QUEUE_REPLY_FORWARD_TO or DEFAULT_FROM_EMAIL."
    )


def default_reply_stop_base_address_from_site_url() -> str:
    site_url = (getattr(settings, "SITE_URL", "") or "").strip()
    if not site_url:
        raise CloudflareAPIError(
            "Could not determine default reply-stop base address. Set EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS or SITE_URL."
        )

    parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise CloudflareAPIError(
            "Could not determine host from SITE_URL. Set EMAIL_QUEUE_REPLY_STOP_BASE_ADDRESS explicitly."
        )
    return default_reply_stop_base_address(host)


def get_webhook_url() -> str:
    site_url = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if not site_url:
        raise CloudflareAPIError("SITE_URL is required to build webhook URL")
    return f"{site_url}/email-queue/webhooks/reply-stop/"


def build_reply_stop_worker_script(*, webhook_url_fallback: str, reply_forward_to_fallback: str) -> str:
    safe_webhook = webhook_url_fallback.replace("\\", "\\\\").replace('"', '\\"')
    safe_forward = reply_forward_to_fallback.replace("\\", "\\\\").replace('"', '\\"')

    return f"""
addEventListener("email", (event) => {{
  event.waitUntil(handleEmail(event));
}});

async function handleEmail(event) {{
  const message = event.message;
  const toAddress = (message.to || "").toLowerCase();
  const fromAddress = (message.from || "").toLowerCase();
  const inboundMessageId = message.headers.get("message-id") || "";
  const localPart = toAddress.split("@")[0] || "";
  const plusIndex = localPart.indexOf("+");
  const inReplyTo = message.headers.get("in-reply-to") || "";
  const references = message.headers.get("references") || "";
  const subject = message.headers.get("subject") || "";

  function snippet(value, maxLen = 160) {{
    const text = String(value || "");
    return text.length <= maxLen ? text : `${{text.slice(0, maxLen)}}...`;
  }}
  function compact(value, maxLen = 160) {{
    return snippet(value, maxLen).replace(/\\s+/g, " ").trim();
  }}

  let tokenSource = "none";
  let tokenSourceDetail = "none";
  let token = plusIndex >= 0 ? localPart.slice(plusIndex + 1) : "";
  if (token) {{
    tokenSource = "plus_alias";
    tokenSourceDetail = "to_local_part";
  }}
  if (!token) {{
    for (const [fieldName, value] of [
      ["in_reply_to", inReplyTo],
      ["references", references],
      ["subject", subject],
    ]) {{
      const fromMessageId = String(value).match(/email-queue-reply\\+([A-Za-z0-9._-]+)@/i);
      if (fromMessageId && fromMessageId[1]) {{
        token = fromMessageId[1];
        tokenSource = "message_id";
        tokenSourceDetail = fieldName;
        break;
      }}
      const fromSubjectTag = String(value).match(/\\[eqr:([A-Za-z0-9._-]+)\\]/i);
      if (fromSubjectTag && fromSubjectTag[1]) {{
        token = fromSubjectTag[1];
        tokenSource = "subject_tag";
        tokenSourceDetail = fieldName;
        break;
      }}
    }}
  }}

  const headers = {{
    auto_submitted: message.headers.get("auto-submitted") || "",
    x_autoreply: message.headers.get("x-autoreply") || "",
    precedence: message.headers.get("precedence") || "",
    x_auto_response_suppress: message.headers.get("x-auto-response-suppress") || "",
    in_reply_to: inReplyTo,
    references: references,
  }};

  const webhookUrl = typeof WEBHOOK_URL === "string" && WEBHOOK_URL ? WEBHOOK_URL : "{safe_webhook}";
  const webhookBearer = typeof WEBHOOK_BEARER_TOKEN === "string" ? WEBHOOK_BEARER_TOKEN : "";
  const replyForwardTo = typeof REPLY_FORWARD_TO === "string" && REPLY_FORWARD_TO ? REPLY_FORWARD_TO : "{safe_forward}";
  const summary = {{
    fromAddress,
    toAddress,
    inboundMessageId: compact(inboundMessageId),
    hasInReplyTo: Boolean(inReplyTo),
    hasReferences: Boolean(references),
    subject: compact(subject, 80),
    tokenSource,
    tokenSourceDetail,
    tokenPrefix: token ? token.slice(0, 16) : "",
    tokenLen: token ? token.length : 0,
    webhookAttempted: false,
    webhookStatus: "",
    webhookOk: "",
    webhookBody: "",
    webhookError: "",
    webhookSkipped: "",
    providerMessageId: "",
    forwardTo: replyForwardTo,
    forwardOk: false,
    forwardError: "",
  }};

  if (token && webhookBearer) {{
    const providerMessageId = inboundMessageId || crypto.randomUUID();
    summary.webhookAttempted = true;
    summary.providerMessageId = compact(providerMessageId);
    const payload = {{
      message_id: providerMessageId,
      token,
      from: message.from || "",
      to: message.to || "",
      subject: message.headers.get("subject") || "",
      headers,
      received_at: new Date().toISOString(),
    }};

    try {{
      const response = await fetch(webhookUrl, {{
        method: "POST",
        headers: {{
          "content-type": "application/json",
          authorization: `Bearer ${{webhookBearer}}`,
        }},
        body: JSON.stringify(payload),
      }});
      let responseText = "";
      try {{
        responseText = await response.text();
      }} catch (readErr) {{
        responseText = "<unreadable>";
      }}
      summary.webhookStatus = String(response.status);
      summary.webhookOk = String(response.ok);
      summary.webhookBody = compact(responseText, 300);
    }} catch (err) {{
      summary.webhookError = compact(String(err), 200);
      // Continue so customer replies still reach inbox even if webhook is down.
    }}
  }} else {{
    summary.webhookSkipped =
      `has_token=${{Boolean(token)}} has_bearer=${{Boolean(webhookBearer)}} ` +
      `in_reply_to=${{compact(inReplyTo)}} references=${{compact(references)}}`;
  }}

  try {{
    await message.forward(replyForwardTo);
    summary.forwardOk = true;
  }} catch (err) {{
    summary.forwardError = compact(String(err), 200);
  }}

  console.log(
    `[email-queue-reply-stop] summary from=${{summary.fromAddress}} to=${{summary.toAddress}} ` +
    `msg_id=${{summary.inboundMessageId}} in_reply_to=${{summary.hasInReplyTo}} references=${{summary.hasReferences}} ` +
    `subject=${{summary.subject}} token_source=${{summary.tokenSource}} token_source_detail=${{summary.tokenSourceDetail}} ` +
    `token_prefix=${{summary.tokenPrefix}} token_len=${{summary.tokenLen}} webhook_attempted=${{summary.webhookAttempted}} ` +
    `webhook_status=${{summary.webhookStatus}} webhook_ok=${{summary.webhookOk}} webhook_error=${{summary.webhookError}} ` +
    `webhook_body=${{summary.webhookBody}} webhook_skipped=${{summary.webhookSkipped}} ` +
    `provider_message_id=${{summary.providerMessageId}} forward_to=${{summary.forwardTo}} ` +
    `forward_ok=${{summary.forwardOk}} forward_error=${{summary.forwardError}}`
  );

  if (!summary.forwardOk) {{
    throw new Error(summary.forwardError || "forward failed");
  }}
}}
""".strip() + "\n"
