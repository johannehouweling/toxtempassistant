"""Tests for the email pipeline in toxtempass/notifications.py."""

from datetime import timedelta
from smtplib import SMTPException
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from toxtempass import Config, notifications, utilities
from toxtempass.models import EmailLog
from toxtempass.tests.fixtures.factories import (
    PersonFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)

pytestmark = pytest.mark.django_db

SITE = "https://toxtemp.example"


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = SITE
    settings.ADMINS = ["maintainer@example.com"]


def test_immediate_email_is_sent_once_the_transaction_commits(
    django_capture_on_commit_callbacks,
):
    user = PersonFactory()
    with django_capture_on_commit_callbacks(execute=True):
        log = notifications.queue_email(notifications.PASSWORD_CHANGED, user=user)
        assert mail.outbox == []

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [user.email]
    assert message.subject == "[ToxTempAssistant] Your password was changed"
    assert f"{SITE}{reverse('password_reset')}" in message.body
    assert message.alternatives[0][1] == "text/html"
    log.refresh_from_db()
    assert log.status == EmailLog.Status.SENT
    assert log.sent_at is not None


def test_dedup_key_sends_an_email_only_once(django_capture_on_commit_callbacks):
    user = PersonFactory()
    with django_capture_on_commit_callbacks(execute=True):
        first = notifications.queue_email(
            notifications.PASSWORD_CHANGED, user=user, dedup_key="once"
        )
        second = notifications.queue_email(
            notifications.PASSWORD_CHANGED, user=user, dedup_key="once"
        )

    assert first is not None
    assert second is None
    assert len(mail.outbox) == 1


def test_switched_off_email_is_recorded_as_skipped():
    user = PersonFactory()
    notifications.set_email_enabled(user, notifications.WORKSPACE_ADDED, False)

    log = notifications.queue_email(
        notifications.WORKSPACE_ADDED,
        user=user,
        payload={"workspace_id": 1},
        send_after=timezone.now(),
    )

    assert log.status == EmailLog.Status.SKIPPED
    assert log.error == "Switched off by the user"


def test_account_emails_cannot_be_switched_off():
    user = PersonFactory()
    with pytest.raises(ValueError):
        notifications.set_email_enabled(user, notifications.PASSWORD_CHANGED, False)
    assert notifications.is_email_enabled(user, notifications.PASSWORD_CHANGED)


def test_daily_cap_per_recipient():
    user = PersonFactory()
    later = timezone.now() + timedelta(hours=1)
    with patch.object(Config, "_email_max_per_recipient_per_day", 2):
        logs = [
            notifications.queue_email(
                notifications.PASSWORD_CHANGED, user=user, send_after=later
            )
            for _ in range(3)
        ]

    assert [log.status for log in logs] == [
        EmailLog.Status.PENDING,
        EmailLog.Status.PENDING,
        EmailLog.Status.SKIPPED,
    ]


def test_failed_send_is_retried_and_then_marked_failed(
    django_capture_on_commit_callbacks,
):
    user = PersonFactory()
    with patch(
        "django.core.mail.EmailMessage.send",
        side_effect=SMTPException("mail server down"),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            log = notifications.queue_email(notifications.PASSWORD_CHANGED, user=user)
        log.refresh_from_db()
        assert log.status == EmailLog.Status.PENDING
        assert log.attempts == 1
        assert log.send_after > timezone.now()

        for _ in Config._email_retry_delays_minutes:
            notifications.run_email_jobs(now=log.send_after + timedelta(seconds=1))
            log.refresh_from_db()

    assert log.status == EmailLog.Status.FAILED
    assert log.attempts == len(Config._email_retry_delays_minutes) + 1
    assert "mail server down" in log.error
    assert mail.outbox == []


def test_maintainer_email_is_skipped_without_admins(settings):
    settings.ADMINS = []
    log = notifications.queue_email(notifications.MAINTAINER_COST_ALERT, payload={})
    assert log.status == EmailLog.Status.SKIPPED


def test_optional_email_carries_an_unsubscribe_link():
    workspace = WorkspaceFactory(name="Liver models")
    member = WorkspaceMemberFactory(workspace=workspace)
    notifications.queue_email(
        notifications.WORKSPACE_ADDED,
        user=member.user,
        payload={"workspace_id": workspace.pk},
        send_after=timezone.now(),
    )

    notifications.run_email_jobs(now=timezone.now() + timedelta(minutes=1))

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    unsubscribe = message.extra_headers["List-Unsubscribe"]
    assert unsubscribe.startswith(f"<{SITE}/email/unsubscribe/")
    assert message.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert unsubscribe.strip("<>") in message.body


def test_account_email_has_no_unsubscribe_link(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        notifications.queue_email(notifications.PASSWORD_CHANGED, user=PersonFactory())
    assert "List-Unsubscribe" not in mail.outbox[0].extra_headers


def test_unsubscribe_link_asks_first_and_then_switches_the_email_off(client):
    user = PersonFactory()
    token = utilities.generate_unsubscribe_token(user, notifications.WORKSPACE_ADDED)
    url = reverse("unsubscribe", args=[token])

    assert client.get(url).status_code == 200
    user.refresh_from_db()
    assert notifications.is_email_enabled(user, notifications.WORKSPACE_ADDED)

    assert client.post(url).status_code == 200
    user.refresh_from_db()
    assert not notifications.is_email_enabled(user, notifications.WORKSPACE_ADDED)


def test_unsubscribe_rejects_forged_tokens_and_account_emails(client):
    user = PersonFactory()
    assert client.post(reverse("unsubscribe", args=["forged"])).status_code == 400
    token = utilities.generate_unsubscribe_token(user, notifications.PASSWORD_CHANGED)
    assert client.post(reverse("unsubscribe", args=[token])).status_code == 400


def test_email_preference_view_switches_optional_kinds_only(client):
    user = PersonFactory()
    client.force_login(user)
    url = reverse("set_email_preference")

    response = client.post(
        url, {"kind": notifications.WORKSPACE_ACCESS_LOST, "enabled": "0"}
    )
    assert response.json() == {
        "success": True,
        "kind": notifications.WORKSPACE_ACCESS_LOST,
        "enabled": False,
    }
    user.refresh_from_db()
    assert not notifications.is_email_enabled(user, notifications.WORKSPACE_ACCESS_LOST)

    refused = client.post(url, {"kind": notifications.PASSWORD_CHANGED, "enabled": "0"})
    assert refused.status_code == 400


def test_send_left_unfinished_by_a_crash_is_retried():
    user = PersonFactory()
    log = notifications.queue_email(
        notifications.PASSWORD_CHANGED, user=user, send_after=timezone.now()
    )
    EmailLog.objects.filter(pk=log.pk).update(
        status=EmailLog.Status.SENDING, updated_at=timezone.now() - timedelta(hours=1)
    )

    notifications.run_email_jobs()

    assert len(mail.outbox) == 1
