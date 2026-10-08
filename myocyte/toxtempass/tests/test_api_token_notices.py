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
    WorkspaceApiToken,
    WorkspaceInvestigation,
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


def test_each_owner_gets_one_email_after_the_cooloff(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    assert not mail.outbox  # nothing straight away
    _after_cooloff()
    recipients = sorted(m.to[0] for m in mail.outbox)
    assert recipients == sorted([setup["alice"].email, setup["bob"].email])


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


def test_the_creator_is_not_told_about_their_own_token(client, setup):
    own = InvestigationFactory(owner=setup["admin"], title="Admin model")
    WorkspaceInvestigation.objects.create(workspace=setup["workspace"], investigation=own)
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


def test_an_owner_who_left_before_the_send_is_not_emailed(client, setup):
    _issue(client, setup["admin"], setup["workspace"])
    WorkspaceInvestigation.objects.filter(investigation__owner=setup["bob"]).delete()
    _after_cooloff()
    assert [m.to[0] for m in mail.outbox] == [setup["alice"].email]


def test_it_cannot_be_switched_off(client, setup):
    alice = setup["alice"]
    for kind in notifications.OPTIONAL_KINDS:
        notifications.set_email_enabled(alice, kind, False)
    alice.refresh_from_db()
    # Control: the opt-out really took effect for the optional kinds...
    assert not notifications.is_email_enabled(alice, notifications.WORKSPACE_ADDED)
    # ...but this kind is required, and cannot even be switched off.
    assert notifications.is_email_enabled(alice, notifications.API_TOKEN_CREATED)
    with pytest.raises(ValueError):
        notifications.set_email_enabled(alice, notifications.API_TOKEN_CREATED, False)
    _issue(client, setup["admin"], setup["workspace"])
    _after_cooloff()
    assert alice.email in {m.to[0] for m in mail.outbox}


def test_a_workspace_without_shared_investigations_emails_nobody(client):
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
