from django.core import signing
from django.http import HttpResponse, HttpResponseBadRequest

from .unsubscribe import decode_unsubscribe_token, record_unsubscribe


def unsubscribe_view(request, token: str):
    try:
        payload = decode_unsubscribe_token(token)
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid unsubscribe link.")

    record_unsubscribe(payload["email"], payload["category"])
    return HttpResponse("You have been unsubscribed")
