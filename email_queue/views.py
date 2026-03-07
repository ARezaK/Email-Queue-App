import hmac
import json

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .reply_stop_service import ReplyStopService
from .unsubscribe import decode_unsubscribe_token, record_unsubscribe


def unsubscribe_view(request, token: str):
    try:
        payload = decode_unsubscribe_token(token)
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid unsubscribe link.")

    record_unsubscribe(payload["email"], payload["category"])
    return HttpResponse("You have been unsubscribed")


def _is_authorized_webhook_request(request) -> bool:
    expected_bearer = (getattr(settings, "SECRET_KEY", "") or "").strip()
    if not expected_bearer:
        return False

    auth_header = (request.META.get("HTTP_AUTHORIZATION", "") or "").strip()
    if not auth_header:
        return False

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False

    return hmac.compare_digest(token, expected_bearer)


@csrf_exempt
def reply_stop_webhook_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if not _is_authorized_webhook_request(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Payload must be a JSON object"}, status=400)

    missing_required = [field for field in ("message_id", "token") if not str(payload.get(field, "")).strip()]
    if missing_required:
        return JsonResponse(
            {"detail": f"Missing required fields: {', '.join(missing_required)}"},
            status=400,
        )

    service = ReplyStopService()
    try:
        result = service.process_payload(payload)
    except signing.BadSignature:
        return JsonResponse({"detail": "Invalid reply-stop token"}, status=400)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse(result)
