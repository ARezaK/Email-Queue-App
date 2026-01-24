import logging
import time

from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from email_queue.models import QueuedEmail
from email_queue.sending import send_queued_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued emails and send them with rate limiting and retry logic"

    def add_arguments(self, parser):
        parser.add_argument(
            "--rate-limit",
            type=int,
            default=10,
            help="Maximum emails per minute (default: 10)",
        )
        parser.add_argument(
            "--retry-delay",
            type=int,
            default=300,
            help="Seconds to wait before retrying failed email (default: 300 = 5 min)",
        )

    def handle(self, *args, **options):
        rate_limit = options["rate_limit"]
        retry_delay = options["retry_delay"]

        # Hardcoded: Always attempt to send each email up to 3 times
        # (1 initial attempt + 2 retries)
        max_attempts = 3

        # Track rate limiting
        sent_count = 0
        window_start = time.time()

        self.stdout.write("Starting email queue processing...")
        self.stdout.write(f"Rate limit: {rate_limit} emails/minute")
        self.stdout.write(f"Max attempts per email: {max_attempts}")
        self.stdout.write(f"Retry delay: {retry_delay}s")

        total_sent = 0
        total_failed = 0

        while True:
            # Get next batch of emails to send
            now = timezone.now()
            retry_cutoff = now - timezone.timedelta(seconds=retry_delay)

            # Query: emails that are:
            # - status='queued' and scheduled_for <= now
            # - OR status='failed' and attempt_count < max_attempts and last_attempt < retry_cutoff
            # Use select_for_update(skip_locked=True) to prevent duplicate sends when
            # multiple cron jobs run concurrently. Locked rows are skipped, ensuring
            # each email is processed by exactly one job.
            # Must execute within a transaction for row locking to work.
            # Note: Cannot use select_related() with select_for_update() on nullable foreign keys
            # due to PostgreSQL limitation with outer joins.
            with transaction.atomic():
                emails_to_send = list(
                    QueuedEmail.objects.select_for_update(skip_locked=True)
                    .filter(scheduled_for__lte=now)
                    .filter(
                        models.Q(status="queued")
                        | models.Q(
                            status="failed",
                            attempt_count__lt=max_attempts,
                            last_attempt_at__lt=retry_cutoff,
                        )
                    )
                    .order_by("scheduled_for")[:100]
                )

                # Mark emails as "sending" inside the transaction to prevent other workers
                # from picking them up after the lock is released.
                # Note: We only update status here. The send_queued_email() function will
                # handle incrementing attempt_count and updating last_attempt_at.
                if emails_to_send:
                    email_ids = [email.id for email in emails_to_send]
                    QueuedEmail.objects.filter(id__in=email_ids).update(status="sending")
                    # Refresh objects to get updated status
                    for email in emails_to_send:
                        email.refresh_from_db()

            if not emails_to_send:
                self.stdout.write(
                    self.style.SUCCESS(f"\nQueue empty. Processed {total_sent} sent, {total_failed} failed/skipped.")
                )
                break

            for queued_email in emails_to_send:
                # Check rate limit
                elapsed = time.time() - window_start
                if sent_count >= rate_limit:
                    if elapsed < 60:
                        sleep_time = 60 - elapsed
                        self.stdout.write(f"Rate limit reached. Sleeping {sleep_time:.1f}s...")
                        time.sleep(sleep_time)
                    # Reset window
                    sent_count = 0
                    window_start = time.time()

                # Send email
                success = send_queued_email(queued_email)
                sent_count += 1

                if success:
                    total_sent += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"✓ Sent {queued_email.email_type} to {queued_email.to_email}")
                    )
                else:
                    total_failed += 1
                    reason = queued_email.failure_reason[:50] if queued_email.failure_reason else "unknown"
                    self.stdout.write(
                        self.style.WARNING(
                            f"✗ Failed/skipped {queued_email.email_type} (attempt {queued_email.attempt_count}): {reason}"
                        )
                    )

        self.stdout.write(self.style.SUCCESS("Email queue processing complete."))
