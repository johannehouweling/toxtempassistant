"""Nobody joins a workspace by being added: they are invited and must accept."""

from datetime import timedelta

import pytest
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from guardian.shortcuts import get_perms

from toxtempass import Config, notifications
from toxtempass import workspace as ws_views
from toxtempass.models import (
    EmailLog,
    Person,
    WorkspaceApiToken,
    WorkspaceInvestigation,
    WorkspaceInvitation,
    WorkspaceMember,
    WorkspaceRole,
)
from toxtempass.tests.fixtures.factories import (
    InvestigationFactory,
    PersonFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)

pytestmark = pytest.mark.django_db

COOLOFF = timedelta(minutes=Config._email_cooloff_minutes)


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.SITE_URL = "https://toxtemp.example"
    settings.ADMINS = []


@pytest.fixture
def owner():
    return PersonFactory(first_name="Olivia", last_name="Owner")


@pytest.fixture
def workspace(owner):
    return WorkspaceFactory(owner=owner, name="Liver models")


@pytest.fixture
def invitee():
    return PersonFactory(first_name="Ines", last_name="Invitee")


def _invite(client, actor, workspace, person, **extra):
    client.force_login(actor)
    return client.post(
        reverse("add_workspace_member_by_email", args=[workspace.pk]),
        {"email": person.email, **extra},
    )


def _respond(client, person, invitation, action):
    client.force_login(person)
    return client.post(
        reverse("respond_workspace_invitation", args=[invitation.pk]),
        {"action": action},
    )


def _after_cooloff():
    return timezone.now() + COOLOFF + timedelta(minutes=1)


class TestInviting:
    def test_adding_someone_only_invites_them(self, client, owner, workspace, invitee):
        investigation = InvestigationFactory(owner=owner)
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=investigation
        )
        response = _invite(client, owner, workspace, invitee)
        body = response.json()
        assert body["success"] is True and body["pending"] is True
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=invitee
        ).exists()
        assert "view_investigation" not in get_perms(invitee, investigation)
        invitation = WorkspaceInvitation.objects.get()
        assert body["invitation_id"] == invitation.pk
        assert invitation.invited_by == owner and invitation.role == "member"

    def test_the_role_is_kept_but_owner_is_refused(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee, role="admin")
        assert WorkspaceInvitation.objects.get().role == WorkspaceRole.ADMIN
        WorkspaceInvitation.objects.all().delete()
        _invite(client, owner, workspace, invitee, role="owner")
        assert WorkspaceInvitation.objects.get().role == WorkspaceRole.MEMBER

    def test_admins_can_invite_but_members_cannot(self, client, workspace, invitee):
        admin, member = PersonFactory(), PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        WorkspaceMemberFactory(workspace=workspace, user=member)
        assert _invite(client, member, workspace, invitee).status_code == 404
        assert _invite(client, admin, workspace, invitee).status_code == 200

    @pytest.mark.parametrize("case", ["member", "pending", "self"])
    def test_pointless_invitations_are_refused(self, client, owner, workspace, case):
        person = PersonFactory()
        if case == "member":
            WorkspaceMemberFactory(workspace=workspace, user=person)
        elif case == "pending":
            assert _invite(client, owner, workspace, person).status_code == 200
        else:
            person = owner
        assert _invite(client, owner, workspace, person).status_code == 400
        assert WorkspaceInvitation.objects.count() == (1 if case == "pending" else 0)

    def test_an_expired_invitation_can_be_replaced(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        WorkspaceInvitation.objects.update(expires_at=timezone.now() - timedelta(days=1))
        assert _invite(client, owner, workspace, invitee).status_code == 200
        assert not WorkspaceInvitation.objects.get().is_expired


class TestAnswering:
    def test_accepting_makes_a_member_with_access_and_credit(
        self, client, owner, workspace, invitee
    ):
        investigation = InvestigationFactory(owner=owner)
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=investigation
        )
        _invite(client, owner, workspace, invitee, role="admin")
        invitation = WorkspaceInvitation.objects.get()
        response = _respond(client, invitee, invitation, "accept")
        assert response.status_code == 302
        member = WorkspaceMember.objects.get(workspace=workspace, user=invitee)
        assert member.role == WorkspaceRole.ADMIN and member.added_by == owner
        assert member.notified_at is not None
        invitee.refresh_from_db()
        assert invitee.credit_by_name is True
        assert "view_investigation" in get_perms(invitee, investigation)
        assert not WorkspaceInvitation.objects.exists()

    def test_accepting_does_not_override_an_explicit_no(
        self, client, owner, workspace, invitee
    ):
        Person.objects.filter(pk=invitee.pk).update(credit_by_name=False)
        _invite(client, owner, workspace, invitee)
        _respond(client, invitee, WorkspaceInvitation.objects.get(), "accept")
        invitee.refresh_from_db()
        assert WorkspaceMember.objects.filter(user=invitee).exists()
        assert invitee.credit_by_name is False

    def test_declining_leaves_no_trace_of_membership(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        _respond(client, invitee, WorkspaceInvitation.objects.get(), "decline")
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=invitee
        ).exists()
        assert not WorkspaceInvitation.objects.exists()

    def test_it_can_only_be_answered_once(self, client, owner, workspace, invitee):
        _invite(client, owner, workspace, invitee)
        invitation = WorkspaceInvitation.objects.get()
        _respond(client, invitee, invitation, "accept")
        assert _respond(client, invitee, invitation, "accept").status_code == 404
        assert (
            WorkspaceMember.objects.filter(workspace=workspace, user=invitee).count() == 1
        )

    def test_only_the_invited_person_can_see_or_answer_it(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        invitation = WorkspaceInvitation.objects.get()
        for other in (owner, PersonFactory()):
            client.force_login(other)
            assert (
                client.get(
                    reverse("workspace_invitation", args=[invitation.pk])
                ).status_code
                == 404
            )
            assert _respond(client, other, invitation, "accept").status_code == 404
        assert not WorkspaceMember.objects.filter(user=invitee).exists()

    def test_an_expired_invitation_cannot_be_accepted(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        invitation = WorkspaceInvitation.objects.get()
        WorkspaceInvitation.objects.update(expires_at=timezone.now() - timedelta(days=1))
        assert _respond(client, invitee, invitation, "accept").status_code == 410
        assert not WorkspaceMember.objects.filter(user=invitee).exists()

    def test_the_page_explains_what_joining_means(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        client.force_login(invitee)
        page = client.get(
            reverse("workspace_invitation", args=[WorkspaceInvitation.objects.get().pk])
        ).content.decode()
        for expected in (
            "Ownership does not change",
            "someone else's investigation",
            "API tokens",
            "credited by name",
            "never your email address",
            "Accept and join",
        ):
            assert expected in page

    def test_rejoining_during_the_cooloff_cancels_the_lost_access_email(
        self, client, owner, workspace, invitee
    ):
        WorkspaceMemberFactory(
            workspace=workspace, user=invitee, notified_at=timezone.now()
        )
        client.force_login(owner)
        client.post(
            reverse("remove_workspace_member_by_email", args=[workspace.pk]),
            {"email": invitee.email},
        )
        _invite(client, owner, workspace, invitee)
        _respond(client, invitee, WorkspaceInvitation.objects.get(), "accept")
        notifications.run_email_jobs(now=_after_cooloff())
        assert not [m for m in mail.outbox if "removed" in m.subject]


class TestInvitationEmail:
    def test_it_goes_out_after_the_cooloff_and_explains_everything(
        self, client, owner, workspace, invitee
    ):
        WorkspaceApiToken.objects.create(
            workspace=workspace,
            name="reporting server",
            token_hash="h",
            prefix="ttw_x",
            expires_at=timezone.now() + timedelta(days=30),
        )
        _invite(client, owner, workspace, invitee)
        notifications.run_email_jobs(now=timezone.now() + timedelta(minutes=2))
        assert mail.outbox == []
        notifications.run_email_jobs(now=_after_cooloff())
        message = mail.outbox[0]
        assert message.to == [invitee.email]
        assert (
            "Olivia Owner invited you to the workspace “Liver models”" in message.subject
        )
        invitation = WorkspaceInvitation.objects.get()
        for expected in (
            "Ownership does not change",
            "reporting server",
            "credited by name",
            "never your email address",
            f"/workspace/invitation/{invitation.pk}/",
        ):
            assert expected in message.body
        assert "reporting server" in message.alternatives[0][0]

    def test_the_email_says_so_when_there_is_no_api_yet(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        notifications.run_email_jobs(now=_after_cooloff())
        assert "you will get an email if one is created" in mail.outbox[0].body

    def test_it_cannot_be_switched_off(self, client, owner, workspace, invitee):
        for kind in notifications.OPTIONAL_KINDS:
            notifications.set_email_enabled(invitee, kind, False)
        _invite(client, owner, workspace, invitee)
        notifications.run_email_jobs(now=_after_cooloff())
        assert [m.to for m in mail.outbox] == [[invitee.email]]

    @pytest.mark.parametrize("how", ["cancel", "decline"])
    def test_an_answered_invitation_sends_no_email(
        self, client, owner, workspace, invitee, how
    ):
        _invite(client, owner, workspace, invitee)
        invitation = WorkspaceInvitation.objects.get()
        if how == "cancel":
            client.force_login(owner)
            client.post(
                reverse("cancel_workspace_invitation", args=[workspace.pk, invitation.pk])
            )
        else:
            _respond(client, invitee, invitation, "decline")
        notifications.run_email_jobs(now=_after_cooloff())
        assert mail.outbox == []
        assert not EmailLog.objects.filter(status=EmailLog.Status.SENT).exists()


class TestCancelling:
    def test_owner_and_admin_can_cancel_but_members_cannot(
        self, client, owner, workspace, invitee
    ):
        admin, member = PersonFactory(), PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        WorkspaceMemberFactory(workspace=workspace, user=member)
        _invite(client, owner, workspace, invitee)
        url = reverse(
            "cancel_workspace_invitation",
            args=[workspace.pk, WorkspaceInvitation.objects.get().pk],
        )
        for denied in (member, PersonFactory()):
            client.force_login(denied)
            assert client.post(url).status_code == 404
        assert WorkspaceInvitation.objects.exists()
        client.force_login(admin)
        assert client.post(url).json()["success"] is True
        assert not WorkspaceInvitation.objects.exists()


class TestCredit:
    """Being named as an author is one setting of the person, for every workspace."""

    def test_a_person_can_withdraw_and_restore_it_from_the_privacy_tab(self, client):
        person = PersonFactory()
        client.force_login(person)
        url = reverse("account_set_credit")
        assert client.post(url, {"credit": "on"}).json() == {
            "success": True,
            "credit": True,
        }
        person.refresh_from_db()
        assert person.credit_by_name is True
        client.post(url, {"credit": "off"})
        person.refresh_from_db()
        assert person.credit_by_name is False

    def test_it_needs_a_login_and_a_post(self, client):
        url = reverse("account_set_credit")
        assert client.post(url, {"credit": "on"}).status_code == 302
        client.force_login(PersonFactory())
        assert client.get(url).status_code == 405

    def test_one_choice_covers_every_workspace_of_the_person(self, workspace):
        from toxtempass import api

        person = PersonFactory()
        other = WorkspaceFactory()
        for ws in (workspace, other):
            WorkspaceMemberFactory(workspace=ws, user=person)
        assert person.pk not in api._credited_ids(workspace)

        Person.objects.filter(pk=person.pk).update(credit_by_name=True)
        assert person.pk in api._credited_ids(workspace)
        assert person.pk in api._credited_ids(other)

        Person.objects.filter(pk=person.pk).update(credit_by_name=False)
        assert person.pk not in api._credited_ids(workspace)
        assert person.pk not in api._credited_ids(other)

    def test_the_switch_is_in_the_privacy_tab_and_not_on_the_workspace_card(
        self, client, owner, workspace
    ):
        client.force_login(owner)
        page = client.get(reverse("overview")).content.decode()
        assert 'id="credit-by-name"' in page
        assert "credit-switch" not in page

    def test_the_privacy_tab_shows_the_current_choice(self, client):
        person = PersonFactory()
        Person.objects.filter(pk=person.pk).update(credit_by_name=True)
        client.force_login(person)
        on = client.get(reverse("overview")).content.decode()
        assert 'id="credit-by-name" checked' in on
        Person.objects.filter(pk=person.pk).update(credit_by_name=False)
        off = client.get(reverse("overview")).content.decode()
        assert 'id="credit-by-name" checked' not in off


class TestWorkspaceTab:
    def _html(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return render_to_string(
            "toxtempass/base_extras/workspaces/workspace_list_partial.html",
            ws_views.get_workspace_list(request),
            request=request,
        )

    def test_managers_see_who_is_pending_and_members_do_not(
        self, client, owner, workspace, invitee
    ):
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        _invite(client, owner, workspace, invitee)
        assert f"{invitee.email} (invited)" in self._html(owner)
        assert f"{invitee.email} (invited)" not in self._html(member)

    def test_the_invitee_sees_the_invitation_waiting(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        html = self._html(invitee)
        assert "You are invited to" in html and "Liver models" in html
