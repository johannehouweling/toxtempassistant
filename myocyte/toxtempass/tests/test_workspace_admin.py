"""Workspace admin: the permission side effects must match the views.

A bare ModelAdmin would write the WorkspaceInvestigation row without the
view_investigation grants, so these tests assert the grants and — harder — the
two revoke exemptions: the investigation owner keeps their baseline perm, and
so does anyone reaching the same investigation through another workspace.
"""

import pytest
from django.urls import reverse
from guardian.shortcuts import get_perms

from toxtempass.models import (
    Workspace,
    WorkspaceInvestigation,
    WorkspaceMember,
    WorkspaceRole,
)
from toxtempass.tests.fixtures.factories import (
    InvestigationFactory,
    PersonFactory,
    WorkspaceFactory,
    WorkspaceInvestigationFactory,
    WorkspaceMemberFactory,
)
from toxtempass.workspace_perms import (
    grant_investigation_to_members,
    grant_shared_investigations_to_member,
    members_losing_access,
    revoke_investigation_from_members,
    revoke_shared_investigations_from_member,
)


def can_view(user, investigation) -> bool:
    return "view_investigation" in get_perms(user, investigation)


# ── the rules themselves ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_grant_reaches_every_member():
    owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    members = [PersonFactory.create() for _ in range(3)]
    for m in members:
        WorkspaceMemberFactory.create(workspace=workspace, user=m)
    investigation = InvestigationFactory.create(owner=owner)

    granted = grant_investigation_to_members(workspace, investigation)

    assert granted == 4  # three members plus the auto-created OWNER row
    for m in members:
        assert can_view(m, investigation)


@pytest.mark.django_db
def test_revoke_never_touches_the_investigation_owner():
    """Investigation.save() grants the owner baseline perms; they are not ours."""
    inv_owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=PersonFactory.create())
    WorkspaceMemberFactory.create(workspace=workspace, user=inv_owner)
    investigation = InvestigationFactory.create(owner=inv_owner)
    grant_investigation_to_members(workspace, investigation)

    assert inv_owner not in members_losing_access(workspace, investigation)
    revoke_investigation_from_members(workspace, investigation)
    assert can_view(inv_owner, investigation)


@pytest.mark.django_db
def test_revoke_keeps_access_held_through_another_workspace():
    owner = PersonFactory.create()
    member = PersonFactory.create()
    investigation = InvestigationFactory.create(owner=owner)

    first = WorkspaceFactory.create(owner=owner)
    second = WorkspaceFactory.create(owner=owner)
    for ws in (first, second):
        WorkspaceMemberFactory.create(workspace=ws, user=member)
        WorkspaceInvestigationFactory.create(workspace=ws, investigation=investigation)
    grant_investigation_to_members(first, investigation)

    # Unshare from the first workspace only.
    WorkspaceInvestigation.objects.filter(
        workspace=first, investigation=investigation
    ).delete()
    revoke_investigation_from_members(first, investigation)

    assert can_view(member, investigation), "second workspace still shares it"


@pytest.mark.django_db
def test_revoke_applies_when_no_other_workspace_holds_it():
    owner = PersonFactory.create()
    member = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    WorkspaceMemberFactory.create(workspace=workspace, user=member)
    investigation = InvestigationFactory.create(owner=owner)
    grant_investigation_to_members(workspace, investigation)
    assert can_view(member, investigation)

    revoke_investigation_from_members(workspace, investigation)

    assert not can_view(member, investigation)
    assert can_view(owner, investigation), "owner keeps baseline access"


@pytest.mark.django_db
def test_member_grant_and_revoke_cover_everything_shared():
    owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    investigations = [InvestigationFactory.create(owner=owner) for _ in range(2)]
    for inv in investigations:
        WorkspaceInvestigationFactory.create(workspace=workspace, investigation=inv)

    joiner = PersonFactory.create()
    WorkspaceMemberFactory.create(workspace=workspace, user=joiner)
    assert grant_shared_investigations_to_member(workspace, joiner) == 2
    assert all(can_view(joiner, inv) for inv in investigations)

    assert revoke_shared_investigations_from_member(workspace, joiner) == 2
    assert not any(can_view(joiner, inv) for inv in investigations)


# ── through the admin ─────────────────────────────────────────────────────────


@pytest.fixture
def admin_client_su(client, db):
    admin = PersonFactory.create(is_superuser=True, is_staff=True)
    client.force_login(admin)
    return client


@pytest.mark.django_db
def test_workspace_models_are_registered(admin_client_su):
    """The gap this change closes: they were absent from the admin entirely."""
    for model in ("workspace", "workspacemember", "workspaceinvestigation"):
        url = reverse(f"admin:toxtempass_{model}_changelist")
        assert admin_client_su.get(url).status_code == 200


@pytest.mark.django_db
def test_sharing_via_admin_grants_view_permission(admin_client_su):
    """The whole point: an admin-created share must grant perms, not just a row."""
    owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    member = PersonFactory.create()
    WorkspaceMemberFactory.create(workspace=workspace, user=member)
    investigation = InvestigationFactory.create(owner=PersonFactory.create())

    assert not can_view(member, investigation)

    response = admin_client_su.post(
        reverse("admin:toxtempass_workspaceinvestigation_add"),
        {"workspace": workspace.pk, "investigation": investigation.pk},
    )

    assert response.status_code == 302
    assert WorkspaceInvestigation.objects.filter(
        workspace=workspace, investigation=investigation
    ).exists()
    assert can_view(member, investigation), "row written but no permission granted"


@pytest.mark.django_db
def test_unsharing_via_admin_revokes_but_spares_the_owner(admin_client_su):
    inv_owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=PersonFactory.create())
    member = PersonFactory.create()
    for user in (member, inv_owner):
        WorkspaceMemberFactory.create(workspace=workspace, user=user)
    investigation = InvestigationFactory.create(owner=inv_owner)
    link = WorkspaceInvestigationFactory.create(
        workspace=workspace, investigation=investigation
    )
    grant_investigation_to_members(workspace, investigation)

    response = admin_client_su.post(
        reverse("admin:toxtempass_workspaceinvestigation_delete", args=[link.pk]),
        {"post": "yes"},
    )

    assert response.status_code == 302
    assert not WorkspaceInvestigation.objects.filter(pk=link.pk).exists()
    assert not can_view(member, investigation)
    assert can_view(inv_owner, investigation), "owner's baseline perm was revoked"


@pytest.mark.django_db
def test_adding_a_member_via_admin_grants_shared_investigations(admin_client_su):
    owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    investigation = InvestigationFactory.create(owner=owner)
    WorkspaceInvestigationFactory.create(
        workspace=workspace, investigation=investigation
    )
    joiner = PersonFactory.create()

    response = admin_client_su.post(
        reverse("admin:toxtempass_workspacemember_add"),
        {
            "workspace": workspace.pk,
            "user": joiner.pk,
            "role": WorkspaceRole.MEMBER,
        },
    )

    assert response.status_code == 302
    assert WorkspaceMember.objects.filter(workspace=workspace, user=joiner).exists()
    assert can_view(joiner, investigation)


@pytest.mark.django_db
def test_deleting_a_workspace_via_admin_revokes_derived_perms(admin_client_su):
    owner = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=owner)
    member = PersonFactory.create()
    WorkspaceMemberFactory.create(workspace=workspace, user=member)
    investigation = InvestigationFactory.create(owner=owner)
    WorkspaceInvestigationFactory.create(
        workspace=workspace, investigation=investigation
    )
    grant_investigation_to_members(workspace, investigation)

    response = admin_client_su.post(
        reverse("admin:toxtempass_workspace_delete", args=[workspace.pk]),
        {"post": "yes"},
    )

    assert response.status_code == 302
    assert not Workspace.objects.filter(pk=workspace.pk).exists()
    assert not can_view(member, investigation)
    assert can_view(owner, investigation), "owner keeps baseline access"
