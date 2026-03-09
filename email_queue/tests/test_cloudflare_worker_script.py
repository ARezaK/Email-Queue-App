from django.test import SimpleTestCase

from email_queue.cloudflare import build_reply_stop_worker_script


class CloudflareWorkerScriptTest(SimpleTestCase):
    def test_worker_script_includes_message_id_token_fallback(self):
        script = build_reply_stop_worker_script(
            webhook_url_fallback="https://example.com/email-queue/webhooks/reply-stop/",
            reply_forward_to_fallback="support@example.com",
        )

        self.assertIn('const inReplyTo = message.headers.get("in-reply-to") || "";', script)
        self.assertIn('const references = message.headers.get("references") || "";', script)
        self.assertIn("email-queue-reply", script)
        self.assertIn("in_reply_to: inReplyTo", script)
        self.assertIn("references: references", script)
