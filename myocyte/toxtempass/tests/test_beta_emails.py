"""Tests for the beta emails: approval, the approve link and the maintainers' digest."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from toxtempass import notifications, utilities
from toxtempass.models import EmailLog
from toxtempass.tests.fixtures.factories import AdminFactory, PersonFactory

pytestmark = pytest.mark.django_db

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
APPROVED_SUBJECT = "[ToxTempAssistant] Your beta access is approved"


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = "https://toxtemp.example"
    settings.ADMINS = ["maintainer@example.com"]


def _tomorrow_at(hour, minute=0):
    day = timezone.now().astimezone(AMSTERDAM).date() + timedelta(days=1)
    return datetime.combine(day, time(hour, minute), tzinfo=AMSTERDAM)


def _requester(requested_at, confirmed_at, **kwargs):
    person = PersonFactory(email_confirmed_at=confirmed_at, **kwargs)
    utilities.set_beta_requested(person)
    utilities.update_prefs_atomic(
        person,
        lambda prefs: prefs.update(beta_requested_at=requested_at.isoformat()) or True,
    )
    return person


def test_admitting_emails_once_and_revoking_sends_nothing(
    django_capture_on_commit_callbacks,
):
    user = PersonFactory()
    utilities.set_beta_requested(user)

    with django_capture_on_commit_callbacks(execute=True):
        assert utilities.set_beta_admitted(user, True) is True
        assert utilities.set_beta_admitted(user, True) is False
    assert [message.subject for message in mail.outbox] == [APPROVED_SUBJECT]

    with django_capture_on_commit_callbacks(execute=True):
        assert utilities.set_beta_admitted(user, False) is False
    assert len(mail.outbox) == 1

    with django_capture_on_commit_callbacks(execute=True):
        assert utilities.set_beta_admitted(user, True) is True
    assert len(mail.outbox) == 2


def test_admitting_from_the_beta_users_page_emails_the_user(
    client, django_capture_on_commit_callbacks
):
    requester = PersonFactory()
    utilities.set_beta_requested(requester)
    client.force_login(AdminFactory())

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("toggle_beta_admitted"), {"person_id": requester.pk, "admit": "1"}
        )

    assert [message.to for message in mail.outbox] == [[requester.email]]
    assert mail.outbox[0].subject == APPROVED_SUBJECT


def test_approve_link_needs_a_staff_login(client, django_capture_on_commit_callbacks):
    requester = PersonFactory()
    utilities.set_beta_requested(requester)
    url = reverse("approve_beta", args=[utilities.generate_beta_token(requester.pk)])

    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert anonymous.url.startswith(f"{reverse('login')}?next=")

    client.force_login(PersonFactory())
    assert client.get(url).status_code == 403
    requester.refresh_from_db()
    assert not requester.preferences.get("beta_admitted")

    client.force_login(AdminFactory())
    with django_capture_on_commit_callbacks(execute=True):
        response = client.get(url)
    assert response.status_code == 200
    requester.refresh_from_db()
    assert requester.preferences["beta_admitted"] is True
    assert [message.to for message in mail.outbox] == [[requester.email]]


def test_login_returns_to_next_only_within_the_site(client):
    user = PersonFactory(email="login.user@example.org")
    user.set_password("Pass-word-12345")
    user.save()
    credentials = {"username": user.email, "password": "Pass-word-12345"}

    response = client.post(f"{reverse('login')}?next=/beta/users/", credentials)
    assert response.json()["redirect_url"] == "/beta/users/"

    client.logout()
    response = client.post(f"{reverse('login')}?next=https://evil.example/", credentials)
    assert response.json()["redirect_url"] == reverse("overview")


def test_digest_lists_requests_confirmed_before_the_digest_hour(
    django_capture_on_commit_callbacks,
):
    morning = _tomorrow_at(8, 5)
    _requester(
        morning - timedelta(days=1),
        morning - timedelta(hours=20),
        email="early@example.org",
    )
    _requester(
        morning - timedelta(minutes=30),
        morning - timedelta(minutes=2),
        email="late@example.org",
    )
    _requester(morning - timedelta(days=1), None, email="unconfirmed@example.org")
    admitted = _requester(
        morning - timedelta(days=1),
        morning - timedelta(hours=20),
        email="admitted@example.org",
    )
    utilities.update_prefs_atomic(
        admitted, lambda prefs: prefs.update(beta_admitted=True) or True
    )

    # The job queues the digest to go out when its transaction commits.
    with django_capture_on_commit_callbacks(execute=True):
        notifications.run_email_jobs(now=morning)

    assert len(mail.outbox) == 1
    digest = mail.outbox[0]
    assert digest.to == ["maintainer@example.com"]
    assert digest.subject == "[ToxTempAssistant] 1 beta request is waiting for approval"
    assert "early@example.org" in digest.body
    assert "https://toxtemp.example/beta/approve/" in digest.body
    for left_out in ("late@", "unconfirmed@", "admitted@"):
        assert left_out not in digest.body

    with django_capture_on_commit_callbacks(execute=True):
        notifications.run_email_jobs(now=morning + timedelta(hours=3))
    assert len(mail.outbox) == 1


def test_no_digest_before_the_hour_or_without_pending_requests(
    django_capture_on_commit_callbacks,
):
    morning = _tomorrow_at(8, 5)
    with django_capture_on_commit_callbacks(execute=True):
        notifications.run_email_jobs(now=morning)
    assert not EmailLog.objects.filter(kind=notifications.MAINTAINER_BETA_DIGEST).exists()

    _requester(morning - timedelta(days=1), morning - timedelta(hours=20))
    with django_capture_on_commit_callbacks(execute=True):
        notifications.run_email_jobs(now=_tomorrow_at(7, 55))
    assert mail.outbox == []
