import threading
import time
from unittest.mock import patch

from django.core.management import call_command
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

from email_queue.models import QueuedEmail
from email_queue.sending import send_queued_email as original_send


class SendQueuedEmailsConcurrencyTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        now = timezone.now()
        self.emails = [
            QueuedEmail.objects.create(
                to_email=f"user{i}@example.com",
                email_type="password_reset",
                context={"user_name": "Test", "reset_link": "http://example.com"},
                scheduled_for=now,
            )
            for i in range(3)
        ]

    def _run_command(self, sent_ids, barrier):
        try:

            def slow_send(queued_email):
                time.sleep(3)
                return original_send(queued_email)

            # Patch at the source, not the command's namespace, to handle module caching
            with patch("email_queue.sending.send_queued_email") as mock_send:
                mock_send.side_effect = slow_send

                barrier.wait()
                call_command("send_queued_emails", rate_limit=100, retry_delay=1)

                sent_ids.extend(call.args[0].id for call in mock_send.call_args_list)
        finally:
            connections.close_all()

    def test_no_duplicate_sends_across_workers(self):
        sent_ids_1 = []
        sent_ids_2 = []
        barrier = threading.Barrier(2)

        worker_1 = threading.Thread(target=self._run_command, args=(sent_ids_1, barrier))
        worker_2 = threading.Thread(target=self._run_command, args=(sent_ids_2, barrier))

        worker_1.start()
        worker_2.start()
        worker_1.join()
        worker_2.join()

        all_ids = sent_ids_1 + sent_ids_2

        self.assertEqual(len(all_ids), len(set(all_ids)), "Duplicate send detected!")
        self.assertEqual(set(all_ids), {email.id for email in self.emails}, "Not all emails were sent!")
