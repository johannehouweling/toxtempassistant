"""Tests that server errors are emailed to the maintainers (ADMINS)."""
from django.conf import settings
from django.core import mail
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path


def _boom(request):
    raise RuntimeError("boom")


def _ok(request):
    return HttpResponse("ok")


urlpatterns = [
    path("boom/", _boom),
    path("ok/", _ok),
]


@override_settings(
    ROOT_URLCONF=__name__,
    DEBUG=False,
    ALLOWED_HOSTS=["testserver"],
    ADMINS=["maintainer-one@example.com", "maintainer-two@example.com"],
)
class ServerErrorEmailTests(TestCase):
    """Unhandled exceptions notify ADMINS; noise does not."""

    def setUp(self):
        self.client.raise_request_exception = False

    def test_unhandled_exception_emails_admins_once(self):
        response = self.client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, settings.ADMINS)
        self.assertEqual(message.from_email, settings.SERVER_EMAIL)
        self.assertTrue(message.subject.startswith(settings.EMAIL_SUBJECT_PREFIX))
        self.assertIn("RuntimeError", message.body)

    def test_not_found_does_not_email(self):
        response = self.client.get("/does-not-exist/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_disallowed_host_does_not_email(self):
        response = self.client.get("/ok/", HTTP_HOST="scanner.example.com")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])

    @override_settings(DEBUG=True)
    def test_debug_mode_does_not_email(self):
        self.client.get("/boom/")

        self.assertEqual(mail.outbox, [])
