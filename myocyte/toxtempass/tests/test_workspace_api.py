"""Tests for workspace API tokens: who may issue them, and what they can read."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from toxtempass.models import (
    WorkspaceApiToken,
    WorkspaceInvestigation,
    WorkspaceRole,
)
from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    StudyFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


def _issue(client, user, workspace, **data):
    client.force_login(user)
    return client.post(
        reverse("workspace_token_create", args=[workspace.pk]),
        {"name": "reporting", **data},
    )


def _bearer(secret):
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


@pytest.fixture
def shared():
    """A workspace with one shared and one unshared ToxTemp."""
    workspace = WorkspaceFactory()
    inv = InvestigationFactory(owner=workspace.owner)
    in_ws = AssayFactory(study=StudyFactory(investigation=inv))
    outside = AssayFactory(study=StudyFactory(investigation=inv))
    other_inv = InvestigationFactory(owner=workspace.owner)
    unshared = AssayFactory(study=StudyFactory(investigation=other_inv))
    WorkspaceInvestigation.objects.create(workspace=workspace, investigation=inv)
    return workspace, in_ws, outside, unshared


class TestIssuing:
    def test_owner_and_admin_can_issue(self, client):
        workspace = WorkspaceFactory()
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        for user in (workspace.owner, admin):
            response = _issue(client, user, workspace)
            assert response.status_code == 200
            assert response.json()["token"].startswith("ttw_")

    def test_member_and_outsider_cannot_issue(self, client):
        workspace = WorkspaceFactory()
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        for user in (member, PersonFactory()):
            assert _issue(client, user, workspace).status_code == 404
        assert not WorkspaceApiToken.objects.exists()

    def test_only_hash_is_stored(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        token = WorkspaceApiToken.objects.get()
        assert secret not in (token.token_hash, token.prefix, token.name)
        assert secret.startswith(token.prefix)

    def test_lifetime_is_bounded(self, client, settings):
        workspace = WorkspaceFactory()
        assert (
            _issue(client, workspace.owner, workspace, expires_days="9999").status_code
            == 400
        )
        assert (
            _issue(client, workspace.owner, workspace, expires_days="abc").status_code
            == 400
        )

    def test_list_never_shows_secret(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        body = client.get(
            reverse("workspace_tokens", args=[workspace.pk])
        ).content.decode()
        assert secret not in body

    def test_admin_can_revoke_owners_token(self, client, shared):
        workspace, *_ = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        client.force_login(admin)
        token = WorkspaceApiToken.objects.get()
        client.post(reverse("workspace_token_revoke", args=[workspace.pk, token.pk]))
        client.logout()
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 401


class TestReading:
    def test_lists_only_shared_investigations(self, client, shared):
        workspace, in_ws, outside, unshared = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        response = client.get(reverse("api_assay_list"), **_bearer(secret))
        ids = {a["id"] for a in response.json()["assays"]}
        assert ids == {in_ws.pk, outside.pk}
        assert unshared.pk not in ids

    def test_detail_of_unshared_assay_is_404(self, client, shared):
        workspace, in_ws, _, unshared = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        ok = client.get(reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret))
        assert ok.status_code == 200
        gone = client.get(
            reverse("api_assay_detail", args=[unshared.pk]), **_bearer(secret)
        )
        assert gone.status_code == 404

    def test_token_of_another_workspace_sees_nothing_here(self, client, shared):
        _, in_ws, _, _ = shared
        other = WorkspaceFactory()
        secret = _issue(client, other.owner, other).json()["token"]
        client.logout()
        response = client.get(
            reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret)
        )
        assert response.status_code == 404

    def test_removing_investigation_stops_access(self, client, shared):
        workspace, in_ws, *_ = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        WorkspaceInvestigation.objects.filter(workspace=workspace).delete()
        response = client.get(
            reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret)
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("header", [None, "Bearer nope", "Basic abc", "Bearer "])
    def test_bad_credentials_are_401(self, client, header):
        extra = {} if header is None else {"HTTP_AUTHORIZATION": header}
        response = client.get(reverse("api_assay_list"), **extra)
        assert response.status_code == 401

    def test_expired_token_is_401(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        WorkspaceApiToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 401

    def test_session_login_is_not_enough(self, client, shared):
        workspace, *_ = shared
        client.force_login(workspace.owner)
        assert client.get(reverse("api_assay_list")).status_code == 401

    def test_token_survives_issuer_leaving(self, client):
        workspace = WorkspaceFactory()
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        secret = _issue(client, admin, workspace).json()["token"]
        client.logout()
        admin.delete()
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 200

    def test_use_is_recorded(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        assert WorkspaceApiToken.objects.get().last_used_at is None
        client.get(reverse("api_assay_list"), **_bearer(secret))
        assert WorkspaceApiToken.objects.get().last_used_at is not None


class TestWorkspaceUi:
    def test_settings_cog_only_for_owner_and_admin(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        from toxtempass import workspace as ws_views

        workspace = WorkspaceFactory()
        admin, member = PersonFactory(), PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        WorkspaceMemberFactory(workspace=workspace, user=member)

        seen = {}
        for label, user in (
            ("owner", workspace.owner),
            ("admin", admin),
            ("member", member),
        ):
            request = RequestFactory().get("/")
            request.user = user
            html = render_to_string(
                "toxtempass/base_extras/workspaces/workspace_list_partial.html",
                ws_views.get_workspace_list(request),
                request=request,
            )
            seen[label] = (
                'aria-label="Workspace settings"'
                in html  # the button; the JS is shown to all
            )
        assert seen == {"owner": True, "admin": True, "member": False}

    def test_every_member_sees_each_token_by_name(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        from toxtempass import workspace as ws_views

        workspace = WorkspaceFactory()
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)

        def chips(user) -> int:
            request = RequestFactory().get("/")
            request.user = user
            html = render_to_string(
                "toxtempass/base_extras/workspaces/workspace_list_partial.html",
                ws_views.get_workspace_list(request),
                request=request,
            )
            return html.count('class="badge text-bg-warning-subtle'), html

        def make(name: str) -> WorkspaceApiToken:
            return WorkspaceApiToken.objects.create(
                workspace=workspace,
                name=name,
                token_hash=name,
                prefix="ttw_x",
                expires_at=timezone.now() + timedelta(days=1),
            )

        assert chips(member)[0] == 0
        first, _ = make("reporting server"), make("dashboard")
        count, html = chips(member)
        assert count == 2
        assert "reporting server" in html and "dashboard" in html
        assert "ttw_x" not in html  # prefixes are for managers only
        assert chips(workspace.owner)[0] == 2
        first.revoked_at = timezone.now()
        first.save()
        assert chips(member)[0] == 1
