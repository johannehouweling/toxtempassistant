"""Owners of shared investigations are told when an API token can read them."""

from datetime import timedelta

import pytest
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from toxtempass import Config, notifications
from toxtempass import workspace as ws_views
from toxtempass.models import (
    EmailLog,
    Person,
    WorkspaceApiToken,
    WorkspaceInvestigation,
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
def setup():
    """A workspace run by an admin, with investigations from two other owners."""
    workspace = WorkspaceFactory(
        owner=PersonFactory(first_name="Olivia", last_name="Owner")
    )
    admin = PersonFactory(first_name="Ada", last_name="Admin")
    WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
    alice = PersonFactory(first_name="Alice", last_name="Author")
    bob = PersonFactory()
    for person in (alice, bob):
        WorkspaceMemberFactory(workspace=workspace, user=person)
    alice_a = InvestigationFactory(owner=alice, title="Liver spheroids")
    alice_b = InvestigationFactory(owner=alice, title="Kidney organoids")
    bob_inv = InvestigationFactory(owner=bob, title="Skin model")
    for inv in (alice_a, alice_b, bob_inv):
        WorkspaceInvestigation.objects.create(workspace=workspace, investigation=inv)
    return {"workspace": workspace, "admin": admin, "alice": alice, "bob": bob}


def _issue(client, user, workspace, name="reporting server"):
    client.force_login(user)
    response = client.post(
        reverse("workspace_token_create", args=[workspace.pk]), {"name": name}
    )
    assert response.json()["success"] is True
    return response.json()


def _after_cooloff():
    notifications.run_email_jobs(now=timezone.now() + COOLOFF + timedelta(minutes=1))


def test_every_member_but_the_creator_gets_one_email_after_the_cooloff(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    assert not mail.outbox  # nothing straight away
    _after_cooloff()
    recipients = sorted(m.to[0] for m in mail.outbox)
    expected = [setup["workspace"].owner.email, setup["alice"].email, setup["bob"].email]
    assert recipients == sorted(expected)  # the creator, Ada, is not among them


def test_the_email_names_what_is_exposed_and_not_the_secret(client, setup):
    body = _issue(client, setup["admin"], setup["workspace"], name="reporting server")
    _after_cooloff()
    message = next(m for m in mail.outbox if m.to == [setup["alice"].email])
    text = message.body
    for expected in (
        "reporting server",
        "Ada Admin",
        "Liver spheroids",
        "Kidney organoids",
    ):
        assert expected in text
    assert "Skin model" not in text  # Bob's investigation is not Alice's concern
    assert body["token"] not in text and body["info"]["prefix"] not in text
    assert "Liver spheroids" in message.alternatives[0][0]


def test_members_without_investigations_are_told_too(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    _after_cooloff()
    message = next(m for m in mail.outbox if m.to == [setup["workspace"].owner.email])
    assert "This includes your own investigations" not in message.body
    assert "now has API access" in message.subject


def test_credit_wording_depends_on_whether_they_opted_out(client, setup):
    assert setup["alice"].credit_by_name is True  # the default
    Person.objects.filter(pk=setup["bob"].pk).update(credit_by_name=False)
    _issue(client, setup["admin"], setup["workspace"])
    _after_cooloff()
    alice = next(m for m in mail.outbox if m.to == [setup["alice"].email]).body
    bob = next(m for m in mail.outbox if m.to == [setup["bob"].email]).body
    assert "You are credited by name (this is on by default)" in alice
    assert 'switch off "Credit me by name"' in alice
    assert "You switched off being credited by name" in bob
    assert 'switch on "Credit me by name"' in bob


def test_the_creator_is_not_told_about_their_own_token(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    _after_cooloff()
    assert setup["admin"].email not in {m.to[0] for m in mail.outbox}


def test_a_token_revoked_during_the_cooloff_sends_nothing(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    token = WorkspaceApiToken.objects.get()
    client.post(reverse("workspace_token_revoke", args=[setup["workspace"].pk, token.pk]))
    _after_cooloff()
    assert not mail.outbox
    assert not EmailLog.objects.filter(
        kind=notifications.API_TOKEN_CREATED, status=EmailLog.Status.SENT
    ).exists()


def test_someone_who_left_before_the_send_is_not_emailed(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    WorkspaceMember.objects.filter(user=setup["bob"]).delete()
    _after_cooloff()
    assert setup["bob"].email not in {m.to[0] for m in mail.outbox}
    assert setup["alice"].email in {m.to[0] for m in mail.outbox}


def test_it_cannot_be_switched_off(client, setup):
    alice = setup["alice"]
    for kind in notifications.OPTIONAL_KINDS:
        notifications.set_email_enabled(alice, kind, False)
    alice.refresh_from_db()
    # Control: the opt-out really took effect for the optional kinds...
    assert not notifications.is_email_enabled(
        alice, notifications.WORKSPACE_ACCESS_LOST
    )
    # ...but this kind is required, and cannot even be switched off.
    assert notifications.is_email_enabled(alice, notifications.API_TOKEN_CREATED)
    with pytest.raises(ValueError):
        notifications.set_email_enabled(alice, notifications.API_TOKEN_CREATED, False)
    _issue(client, setup["admin"], setup["workspace"])
    _after_cooloff()
    assert alice.email in {m.to[0] for m in mail.outbox}


def test_a_workspace_of_one_emails_nobody(client):
    workspace = WorkspaceFactory()
    _issue(client, workspace.owner, workspace)
    _after_cooloff()
    assert not mail.outbox


def test_cards_carry_the_token_names_for_the_sharing_warning(setup):
    workspace = setup["workspace"]
    WorkspaceApiToken.objects.create(
        workspace=workspace,
        name="reporting server",
        token_hash="h",
        prefix="ttw_x",
        expires_at=timezone.now() + timedelta(days=1),
    )
    request = RequestFactory().get("/")
    request.user = setup["alice"]
    html = render_to_string(
        "toxtempass/base_extras/workspaces/workspace_list_partial.html",
        ws_views.get_workspace_list(request),
        request=request,
    )
    assert 'data-token-name="reporting server"' in html
    assert 'id="addAssayApiWarning"' in html
