"""Nobody joins a workspace by being added: they are invited and must accept."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

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


class TestInvitationLimits:
    """An invitation is an email to someone else, so sending them is limited."""

    def test_one_person_can_only_send_so_many_a_day(self, client, owner, workspace):
        with patch.object(Config, "_workspace_invites_per_user_per_day", 2):
            people = [PersonFactory() for _ in range(3)]
            statuses = [
                _invite(client, owner, workspace, person).status_code for person in people
            ]
        assert statuses == [200, 200, 429]
        assert WorkspaceInvitation.objects.count() == 2

    def test_the_same_person_cannot_be_invited_over_and_over(
        self, client, owner, invitee
    ):
        """Declining and being invited again is not a way to keep mailing someone."""
        statuses = []
        for _ in range(Config._workspace_invites_per_pair_per_month + 1):
            workspace = WorkspaceFactory(owner=owner)
            response = _invite(client, owner, workspace, invitee)
            statuses.append(response.status_code)
            if response.status_code == 200:
                _respond(client, invitee, WorkspaceInvitation.objects.get(), "decline")
        assert statuses[:-1] == [200] * Config._workspace_invites_per_pair_per_month
        assert statuses[-1] == 429
        assert "several times" in response.json()["error"]

    def test_inviting_a_colleague_who_says_yes_is_not_limited(
        self, client, owner, invitee
    ):
        """Four workspaces, one colleague who accepts each: nothing to stop."""
        for _ in range(Config._workspace_invites_per_pair_per_month + 2):
            workspace = WorkspaceFactory(owner=owner)
            response = _invite(client, owner, workspace, invitee)
            assert response.status_code == 200
            _respond(client, invitee, WorkspaceInvitation.objects.get(), "accept")

    def test_accepted_ones_do_not_hide_a_pile_of_declined_ones(
        self, client, owner, invitee
    ):
        limit = Config._workspace_invites_per_pair_per_month
        accepted = WorkspaceFactory(owner=owner)
        _invite(client, owner, accepted, invitee)
        _respond(client, invitee, WorkspaceInvitation.objects.get(), "accept")
        statuses = []
        for _ in range(limit + 1):
            workspace = WorkspaceFactory(owner=owner)
            response = _invite(client, owner, workspace, invitee)
            statuses.append(response.status_code)
            if response.status_code == 200:
                _respond(client, invitee, WorkspaceInvitation.objects.get(), "decline")
        assert statuses == [200] * limit + [429]

    def test_the_limit_is_per_person_not_global(self, client, owner, workspace, invitee):
        other = PersonFactory()
        with patch.object(Config, "_workspace_invites_per_pair_per_month", 1):
            assert _invite(client, owner, workspace, invitee).status_code == 200
            assert _invite(client, owner, workspace, other).status_code == 200

    def test_another_inviter_is_not_affected(self, client, owner, workspace, invitee):
        stranger = PersonFactory()
        other_ws = WorkspaceFactory(owner=stranger)
        with patch.object(Config, "_workspace_invites_per_user_per_day", 1):
            assert _invite(client, owner, workspace, invitee).status_code == 200
            assert _invite(client, stranger, other_ws, PersonFactory()).status_code == 200


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
        assert invitee.credit_by_name is True  # on by default; accepting changes nothing
        assert "view_investigation" in get_perms(invitee, investigation)
        assert not WorkspaceInvitation.objects.exists()

    def test_accepting_does_not_override_an_opt_out(
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

    def test_it_can_be_switched_off_and_the_invitation_still_waits_in_the_app(
        self, client, owner, workspace, invitee
    ):
        """Nobody can be mailed by strangers they cannot silence."""
        notifications.set_email_enabled(
            invitee, notifications.WORKSPACE_INVITATION, False
        )
        assert _invite(client, owner, workspace, invitee).status_code == 200
        notifications.run_email_jobs(now=_after_cooloff())
        assert mail.outbox == []
        # Still there to answer, from the Workspaces tab.
        invitation = WorkspaceInvitation.objects.get()
        html = TestWorkspaceTab()._html(invitee)
        assert "You are invited to" in html
        assert _respond(client, invitee, invitation, "accept").status_code == 302
        assert WorkspaceMember.objects.filter(workspace=workspace, user=invitee).exists()

    def test_the_email_carries_an_unsubscribe_link_and_is_in_the_settings(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        notifications.run_email_jobs(now=_after_cooloff())
        assert "List-Unsubscribe" in mail.outbox[0].extra_headers
        kinds = {s["kind"] for s in notifications.email_settings_for(invitee)}
        assert notifications.WORKSPACE_INVITATION in kinds
        assert notifications.WORKSPACE_ADDED not in kinds

    def test_the_unsubscribe_link_switches_it_off(self, client, invitee):
        from toxtempass import utilities

        token = utilities.generate_unsubscribe_token(
            invitee, notifications.WORKSPACE_INVITATION
        )
        assert client.post(reverse("unsubscribe", args=[token])).status_code == 200
        invitee.refresh_from_db()
        assert not notifications.is_email_enabled(
            invitee, notifications.WORKSPACE_INVITATION
        )

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
        # On by default, in every workspace they belong to.
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

    def test_it_is_on_by_default_and_the_privacy_tab_says_so(self, client):
        person = PersonFactory()
        assert person.credit_by_name is True
        client.force_login(person)
        on = client.get(reverse("overview")).content.decode()
        assert 'id="credit-by-name" checked' in on
        assert "On by default" in on
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

    def test_the_cards_have_no_credit_switch_any_more(self, client, owner, workspace):
        html = self._html(owner)
        assert "Liver models" in html
        assert "credit-switch" not in html and "Credit me by name" not in html
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        html = self._html(member)
        assert "Liver models" in html
        assert "credit-switch" not in html and "Credit me by name" not in html

    def test_every_card_is_in_a_column_so_deleting_it_removes_it(
        self, client, owner, workspace
    ):
        """The delete script removes the card's column; a card without one stayed."""
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        WorkspaceFactory(owner=member, name="Mine")
        # Only the server-rendered cards: the script below holds templates of its own.
        html = self._html(member).split("<style>")[0]
        card = '<div class="card border-0 h-100 workspace-card workspace-list'
        wrapped = '<div class="col">\n          ' + card
        assert html.count(card) == 2 and html.count(wrapped) == 2

    def test_the_delete_script_removes_the_card_even_without_a_column(self):
        script = (
            Path(ws_views.__file__).parent
            / "templates/toxtempass/base_extras/workspaces/workspace_js.html"
        ).read_text(encoding="utf-8")
        assert 'parent.classList.contains("col") ? parent : workspaceEl' in script

    def _script(self, user) -> str:
        import re

        html = self._html(user)
        return max(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S), key=len)

    def test_a_card_built_in_the_browser_matches_the_one_the_server_renders(
        self, client, owner
    ):
        """A new workspace showed no key and no cogwheel until the page was reloaded."""
        script = self._script(owner)
        # Django tags run once when the page loads, so none may be left in the script.
        assert "{%" not in script and "{{" not in script
        assert f'const CURRENT_USER_ID = "{owner.pk}"' in script
        chip = script[script.index("const OWNER_CHIP_HTML") :]
        chip = chip[: chip.index("function settingsButtonHtml")]
        assert "bi-key-fill" in chip and "bg-success-subtle" in chip
        assert "member-row member-chip" in chip and ">You<" in chip
        # Both cards the script builds get the owner chip and the settings cogwheel.
        assert script.count("${OWNER_CHIP_HTML}") == 2
        assert "${settingsButtonHtml(true)}" in script  # hidden until it exists
        assert "${settingsButtonHtml(false)}" in script
        assert "btn-api-tokens" in script and "Workspace settings" in script
        inserted = script.split("function insertWorkspaceIntoDOM")[1][:2500]
        assert "No members yet" not in inserted

    def test_the_cogwheel_of_a_new_workspace_appears_once_it_exists(self, owner):
        script = self._script(owner)
        created = script[script.index("function onBlurSave") :][:1800]
        assert '.btn-api-tokens")?.classList.remove("d-none")' in created

    def test_the_rendered_script_is_valid_javascript(self, owner, tmp_path):
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        path = tmp_path / "workspaces.js"
        path.write_text(self._script(owner), encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_the_dialogs_do_not_promise_anonymity_and_link_the_docs(
        self, owner, workspace
    ):
        html = self._html(owner)
        assert "without naming any person" not in html
        flat = " ".join(html.split())
        assert "They see the authors of its ToxTemps by name" in flat
        assert "unless a person has switched that off under Privacy" in flat
        assert reverse("api_docs") in html and "API documentation" in html

    def test_token_chips_are_readable(self, client, owner, workspace):
        """A chip needs a real background and text colour, or its text is white."""
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        WorkspaceApiToken.objects.create(
            workspace=workspace, name="reporting", token_hash="c" * 64,
            prefix="ttw_abcd", created_by=owner,
            expires_at=timezone.now() + timedelta(days=30),
        )
        for person in (owner, member):
            html = self._html(person)
            assert 'data-token-name="reporting"' in html
            start = html.index("member-chip api-access-chip")
            chip = html[start : html.index("</span></span>", start) + 14]
            # Styled like a person: the same pill and round avatar, with the network icon.
            assert "member-avatar" in chip and "bi-hdd-network-fill" in chip
            assert '<span class="member-text">reporting</span>' in chip
            assert "badge" not in chip

    def test_the_chip_script_uses_real_bootstrap_classes(self):
        script = (
            Path(ws_views.__file__).parent
            / "templates/toxtempass/base_extras/workspaces/workspace_js.html"
        ).read_text(encoding="utf-8")
        assert "badge" not in script.split("api-access-chip")[1][:400]
        assert "member-chip api-access-chip" in script
        assert 'avatar.className' in script and "bi bi-hdd-network-fill" in script

    def test_managers_see_who_is_pending_and_members_do_not(
        self, client, owner, workspace, invitee
    ):
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        _invite(client, owner, workspace, invitee)
        assert f"{invitee.email} (invited)" in self._html(owner)
        assert f"{invitee.email} (invited)" not in self._html(member)

    def test_a_pending_invitation_chip_has_a_white_question_icon(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        html = self._html(owner)
        start = html.index("invite-row")
        chip = html[start : html.index("(invited)", start)]
        assert "bi-patch-question-fill" in chip and "text-white" in chip
        assert "hourglass" not in html
        script = (
            Path(ws_views.__file__).parent
            / "templates/toxtempass/base_extras/workspaces/workspace_js.html"
        ).read_text(encoding="utf-8")
        assert "hourglass" not in script
        start = script.index("invite-row")
        added = script[start : script.index("bi-patch-question-fill")]
        assert "text-white member-avatar" in added

    def test_the_invitation_page_and_email_say_credit_is_on_by_default(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        notifications.run_email_jobs(now=_after_cooloff())
        invitation = WorkspaceInvitation.objects.get()
        client.force_login(invitee)
        page = client.get(reverse("workspace_invitation", args=[invitation.pk])).content
        for text in (mail.outbox[0].body, page.decode()):
            flat = " ".join(text.split())
            assert "Unless you switch it off, you are credited by name" in flat
            assert "under Privacy in the user menu" in flat
            assert "agree to be credited" not in flat

    def test_the_invitee_sees_the_invitation_waiting(
        self, client, owner, workspace, invitee
    ):
        _invite(client, owner, workspace, invitee)
        html = self._html(invitee)
        assert "You are invited to" in html and "Liver models" in html
