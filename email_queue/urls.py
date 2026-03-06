from django.urls import path

from .views import unsubscribe_view

app_name = "email_queue"

urlpatterns = [
    path("email-queue/unsubscribe/<str:token>/", unsubscribe_view, name="email_queue_unsubscribe"),
]
