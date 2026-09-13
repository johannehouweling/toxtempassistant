"""Tests for email confirmation: signup, the link, resending, cleanup and the command."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from toxtempass import Config, notifications, utilities
from toxtempass.models import EmailLog, Investigation, Person
from toxtempass.tests.fixtures.factories import InvestigationFactory, PersonFactory

pytestmark = pytest.mark.django_db

SITE = "https://toxtemp.example"
SIGNUP_DATA = {
    "email": "new.user@example.org",
    "first_name": "New",
    "last_name": "User",
    "organization": "RIVM",
    "password1": "a-Long-and-unusual-pass-42",
    "password2": "a-Long-and-unusual-pass-42",
    "has_accepted_tos": "on",
}


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = SITE
    settings.ADMINS = ["maintainer@example.com"]


def _unconfirmed(**kwargs):
    return PersonFactory(email_confirmed_at=None, **kwargs)


def test_signup_emails_a_confirmation_link_and_not_the_maintainers(
    client, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("signup"), SIGNUP_DATA)

    assert response.json()["success"] is True
    user = Person.objects.get(email=SIGNUP_DATA["email"])
    assert user.email_confirmed_at is None
    assert user.delete_if_unconfirmed is True
    assert user.preferences["beta_signup"] is True
    assert [message.to for message in mail.outbox] == [[SIGNUP_DATA["email"]]]
    assert f"{SITE}/account/confirm-email/" in mail.outbox[0].body


def test_confirmation_link_confirms_the_address(client):
    user = _unconfirmed()
    token = utilities.generate_email_confirmation_token(user)
    url = reverse("confirm_email", args=[token])

    response = client.get(url)
    assert response.status_code == 200
    assert b"is now confirmed" in response.content
    user.refresh_from_db()
    assert user.email_confirmed_at is not None

    assert b"was already confirmed" in client.get(url).content


def test_confirmation_link_rejects_forged_expired_and_outdated_tokens(client):
    user = _unconfirmed()
    token = utilities.generate_email_confirmation_token(user)

    assert client.get(reverse("confirm_email", args=[token + "x"])).status_code == 400
    with patch.object(Config, "_email_confirmation_valid_days", 0):
        assert client.get(reverse("confirm_email", args=[token])).status_code == 400
    user.email = "changed@example.org"
    user.save()
    assert client.get(reverse("confirm_email", args=[token])).status_code == 400

    user.refresh_from_db()
    assert user.email_confirmed_at is None


def test_resending_the_link_is_rate_limited(client, django_capture_on_commit_callbacks):
    user = _unconfirmed()
    client.force_login(user)
    url = reverse("resend_confirmation_email")

    with django_capture_on_commit_callbacks(execute=True):
        first = client.post(url)
    assert first.json()["success"] is True
    assert len(mail.outbox) == 1

    second = client.post(url)
    assert second.status_code == 429
    assert "Please wait" in second.json()["error"]
    assert len(mail.outbox) == 1


def test_resend_does_nothing_for_a_confirmed_address(client):
    client.force_login(PersonFactory())
    response = client.post(reverse("resend_confirmation_email"))
    assert "already confirmed" in response.json()["message"]
    assert not EmailLog.objects.exists()


def test_banner_asks_only_unconfirmed_users_to_confirm(client):
    page = reverse("beta_wait")
    client.force_login(_unconfirmed())
    assert b"Please confirm your email address" in client.get(page).content

    client.force_login(PersonFactory())
    assert b"Please confirm your email address" not in client.get(page).content

    client.force_login(_unconfirmed(is_staff=True))
    assert b"Please confirm your email address" not in client.get(page).content


def test_stale_unconfirmed_signups_are_deleted_and_nothing_else():
    now = timezone.now()
    stale = _unconfirmed(date_joined=now - timedelta(days=8))
    InvestigationFactory(owner=stale)
    recent = _unconfirmed(date_joined=now - timedelta(days=2))
    existing = _unconfirmed(
        date_joined=now - timedelta(days=400), delete_if_unconfirmed=False
    )
    staff = _unconfirmed(date_joined=now - timedelta(days=30), is_staff=True)
    confirmed = PersonFactory(date_joined=now - timedelta(days=30))

    notifications.run_email_jobs(now=now)

    assert not Person.objects.filter(pk=stale.pk).exists()
    assert not Investigation.objects.filter(owner_id=stale.pk).exists()
    remaining = set(Person.objects.values_list("pk", flat=True))
    assert {recent.pk, existing.pk, staff.pk, confirmed.pk} <= remaining


def test_confirmation_requests_go_to_each_existing_account_once(
    django_capture_on_commit_callbacks,
):
    existing = _unconfirmed(delete_if_unconfirmed=False)
    _unconfirmed()  # a new signup already got its link at signup
    PersonFactory(delete_if_unconfirmed=False)  # confirmed
    _unconfirmed(delete_if_unconfirmed=False, is_staff=True)

    dry_run = StringIO()
    call_command("send_confirmation_requests", "--dry-run", stdout=dry_run)
    assert dry_run.getvalue().strip() == f"Would email {existing.email}"
    assert not EmailLog.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        call_command("send_confirmation_requests", stdout=StringIO())
        call_command("send_confirmation_requests", stdout=StringIO())

    assert [message.to for message in mail.outbox] == [[existing.email]]
    assert "now asks everyone to confirm" in mail.outbox[0].body


def test_password_reset_confirms_the_address_and_sends_a_notice(
    client, django_capture_on_commit_callbacks
):
    user = _unconfirmed()
    user.set_password("old-Password-123")
    user.save()
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    page = client.get(reverse("password_reset_confirm", args=[uid, token]), follow=True)
    set_password_url = page.redirect_chain[-1][0]
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            set_password_url,
            {
                "new_password1": "New-Secure-Pass-42",
                "new_password2": "New-Secure-Pass-42",
            },
        )

    user.refresh_from_db()
    assert user.email_confirmed_at is not None
    assert [message.subject for message in mail.outbox] == [
        "[ToxTempAssistant] Your password was changed"
    ]
