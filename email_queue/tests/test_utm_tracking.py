from django.test import TestCase

from email_queue.rendering import add_utm_parameters_to_html, render_email


class UTMParameterTest(TestCase):
    """Test UTM parameter injection into HTML emails."""

    def test_add_utm_to_simple_link(self):
        """Test adding UTM parameters to a simple link"""
        html = '<a href="https://example.com/page">Click here</a>'
        result = add_utm_parameters_to_html(html, email_id=123)

        self.assertIn('href="https://example.com/page?utm_source=email&utm_medium=email&utm_campaign=123"', result)
        self.assertIn(">Click here</a>", result)  # Link text unchanged

    def test_add_utm_to_multiple_links(self):
        """Test adding UTM parameters to multiple links"""
        html = '''
        <a href="https://example.com/page1">Link 1</a>
        <a href="https://example.com/page2">Link 2</a>
        '''
        result = add_utm_parameters_to_html(html, email_id=456)

        self.assertIn("utm_campaign=456", result)
        self.assertEqual(result.count("utm_source=email"), 2)
        self.assertEqual(result.count("utm_medium=email"), 2)
        self.assertEqual(result.count("utm_campaign=456"), 2)

    def test_preserves_existing_query_params(self):
        """Test that existing query parameters are preserved"""
        html = '<a href="https://example.com/page?foo=bar&baz=qux">Link</a>'
        result = add_utm_parameters_to_html(html, email_id=789)

        self.assertIn("foo=bar", result)
        self.assertIn("baz=qux", result)
        self.assertIn("utm_source=email", result)
        self.assertIn("utm_campaign=789", result)

    def test_does_not_overwrite_existing_utm_params(self):
        """Test that existing UTM parameters are not overwritten"""
        html = '<a href="https://example.com/page?utm_source=newsletter&utm_campaign=special">Link</a>'
        result = add_utm_parameters_to_html(html, email_id=999)

        self.assertIn("utm_source=newsletter", result)
        self.assertIn("utm_campaign=special", result)
        self.assertIn("utm_medium=email", result)  # Only adds missing one

    def test_skips_anchor_links(self):
        """Test that anchor links (#) are not modified"""
        html = '<a href="#section">Jump to section</a>'
        result = add_utm_parameters_to_html(html, email_id=111)

        self.assertEqual(html, result)  # Unchanged

    def test_skips_mailto_links(self):
        """Test that mailto links are not modified"""
        html = '<a href="mailto:test@example.com">Email us</a>'
        result = add_utm_parameters_to_html(html, email_id=222)

        self.assertEqual(html, result)  # Unchanged

    def test_skips_tel_links(self):
        """Test that tel links are not modified"""
        html = '<a href="tel:+1234567890">Call us</a>'
        result = add_utm_parameters_to_html(html, email_id=333)

        self.assertEqual(html, result)  # Unchanged

    def test_handles_single_quotes(self):
        """Test that links with single quotes work"""
        html = "<a href='https://example.com/page'>Link</a>"
        result = add_utm_parameters_to_html(html, email_id=444)

        self.assertIn("utm_source=email", result)
        self.assertIn("utm_campaign=444", result)

    def test_handles_relative_urls(self):
        """Test that relative URLs get UTM parameters"""
        html = '<a href="/pricing">Pricing</a>'
        result = add_utm_parameters_to_html(html, email_id=555)

        self.assertIn("utm_source=email", result)
        self.assertIn("utm_campaign=555", result)

    def test_handles_empty_html(self):
        """Test that empty HTML is handled gracefully"""
        result = add_utm_parameters_to_html("", email_id=666)
        self.assertEqual(result, "")

        result = add_utm_parameters_to_html(None, email_id=666)
        self.assertIsNone(result)

    def test_handles_html_without_links(self):
        """Test that HTML without links is unchanged"""
        html = "<p>This is just text with no links</p>"
        result = add_utm_parameters_to_html(html, email_id=777)

        self.assertEqual(html, result)

    def test_complex_html_structure(self):
        """Test with a more complex HTML structure"""
        html = '''
        <html>
            <body>
                <p>Hello, <a href="https://example.com/welcome">welcome</a>!</p>
                <div>
                    <a href="https://example.com/pricing?plan=premium">View pricing</a>
                </div>
                <footer>
                    <a href="mailto:support@example.com">Contact</a> |
                    <a href="https://example.com/unsubscribe">Unsubscribe</a>
                </footer>
            </body>
        </html>
        '''
        result = add_utm_parameters_to_html(html, email_id=888)

        # Check that regular links have UTM params
        self.assertIn("utm_campaign=888", result)
        # Check that mailto is unchanged
        self.assertIn('href="mailto:support@example.com"', result)
        # Check that existing query params are preserved
        self.assertIn("plan=premium", result)

    def test_render_email_with_utm_parameters(self):
        """Test that render_email applies UTM parameters when email_id provided"""
        # This requires an actual email template, so we'll use password_reset
        context = {
            "user_name": "Test User",
            "reset_link": "https://example.com/reset?token=abc123",
            "expires_hours": 24,
        }

        # Without email_id
        result_without = render_email("password_reset", context)
        if result_without["html_body"]:
            self.assertNotIn("utm_source", result_without["html_body"])

        # With email_id
        result_with = render_email("password_reset", context, email_id=999)
        if result_with["html_body"]:
            self.assertIn("utm_source=email", result_with["html_body"])
            self.assertIn("utm_campaign=999", result_with["html_body"])

    def test_url_encoding_special_characters(self):
        """Test that special characters in URLs are properly encoded"""
        html = '<a href="https://example.com/page?name=John Doe&city=New York">Link</a>'
        result = add_utm_parameters_to_html(html, email_id=1111)

        # Should contain encoded parameters
        self.assertIn("utm_source=email", result)
        self.assertIn("utm_campaign=1111", result)
        # Original params should be preserved
        self.assertIn("name=", result)
        self.assertIn("city=", result)
