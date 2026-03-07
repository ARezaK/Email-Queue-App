from django.urls import path

from .views import reply_stop_webhook_view, unsubscribe_view

app_name = "email_queue"

urlpatterns = [
    path("email-queue/unsubscribe/<str:token>/", unsubscribe_view, name="email_queue_unsubscribe"),
    path("email-queue/webhooks/reply-stop/", reply_stop_webhook_view, name="email_queue_reply_stop_webhook"),
]
