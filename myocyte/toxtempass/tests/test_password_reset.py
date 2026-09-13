"""Tests for the password reset feature: rate limiting, views, templates."""
import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toxtempass import Config, utilities
from toxtempass.tests.fixtures.factories import PersonFactory

# ---------------------------------------------------------------------------
# Unit tests for rate-limiting utility functions
# ---------------------------------------------------------------------------


class PasswordResetRateLimitTests(TestCase):
    """Tests for get_password_reset_wait_seconds and record_password_reset_attempt."""

    def test_no_attempts_allowed_immediately(self):
        """A user with no prior reset attempts should have zero wait."""
        person = PersonFactory.create()
        wait = utilities.get_password_reset_wait_seconds(person)
        self.assertEqual(wait, 0.0)

    def test_first_attempt_recorded_creates_wait(self):
        """After the first attempt is recorded a second request requires a wait."""
        person = PersonFactory.create()
        utilities.record_password_reset_attempt(person)
        person.refresh_from_db()
        wait = utilities.get_password_reset_wait_seconds(person)
        # First wait period is 60 seconds; should be close to 60 seconds remaining.
        self.assertGreater(wait, 50)
        self.assertLessEqual(wait, 60)

    def test_second_attempt_requires_longer_wait(self):
        """After two attempts the second wait period (300 s) applies."""
        person = PersonFactory.create()
        # Simulate two old attempts: first 70 seconds ago, second 10 seconds ago.
        now = timezone.now()
        ts1 = (now - datetime.timedelta(seconds=70)).isoformat()
        ts2 = (now - datetime.timedelta(seconds=10)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [ts1, ts2]
            return True

        utilities.update_prefs_atomic(person, mutate)
        person.refresh_from_db()

        wait = utilities.get_password_reset_wait_seconds(person)
        # Second wait period is 300 s; 10 s have elapsed, so ~290 s remaining.
        self.assertGreater(wait, 280)
        self.assertLessEqual(wait, 300)

    def test_third_attempt_requires_one_hour_wait(self):
        """After three attempts the third wait period (3600 s) applies."""
        person = PersonFactory.create()
        now = timezone.now()
        ts1 = (now - datetime.timedelta(seconds=400)).isoformat()
        ts2 = (now - datetime.timedelta(seconds=310)).isoformat()
        ts3 = (now - datetime.timedelta(seconds=30)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [ts1, ts2, ts3]
            return True

        utilities.update_prefs_atomic(person, mutate)
        person.refresh_from_db()

        wait = utilities.get_password_reset_wait_seconds(person)
        self.assertGreater(wait, 3560)
        self.assertLessEqual(wait, 3600)

    def test_fourth_plus_attempt_requires_one_day_wait(self):
        """After four or more attempts the maximum wait period (86400 s) applies."""
        person = PersonFactory.create()
        now = timezone.now()
        ts1 = (now - datetime.timedelta(hours=2)).isoformat()
        ts2 = (now - datetime.timedelta(hours=1, minutes=55)).isoformat()
        ts3 = (now - datetime.timedelta(hours=1, minutes=30)).isoformat()
        ts4 = (now - datetime.timedelta(seconds=60)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [ts1, ts2, ts3, ts4]
            return True

        utilities.update_prefs_atomic(person, mutate)
        person.refresh_from_db()

        wait = utilities.get_password_reset_wait_seconds(person)
        self.assertGreater(wait, 86000)
        self.assertLessEqual(wait, 86400)

    def test_wait_expires_after_period(self):
        """Once the required wait period has elapsed the user is allowed again."""
        person = PersonFactory.create()
        now = timezone.now()
        # First attempt 61 seconds ago — more than the 60 s wait period.
        ts = (now - datetime.timedelta(seconds=61)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [ts]
            return True

        utilities.update_prefs_atomic(person, mutate)
        person.refresh_from_db()

        wait = utilities.get_password_reset_wait_seconds(person)
        self.assertEqual(wait, 0.0)

    def test_record_attempt_appends_timestamp(self):
        """record_password_reset_attempt stores a new ISO timestamp in prefs."""
        person = PersonFactory.create()
        before = timezone.now()
        utilities.record_password_reset_attempt(person)
        after = timezone.now()

        person.refresh_from_db()
        attempts = person.preferences.get("pw_reset_attempts", [])
        self.assertEqual(len(attempts), 1)

        recorded = datetime.datetime.fromisoformat(attempts[0])
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=datetime.timezone.utc)

        self.assertGreaterEqual(recorded, before)
        self.assertLessEqual(recorded, after)

    def test_record_attempt_prunes_old_entries(self):
        """record_password_reset_attempt prunes the list to _PW_RESET_MAX_STORED."""
        person = PersonFactory.create()
        max_stored = Config._pw_reset_max_stored

        # Pre-populate with more than max_stored entries.
        old_ts = (timezone.now() - datetime.timedelta(days=365)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [old_ts] * (max_stored + 5)
            return True

        utilities.update_prefs_atomic(person, mutate)
        utilities.record_password_reset_attempt(person)
        person.refresh_from_db()

        attempts = person.preferences.get("pw_reset_attempts", [])
        self.assertLessEqual(len(attempts), max_stored)


# ---------------------------------------------------------------------------
# View / integration tests
# ---------------------------------------------------------------------------


@override_settings(PASSWORD_RESET_TIMEOUT=3600)
class PasswordResetViewTests(TestCase):
    """Integration tests for the password reset request page."""

    def setUp(self):
        self.person = PersonFactory.create()
        self.person.set_password("old-password-123")
        self.person.save()
        self.url = reverse("password_reset")

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset your password")

    def test_post_unknown_email_redirects_to_done(self):
        """Unknown email still shows the done page (avoids account enumeration)."""
        response = self.client.post(self.url, {"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))

    def test_post_valid_email_redirects_to_done(self):
        """A valid email redirects to the done page and sends an email."""
        from django.core import mail

        response = self.client.post(self.url, {"email": self.person.email})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.person.email, mail.outbox[0].to)

    def test_rate_limit_blocks_second_immediate_request(self):
        """A second request immediately after the first is blocked."""
        # First request — succeeds.
        self.client.post(self.url, {"email": self.person.email})

        # Second request — should be blocked.
        response = self.client.post(self.url, {"email": self.person.email})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many password reset requests")

    def test_rate_limit_allows_request_after_wait(self):
        """A request after the wait period has elapsed is allowed."""
        from django.core import mail

        # Pre-populate an old attempt (65 seconds ago = past the 1-min threshold).
        old_ts = (timezone.now() - datetime.timedelta(seconds=65)).isoformat()

        def mutate(prefs):
            prefs["pw_reset_attempts"] = [old_ts]
            return True

        utilities.update_prefs_atomic(self.person, mutate)

        response = self.client.post(self.url, {"email": self.person.email})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    def test_password_reset_done_page(self):
        response = self.client.get(reverse("password_reset_done"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your email")

    def test_password_reset_complete_page(self):
        response = self.client.get(reverse("password_reset_complete"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password reset complete")

    def test_password_reset_confirm_invalid_link(self):
        """An invalid reset link renders the invalid-link template content."""
        response = self.client.get(
            reverse(
                "password_reset_confirm",
                kwargs={"uidb64": "bad", "token": "bad-token"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")

    def test_full_reset_flow(self):
        """Full end-to-end: request → confirm link → set new password → login."""
        from django.core import mail

        # 1. Request reset.
        self.client.post(self.url, {"email": self.person.email})
        self.assertEqual(len(mail.outbox), 1)

        # 2. Extract confirm URL from the email body.
        email_body = mail.outbox[0].body
        confirm_path = None
        for line in email_body.splitlines():
            if "/password-reset/confirm/" in line:
                confirm_path = line.strip()
                break
        self.assertIsNotNone(confirm_path, "Confirm URL not found in email")

        # 3. GET the confirm page to retrieve the session token.
        get_response = self.client.get(confirm_path, follow=True)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Set a new password")

        # 4. POST the new password.
        post_url = (
            get_response.redirect_chain[-1][0]
            if get_response.redirect_chain
            else confirm_path
        )
        post_response = self.client.post(
            post_url,
            {"new_password1": "NewSecurePass42!", "new_password2": "NewSecurePass42!"},
            follow=True,
        )
        self.assertContains(post_response, "Password reset complete")

        # 5. Log in with the new password.
        logged_in = self.client.login(
            username=self.person.email, password="NewSecurePass42!"
        )
        self.assertTrue(logged_in)

    def test_login_page_links_to_password_reset(self):
        """The login page links 'Forgot your password?' to the reset page."""
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Forgot your password?")
        self.assertContains(response, f'href="{reverse("password_reset")}"')
