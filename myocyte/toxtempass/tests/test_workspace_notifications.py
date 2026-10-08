"""Tests for the delayed 'added to' and 'lost access to' workspace emails."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from toxtempass import Config, notifications
from toxtempass.models import EmailLog, WorkspaceInvitation, WorkspaceMember
from toxtempass.tests.fixtures.factories import (
    AdminFactory,
    PersonFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)

pytestmark = pytest.mark.django_db

COOLOFF = timedelta(minutes=Config._email_cooloff_minutes)


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = "https://toxtemp.example"
    settings.ADMINS = []


@pytest.fixture
def owner():
    return PersonFactory(first_name="Olivia", last_name="Owner")


@pytest.fixture
def member():
    return PersonFactory()


@pytest.fixture
def workspace(owner):
    return WorkspaceFactory(owner=owner, name="Liver models")


def _add(client, actor, workspace, person):
    """Invite ``person`` and let them accept, which is how anyone joins."""
    client.force_login(actor)
    response = client.post(
        reverse("add_workspace_member_by_email", args=[workspace.pk]),
        {"email": person.email},
    )
    assert response.json()["success"] is True
    invitation = WorkspaceInvitation.objects.get(workspace=workspace, user=person)
    client.force_login(person)
    client.post(
        reverse("respond_workspace_invitation", args=[invitation.pk]),
        {"action": "accept"},
    )


def _admin_add(workspace, person, by):
    """Add ``person`` the way the admin does, which still sends the 'added' email."""
    member = WorkspaceMemberFactory(workspace=workspace, user=person, added_by=by)
    notifications.notify_member_added(member, by)
    return member


def _remove(client, actor, workspace, person):
    client.force_login(actor)
    response = client.post(
        reverse("remove_workspace_member_by_email", args=[workspace.pk]),
        {"email": person.email},
    )
    assert response.json()["success"] is True


def _after_cooloff(extra_minutes=0):
    return timezone.now() + COOLOFF + timedelta(minutes=1 + extra_minutes)


def test_admin_added_member_is_emailed_once_after_the_cooloff(owner, workspace, member):
    _admin_add(workspace, member, owner)

    notifications.run_email_jobs(now=timezone.now() + timedelta(minutes=5))
    assert mail.outbox == []

    notifications.run_email_jobs(now=_after_cooloff())
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [member.email]
    assert message.subject == (
        "[ToxTempAssistant] Olivia Owner added you to the workspace “Liver models”"
    )
    assert WorkspaceMember.objects.get(workspace=workspace, user=member).notified_at

    notifications.run_email_jobs(now=_after_cooloff(60))
    assert len(mail.outbox) == 1


def test_several_adds_arrive_as_one_email(owner, member):
    _admin_add(WorkspaceFactory(owner=owner), member, owner)
    _admin_add(WorkspaceFactory(owner=owner), member, owner)

    notifications.run_email_jobs(now=_after_cooloff())

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "[ToxTempAssistant] You were added to 2 workspaces"
    statuses = sorted(EmailLog.objects.values_list("status", flat=True))
    assert statuses == [EmailLog.Status.MERGED, EmailLog.Status.SENT]


def test_add_undone_within_the_cooloff_sends_nothing(client, owner, workspace, member):
    _admin_add(workspace, member, owner)
    _remove(client, owner, workspace, member)

    notifications.run_email_jobs(now=_after_cooloff())

    assert mail.outbox == []


def test_removed_member_is_told_after_the_cooloff(client, owner, workspace, member):
    _admin_add(workspace, member, owner)
    notifications.run_email_jobs(now=_after_cooloff())
    assert len(mail.outbox) == 1

    _remove(client, owner, workspace, member)
    notifications.run_email_jobs(now=timezone.now() + timedelta(minutes=5))
    assert len(mail.outbox) == 1

    notifications.run_email_jobs(now=_after_cooloff())
    assert len(mail.outbox) == 2
    message = mail.outbox[1]
    assert message.subject == (
        "[ToxTempAssistant] You were removed from the workspace “Liver models”"
    )
    assert "by Olivia Owner" in message.body


def test_leaving_a_workspace_yourself_sends_nothing(client, workspace, member):
    WorkspaceMemberFactory(workspace=workspace, user=member, notified_at=timezone.now())

    _remove(client, member, workspace, member)
    notifications.run_email_jobs(now=_after_cooloff())

    assert mail.outbox == []


def test_removal_undone_within_the_cooloff_sends_nothing(
    client, owner, workspace, member
):
    WorkspaceMemberFactory(workspace=workspace, user=member, notified_at=timezone.now())

    _remove(client, owner, workspace, member)
    _add(client, owner, workspace, member)
    notifications.run_email_jobs(now=_after_cooloff())

    assert mail.outbox == []
    assert WorkspaceMember.objects.get(workspace=workspace, user=member).notified_at


def test_deleting_a_workspace_tells_the_other_members(client, owner, workspace, member):
    WorkspaceMemberFactory(workspace=workspace, user=member, notified_at=timezone.now())
    client.force_login(owner)

    client.post(reverse("delete_workspace", args=[workspace.pk]))
    notifications.run_email_jobs(now=_after_cooloff())

    assert [message.to for message in mail.outbox] == [[member.email]]
    assert mail.outbox[0].subject == (
        "[ToxTempAssistant] The workspace “Liver models” was deleted"
    )


def test_switched_off_workspace_emails_are_not_sent(owner, workspace, member):
    notifications.set_email_enabled(member, notifications.WORKSPACE_ADDED, False)

    _admin_add(workspace, member, owner)
    notifications.run_email_jobs(now=_after_cooloff())

    assert mail.outbox == []


def test_creating_a_workspace_sends_the_owner_nothing(owner):
    workspace = WorkspaceFactory(owner=owner)
    assert workspace.memberships.get().notified_at is not None
    assert not EmailLog.objects.exists()


def test_member_added_in_the_admin_is_emailed(client, workspace, member):
    admin = AdminFactory()
    client.force_login(admin)

    response = client.post(
        reverse("admin:toxtempass_workspacemember_add"),
        {"workspace": workspace.pk, "user": member.pk, "role": "member"},
    )

    assert response.status_code == 302
    assert WorkspaceMember.objects.get(workspace=workspace, user=member).added_by == admin
    notifications.run_email_jobs(now=_after_cooloff())
    assert [message.to for message in mail.outbox] == [[member.email]]
