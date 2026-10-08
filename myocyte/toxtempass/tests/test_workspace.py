"""Comprehensive tests for workspace CRUD, member management, and investigation sharing."""

import pytest
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from guardian.shortcuts import get_perms

from toxtempass.models import (
    Answer,
    Assay,
    Investigation,
    Person,
    Study,
    Workspace,
    WorkspaceInvestigation,
    WorkspaceInvitation,
    WorkspaceMember,
    WorkspaceRole,
)
from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionFactory,
    QuestionSetFactory,
    SectionFactory,
    StudyFactory,
    SubsectionFactory,
    WorkspaceFactory,
    WorkspaceInvestigationFactory,
    WorkspaceMemberFactory,
)


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db):
    return PersonFactory.create()


@pytest.fixture
def admin_user(db):
    return PersonFactory.create()


@pytest.fixture
def member_user(db):
    return PersonFactory.create()


@pytest.fixture
def outsider(db):
    return PersonFactory.create()


@pytest.fixture
def workspace(db, owner):
    return WorkspaceFactory.create(owner=owner)


@pytest.fixture
def investigation(db, owner):
    return InvestigationFactory.create(owner=owner)


@pytest.fixture
def other_investigation(db, outsider):
    return InvestigationFactory.create(owner=outsider)


# ---------------------------------------------------------------------------
# TestWorkspaceModel — direct DB tests, no HTTP
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceModel:
    def test_workspace_creation_sets_owner_membership(self, workspace, owner):
        assert WorkspaceMember.objects.filter(
            workspace=workspace, user=owner, role=WorkspaceRole.OWNER
        ).exists()

    def test_workspace_creation_exactly_one_member(self, workspace):
        assert workspace.memberships.count() == 1

    def test_workspace_str_returns_name(self, workspace):
        assert str(workspace) == workspace.name

    def test_workspace_member_unique_together_constraint(self, workspace, owner):
        with pytest.raises(IntegrityError):
            WorkspaceMember.objects.create(
                workspace=workspace, user=owner, role=WorkspaceRole.MEMBER
            )

    def test_workspace_investigation_unique_together_constraint(self, workspace, investigation):
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=investigation, added_by=workspace.owner
        )
        with pytest.raises(IntegrityError):
            WorkspaceInvestigation.objects.create(
                workspace=workspace, investigation=investigation, added_by=workspace.owner
            )

    def test_workspace_member_factory_creates_non_owner_member(self, workspace, member_user):
        wm = WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        assert WorkspaceMember.objects.filter(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        ).exists()
        assert wm.joined_at is not None

    def test_workspace_investigation_factory_sets_added_by_to_workspace_owner(
        self, workspace, investigation
    ):
        wi = WorkspaceInvestigationFactory.create(
            workspace=workspace, investigation=investigation
        )
        assert wi.added_by == workspace.owner


# ---------------------------------------------------------------------------
# TestCreateOrUpdateWorkspace
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateOrUpdateWorkspace:
    def test_create_workspace_happy_path(self, client, owner):
        client.force_login(owner)
        resp = client.post(
            reverse("create_workspace"),
            data={"name": "My Workspace", "description": "desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "workspace_id" in data
        ws = Workspace.objects.get(pk=data["workspace_id"])
        assert ws.owner == owner
        assert WorkspaceMember.objects.filter(
            workspace=ws, user=owner, role=WorkspaceRole.OWNER
        ).exists()

    def test_create_workspace_sets_current_user_as_owner(self, client, owner):
        client.force_login(owner)
        resp = client.post(
            reverse("create_workspace"),
            data={"name": "Owned WS", "description": ""},
        )
        ws_id = resp.json()["workspace_id"]
        assert Workspace.objects.get(pk=ws_id).owner == owner

    def test_create_workspace_missing_name_returns_errors(self, client, owner):
        client.force_login(owner)
        resp = client.post(
            reverse("create_workspace"),
            data={"name": "", "description": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["errors"]

    def test_create_workspace_unauthenticated_redirects(self, client):
        resp = client.post(
            reverse("create_workspace"),
            data={"name": "Test", "description": ""},
        )
        assert resp.status_code == 302

    def test_update_workspace_happy_path(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.post(
            reverse("update_workspace", kwargs={"pk": workspace.pk}),
            data={"name": "Updated Name", "description": "new desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        workspace.refresh_from_db()
        assert workspace.name == "Updated Name"

    def test_update_workspace_does_not_change_owner(self, client, owner, workspace):
        client.force_login(owner)
        client.post(
            reverse("update_workspace", kwargs={"pk": workspace.pk}),
            data={"name": "New Name", "description": ""},
        )
        workspace.refresh_from_db()
        assert workspace.owner == owner

    def test_update_workspace_by_non_owner_raises_403(self, client, outsider, workspace):
        client.force_login(outsider)
        resp = client.post(
            reverse("update_workspace", kwargs={"pk": workspace.pk}),
            data={"name": "Hack", "description": ""},
        )
        assert resp.status_code == 403

    def test_update_workspace_non_existent_pk_returns_404(self, client, owner):
        client.force_login(owner)
        resp = client.post(
            reverse("update_workspace", kwargs={"pk": 99999}),
            data={"name": "X", "description": ""},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestDeleteWorkspace
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteWorkspace:
    def test_delete_workspace_by_owner_succeeds(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))
        assert resp.status_code == 302
        assert not Workspace.objects.filter(pk=workspace.pk).exists()

    def test_delete_workspace_by_non_owner_raises_403(self, client, outsider, workspace):
        client.force_login(outsider)
        resp = client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))
        assert resp.status_code == 403

    def test_delete_workspace_unauthenticated_redirects(self, client, workspace):
        resp = client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))
        assert resp.status_code == 302

    def test_delete_workspace_non_existent_returns_404(self, client, owner):
        client.force_login(owner)
        resp = client.post(reverse("delete_workspace", kwargs={"pk": 99999}))
        assert resp.status_code == 404

    def test_delete_workspace_via_get_is_rejected(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.get(reverse("delete_workspace", kwargs={"pk": workspace.pk}))
        assert resp.status_code == 405
        assert Workspace.objects.filter(pk=workspace.pk).exists()

    def test_delete_workspace_revokes_member_guardian_perm(
        self, client, owner, workspace, investigation, member_user
    ):
        """Deleting the workspace revokes view_investigation for non-owner members."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        assert not Workspace.objects.filter(pk=workspace.pk).exists()
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

    def test_delete_workspace_member_cannot_view_assay_after_deletion(
        self, client, owner, workspace, investigation, member_user
    ):
        """After workspace deletion, ex-member is denied access to shared assays."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        client.force_login(member_user)
        resp = client.get(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk})
        )
        assert resp.status_code == 403

    def test_delete_workspace_preserves_perm_if_member_has_other_workspace(
        self, client, owner, member_user, investigation
    ):
        """Member retains view_investigation if another workspace still shares the same investigation."""
        workspace_a = WorkspaceFactory.create(owner=owner)
        workspace_b = WorkspaceFactory.create(owner=owner)

        _give_member_access(client, workspace_a, investigation, owner, member_user)
        _give_member_access(client, workspace_b, investigation, owner, member_user)

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        # Delete workspace_a — member still has access via workspace_b
        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace_a.pk}))

        assert not Workspace.objects.filter(pk=workspace_a.pk).exists()
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

    def test_delete_workspace_owner_retains_own_investigation_access(
        self, client, owner, workspace, investigation
    ):
        """Deleting the workspace does not revoke the workspace owner's baseline perm on their own investigation."""
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=investigation, added_by=owner
        )

        # investigation is owned by owner, so Investigation.save() already set the perm.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=owner.pk), investigation
        )

        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        # The investigation itself and the owner's baseline perm must be intact.
        assert Investigation.objects.filter(pk=investigation.pk).exists()
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=owner.pk), investigation
        )

    def test_delete_workspace_revokes_workspace_owner_perm_for_member_investigation(
        self, client, owner, workspace, admin_user
    ):
        """Workspace owner loses workspace-derived view perm when the workspace is deleted.

        When another member shares *their own* investigation into the workspace,
        the workspace owner also receives ``view_investigation`` via
        ``add_workspace_assay``.  Deleting the workspace must remove that derived
        perm from the workspace owner while preserving the investigation owner's
        baseline access.
        """
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        # Use the real view so all members (including workspace owner) receive
        # view_investigation exactly as production does.
        client.force_login(admin_user)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": owned_investigation.pk},
        )

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=owner.pk), owned_investigation
        )
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        assert not Workspace.objects.filter(pk=workspace.pk).exists()
        # Workspace owner loses the workspace-derived perm.
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=owner.pk), owned_investigation
        )
        # Investigation owner retains their baseline perm.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

    def test_delete_workspace_with_admin_member_revokes_admin_perm(
        self, client, owner, workspace, investigation, admin_user
    ):
        """ADMIN-role members also lose view_investigation when the workspace is deleted."""
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        # Use the real view to share the investigation — grants perm to all members.
        client.force_login(owner)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), investigation
        )

        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        assert not Workspace.objects.filter(pk=workspace.pk).exists()
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=admin_user.pk), investigation
        )

    def test_delete_workspace_preserves_perm_for_member_who_owns_investigation(
        self, client, owner, workspace, admin_user
    ):
        """Deleting a workspace must not revoke the view perm of a member who owns the shared investigation.

        A workspace member (with ADMIN role here, but the same applies to any
        non-owner role) can share *their own* investigation into the workspace.
        When the workspace is later deleted the investigation owner must retain
        their baseline ``view_investigation`` permission — it was granted by
        ``Investigation.save()`` and is not workspace-derived.
        """
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        WorkspaceInvestigation.objects.create(
            workspace=workspace,
            investigation=owned_investigation,
            added_by=admin_user,
        )

        # Investigation.save() already assigned the perm; confirm it exists.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

        client.force_login(owner)
        client.post(reverse("delete_workspace", kwargs={"pk": workspace.pk}))

        assert not Workspace.objects.filter(pk=workspace.pk).exists()
        assert Investigation.objects.filter(pk=owned_investigation.pk).exists()
        # Owner must still be able to see their own investigation.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )


# ---------------------------------------------------------------------------
# TestDeleteEndpointsRejectGet — regression for the GET-prefetch CSRF hazard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteEndpointsRejectGet:
    """All four delete endpoints must reject GET so prefetchers / link
    scanners / cross-site image tags cannot trigger deletes by URL alone.
    """

    def test_delete_investigation_via_get_is_rejected(
        self, client, owner, investigation
    ):
        client.force_login(owner)
        resp = client.get(
            reverse("delete_investigation", kwargs={"pk": investigation.pk})
        )
        assert resp.status_code == 405
        assert Investigation.objects.filter(pk=investigation.pk).exists()

    def test_delete_study_via_get_is_rejected(self, client, owner, investigation):
        study = StudyFactory.create(investigation=investigation, created_by=owner)
        client.force_login(owner)
        resp = client.get(reverse("delete_study", kwargs={"pk": study.pk}))
        assert resp.status_code == 405
        assert Study.objects.filter(pk=study.pk).exists()

    def test_delete_assay_via_get_is_rejected(self, client, owner, investigation):
        study = StudyFactory.create(investigation=investigation, created_by=owner)
        assay = AssayFactory.create(study=study, created_by=owner)
        client.force_login(owner)
        resp = client.get(reverse("delete_assay", kwargs={"pk": assay.pk}))
        assert resp.status_code == 405
        assert Assay.objects.filter(pk=assay.pk).exists()


# ---------------------------------------------------------------------------
# TestAddWorkspaceMember
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddWorkspaceMember:
    def test_owner_can_add_member_by_id(self, client, owner, workspace, member_user):
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Added means invited: no membership, and so no access, until accepted.
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=member_user
        ).exists()
        assert WorkspaceInvitation.objects.filter(
            workspace=workspace, user=member_user
        ).exists()

    def test_admin_can_add_member_by_id(self, client, workspace, admin_user, member_user):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        client.force_login(admin_user)
        resp = client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_member_cannot_add_member_by_id(self, client, workspace, member_user, outsider):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(member_user)
        resp = client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": outsider.pk, "role": WorkspaceRole.MEMBER},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_outsider_cannot_add_member_by_id(self, client, workspace, outsider, member_user):
        client.force_login(outsider)
        resp = client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_add_duplicate_member_by_id_returns_400(self, client, owner, workspace, member_user):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_add_member_grants_view_perm_for_existing_shared_investigations(
        self, client, owner, workspace, investigation, member_user
    ):
        # Pre-share investigation into the workspace directly (no perm propagation yet)
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=investigation, added_by=owner
        )
        client.force_login(owner)
        client.post(
            reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
            data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
        )
        # Invited only: nothing is granted yet.
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )
        invitation = WorkspaceInvitation.objects.get(
            workspace=workspace, user=member_user
        )
        client.force_login(member_user)
        client.post(
            reverse("respond_workspace_invitation", args=[invitation.pk]),
            {"action": "accept"},
        )
        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh, investigation)

    def test_add_member_by_email_happy_path(self, client, owner, workspace, member_user):
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": member_user.email},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert WorkspaceInvitation.objects.filter(
            workspace=workspace, user=member_user
        ).exists()

    def test_add_member_by_email_nonexistent_email_returns_404(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": "nobody@nowhere.example.com"},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# TestRemoveWorkspaceMember
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveWorkspaceMember:
    def test_owner_can_remove_member_by_id(self, client, owner, workspace, member_user):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(owner)
        resp = client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert not WorkspaceMember.objects.filter(workspace=workspace, user=member_user).exists()

    def test_admin_can_remove_member_by_id(
        self, client, workspace, admin_user, member_user
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(admin_user)
        resp = client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_member_cannot_remove_member_by_id(
        self, client, workspace, member_user, outsider
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        WorkspaceMemberFactory.create(
            workspace=workspace, user=outsider, role=WorkspaceRole.MEMBER
        )
        client.force_login(member_user)
        resp = client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": outsider.pk},
            )
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_cannot_remove_owner_by_id(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": owner.pk},
            )
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "owner" in data["error"].lower()

    def test_remove_member_revokes_view_perm(
        self, client, owner, workspace, investigation, member_user
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        # Share investigation via the view so guardian perms are granted
        client.force_login(owner)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        # Confirm perm was granted
        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh, investigation)

        # Now remove the member
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )
        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" not in get_perms(fresh, investigation)

    def test_member_can_remove_themselves_by_email(
        self, client, workspace, member_user
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(member_user)
        resp = client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": member_user.email},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=member_user
        ).exists()

    def test_owner_cannot_remove_themselves_by_email(self, client, owner, workspace):
        client.force_login(owner)
        resp = client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": owner.email},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "owner" in data["error"].lower()

    def test_remove_by_email_non_member_returns_404(
        self, client, owner, workspace, outsider
    ):
        client.force_login(owner)
        resp = client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": outsider.email},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_remove_member_preserves_perm_for_investigation_owner(
        self, client, owner, workspace, admin_user
    ):
        """Removing a member who owns a shared investigation must not revoke their baseline perm.

        Only the owner of an investigation can add it to a workspace.  If that
        member is later removed from the workspace their ``view_investigation``
        perm must be preserved because it was set by ``Investigation.save()``,
        not by the workspace sharing operation.
        """
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        # Use the real view so all members receive view_investigation as production does.
        client.force_login(admin_user)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": owned_investigation.pk},
        )

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": admin_user.pk},
            )
        )

        # Membership removed but baseline perm must remain.
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=admin_user
        ).exists()
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

    def test_remove_member_by_email_preserves_perm_for_investigation_owner(
        self, client, owner, workspace, admin_user
    ):
        """Removing a member by email who owns a shared investigation must not revoke their baseline perm."""
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        # Use the real view so perms are granted exactly as production does.
        client.force_login(admin_user)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": owned_investigation.pk},
        )

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

        client.force_login(owner)
        client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": admin_user.email},
        )

        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=admin_user
        ).exists()
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )


# ---------------------------------------------------------------------------
# TestWorkspaceInvestigationSharing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceInvestigationSharing:
    def test_owner_can_add_investigation(
        self, client, owner, workspace, investigation
    ):
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "workspace_investigation_id" in data
        assert WorkspaceInvestigation.objects.filter(
            workspace=workspace, investigation=investigation
        ).exists()

    def test_member_cannot_add_investigation(
        self, client, workspace, member_user, investigation
    ):
        # member_user owns investigation but only has MEMBER role
        inv = InvestigationFactory.create(owner=member_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(member_user)
        resp = client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": inv.pk},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_non_owner_of_investigation_cannot_add_it(
        self, client, owner, workspace, other_investigation
    ):
        client.force_login(owner)
        resp = client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": other_investigation.pk},
        )
        assert resp.status_code == 403
        assert resp.json()["success"] is False

    def test_add_investigation_grants_view_perm_to_all_members(
        self, client, owner, workspace, investigation, admin_user, member_user
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(owner)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        fresh_admin = Person.objects.get(pk=admin_user.pk)
        fresh_member = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh_admin, investigation)
        assert "view_investigation" in get_perms(fresh_member, investigation)

    def test_add_duplicate_investigation_returns_400(
        self, client, owner, workspace, investigation
    ):
        client.force_login(owner)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        resp = client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_owner_can_remove_investigation(
        self, client, owner, workspace, investigation
    ):
        client.force_login(owner)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        resp = client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace.pk, "assay_id": investigation.pk},
            )
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert not WorkspaceInvestigation.objects.filter(
            workspace=workspace, investigation=investigation
        ).exists()

    def test_remove_investigation_revokes_view_perm_from_members(
        self, client, owner, workspace, investigation, member_user
    ):
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        client.force_login(owner)
        # Add investigation — grants perm
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": investigation.pk},
        )
        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh, investigation)

        # Remove investigation — revokes perm
        client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace.pk, "assay_id": investigation.pk},
            )
        )
        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" not in get_perms(fresh, investigation)

    def test_remove_nonexistent_investigation_returns_404(
        self, client, owner, workspace
    ):
        client.force_login(owner)
        resp = client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace.pk, "assay_id": 99999},
            )
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_remove_investigation_preserves_investigation_owner_perm(
        self, client, owner, workspace, admin_user
    ):
        """Removing an investigation from a workspace must not revoke the investigation owner's baseline perm.

        Only the owner can share an investigation into a workspace, so when the
        investigation is removed, the investigation owner's ``view_investigation``
        perm (set by ``Investigation.save()``) must be preserved.
        """
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        # Add investigation through the view so all members receive the perm.
        client.force_login(admin_user)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": owned_investigation.pk},
        )

        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

        # Workspace owner removes the investigation.
        client.force_login(owner)
        resp = client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace.pk, "assay_id": owned_investigation.pk},
            )
        )
        assert resp.status_code == 200
        assert not WorkspaceInvestigation.objects.filter(
            workspace=workspace, investigation=owned_investigation
        ).exists()
        # Investigation owner must still have their baseline perm.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )

    def test_remove_investigation_revokes_non_owner_member_perm(
        self, client, owner, workspace, admin_user, member_user
    ):
        """Removing an investigation revokes access for all workspace members who don't own it."""
        owned_investigation = InvestigationFactory.create(owner=admin_user)
        WorkspaceMemberFactory.create(
            workspace=workspace, user=admin_user, role=WorkspaceRole.ADMIN
        )
        WorkspaceMemberFactory.create(
            workspace=workspace, user=member_user, role=WorkspaceRole.MEMBER
        )
        # Add investigation — grants perm to all members including workspace owner.
        client.force_login(admin_user)
        client.post(
            reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
            data={"investigation": owned_investigation.pk},
        )
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=owner.pk), owned_investigation
        )
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), owned_investigation
        )

        # Remove investigation — workspace owner revokes it.
        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace.pk, "assay_id": owned_investigation.pk},
            )
        )
        # Workspace owner and regular member lose access.
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=owner.pk), owned_investigation
        )
        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=member_user.pk), owned_investigation
        )
        # Investigation owner retains baseline access.
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=admin_user.pk), owned_investigation
        )


# ---------------------------------------------------------------------------
# Shared helper: set up a workspace member with guardian perm on investigation
# ---------------------------------------------------------------------------


def _give_member_access(client, workspace, investigation, owner, member_user):
    """Grant workspace member access via the real add_workspace_assay / add_workspace_member views.

    Logs in as ``owner`` (the investigation owner and workspace OWNER/ADMIN)
    to perform both operations, which is the only path that grants guardian
    ``view_investigation`` perms in production.  Both calls are idempotent —
    a 400 response (already shared / already a member) is silently ignored.
    """
    client.force_login(owner)
    client.post(
        reverse("add_workspace_assay", kwargs={"pk": workspace.pk}),
        data={"investigation": investigation.pk},
    )
    client.post(
        reverse("add_workspace_member", kwargs={"pk": workspace.pk}),
        data={"user": member_user.pk, "role": WorkspaceRole.MEMBER},
    )
    # Nobody joins by being added: the invited person has to accept.
    invitation = WorkspaceInvitation.objects.filter(
        workspace=workspace, user=member_user
    ).first()
    if invitation is not None:
        client.force_login(member_user)
        client.post(
            reverse("respond_workspace_invitation", args=[invitation.pk]),
            {"action": "accept"},
        )
        client.force_login(owner)


# ---------------------------------------------------------------------------
# TestWorkspaceMemberAccess
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceMemberAccess:
    """What workspace MEMBERs and ADMINs can and cannot do with ISA data."""

    def test_member_can_view_shared_assay(
        self, client, owner, workspace, investigation, member_user
    ):
        """MEMBER can submit the answer form for an assay in a shared investigation.

        We POST (returns JSON) rather than GET (renders a template requiring static assets
        that are not present in the test environment).
        """
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(member_user)
        resp = client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_outsider_cannot_view_assay_in_shared_investigation(
        self, client, outsider, investigation
    ):
        """Non-member is denied access to an assay (403)."""
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(outsider)
        resp = client.get(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk})
        )
        assert resp.status_code == 403

    def test_member_can_create_study_in_shared_investigation(
        self, client, owner, workspace, investigation, member_user
    ):
        """MEMBER can POST to create_study inside a shared investigation."""
        _give_member_access(client, workspace, investigation, owner, member_user)

        client.force_login(member_user)
        resp = client.post(
            reverse("create_study"),
            data={
                "investigation": investigation.pk,
                "title": "Member Study",
                "description": "A study created by a member.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert Study.objects.filter(
            investigation=investigation, title="Member Study"
        ).exists()

    def test_outsider_cannot_create_study_in_shared_investigation(
        self, client, outsider, investigation
    ):
        """Non-member's investigation is not in queryset → form invalid → success=False."""
        client.force_login(outsider)
        resp = client.post(
            reverse("create_study"),
            data={
                "investigation": investigation.pk,
                "title": "Outsider Study",
                "description": "",
            },
        )
        assert resp.json()["success"] is False

    def test_member_cannot_edit_existing_study(
        self, client, owner, workspace, investigation, member_user
    ):
        """Study.is_accessible_by(change) has no workspace override → 403 for members."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)

        client.force_login(member_user)
        resp = client.post(
            reverse("update_study", kwargs={"pk": study.pk}),
            data={
                "investigation": investigation.pk,
                "title": "Hacked",
                "description": "",
            },
        )
        assert resp.status_code == 403

    def test_member_cannot_delete_study(
        self, client, owner, workspace, investigation, member_user
    ):
        """Study.is_accessible_by(delete) also has no workspace override → 403."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)

        client.force_login(member_user)
        resp = client.post(reverse("delete_study", kwargs={"pk": study.pk}))
        assert resp.status_code == 403

    def test_member_can_edit_assay_via_workspace_check(
        self, client, owner, workspace, investigation, member_user
    ):
        """Assay.is_accessible_by(change) has workspace override → members CAN edit assays."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(member_user)
        resp = client.post(
            reverse("update_assay", kwargs={"pk": assay.pk}),
            data={
                "study": study.pk,
                "title": "Updated by Member",
                "description": "Updated description.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assay.refresh_from_db()
        assert assay.title == "Updated by Member"

    def test_member_can_save_answers(
        self, client, owner, workspace, investigation, member_user
    ):
        """MEMBER can POST answers on a shared assay (no question_set → form valid with no fields)."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)  # no question_set → no question fields

        client.force_login(member_user)
        resp = client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_admin_can_view_assay(
        self, client, owner, workspace, investigation, admin_user
    ):
        """ADMIN has identical data-access as MEMBER (role only governs workspace management)."""
        _give_member_access(client, workspace, investigation, owner, admin_user)
        # Promote to ADMIN
        WorkspaceMember.objects.filter(
            workspace=workspace, user=admin_user
        ).update(role=WorkspaceRole.ADMIN)

        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(admin_user)
        # POST (returns JSON) instead of GET (would require static template assets in CI)
        resp = client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_admin_can_edit_assay(
        self, client, owner, workspace, investigation, admin_user
    ):
        """ADMIN can edit an assay (workspace override in Assay.is_accessible_by)."""
        _give_member_access(client, workspace, investigation, owner, admin_user)
        WorkspaceMember.objects.filter(
            workspace=workspace, user=admin_user
        ).update(role=WorkspaceRole.ADMIN)

        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        client.force_login(admin_user)
        resp = client.post(
            reverse("update_assay", kwargs={"pk": assay.pk}),
            data={"study": study.pk, "title": "Admin Edit", "description": "desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_admin_cannot_edit_existing_study_either(
        self, client, owner, workspace, investigation, admin_user
    ):
        """ADMIN also cannot edit existing studies (same as MEMBER — no workspace override)."""
        _give_member_access(client, workspace, investigation, owner, admin_user)
        WorkspaceMember.objects.filter(
            workspace=workspace, user=admin_user
        ).update(role=WorkspaceRole.ADMIN)
        study = StudyFactory.create(investigation=investigation)

        client.force_login(admin_user)
        resp = client.post(
            reverse("update_study", kwargs={"pk": study.pk}),
            data={"investigation": investigation.pk, "title": "Hacked", "description": ""},
        )
        assert resp.status_code == 403

    def test_member_cannot_delete_assay_created_by_owner(
        self, client, owner, workspace, investigation, member_user
    ):
        """MEMBER must not delete an assay they did not create (created by the investigation owner)."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation, created_by=owner)
        assay = AssayFactory.create(study=study, created_by=owner)

        client.force_login(member_user)
        resp = client.post(reverse("delete_assay", kwargs={"pk": assay.pk}))
        assert resp.status_code == 403
        assert Assay.objects.filter(pk=assay.pk).exists()

    def test_member_can_delete_assay_they_created(
        self, client, owner, workspace, investigation, member_user
    ):
        """MEMBER CAN delete an assay they created themselves."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation, created_by=owner)
        assay = AssayFactory.create(study=study, created_by=member_user)

        client.force_login(member_user)
        resp = client.post(reverse("delete_assay", kwargs={"pk": assay.pk}))
        # Expect redirect (success) — assay is gone
        assert resp.status_code == 302
        assert not Assay.objects.filter(pk=assay.pk).exists()

    def test_investigation_owner_can_delete_member_created_assay(
        self, client, owner, workspace, investigation, member_user
    ):
        """Investigation owner can always delete any assay regardless of who created it."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation, created_by=owner)
        assay = AssayFactory.create(study=study, created_by=member_user)

        client.force_login(owner)
        resp = client.post(reverse("delete_assay", kwargs={"pk": assay.pk}))
        assert resp.status_code == 302
        assert not Assay.objects.filter(pk=assay.pk).exists()


# ---------------------------------------------------------------------------
# TestWorkspaceAccessRevocation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceAccessRevocation:
    """After removal from a workspace, all access is revoked — no lingering state."""

    def test_removed_member_loses_guardian_view_perm(
        self, client, owner, workspace, investigation, member_user
    ):
        """Guardian view_investigation perm is explicitly revoked on member removal."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

    def test_removed_member_cannot_view_assay(
        self, client, owner, workspace, investigation, member_user
    ):
        """After removal, ex-member is denied assay access (both guardian and workspace paths gone)."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)
        assay = AssayFactory.create(study=study)

        # Remove member
        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        # Access denied after removal
        client.force_login(member_user)
        resp = client.get(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk})
        )
        assert resp.status_code == 403

    def test_removed_member_workspace_record_deleted(
        self, client, owner, workspace, investigation, member_user
    ):
        """WorkspaceMember record is gone after removal — no half-state left in DB."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        assert WorkspaceMember.objects.filter(
            workspace=workspace, user=member_user
        ).exists()

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        assert not WorkspaceMember.objects.filter(
            workspace=workspace, user=member_user
        ).exists()

    def test_removed_member_cannot_create_study(
        self, client, owner, workspace, investigation, member_user
    ):
        """After removal, ex-member's investigation queryset is empty → study creation fails."""
        _give_member_access(client, workspace, investigation, owner, member_user)

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        client.force_login(member_user)
        resp = client.post(
            reverse("create_study"),
            data={
                "investigation": investigation.pk,
                "title": "Ghost Study",
                "description": "",
            },
        )
        assert resp.json()["success"] is False

    def test_study_created_by_removed_member_still_exists(
        self, client, owner, workspace, investigation, member_user
    ):
        """Studies created by a removed member are NOT deleted — they remain under the investigation."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation, created_by=member_user)
        study_pk = study.pk

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        # Study still exists and belongs to the investigation
        assert Study.objects.filter(pk=study_pk).exists()
        assert Study.objects.get(pk=study_pk).investigation == investigation

    def test_removed_via_email_also_revokes_guardian_perm(
        self, client, owner, workspace, investigation, member_user
    ):
        """Removing by email (self-removal path) also revokes the guardian perm."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        # Member removes themselves by email
        client.force_login(member_user)
        client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace.pk}),
            data={"email": member_user.email},
        )

        assert "view_investigation" not in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )


# ---------------------------------------------------------------------------
# TestCrossCreatedContent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossCreatedContent:
    """Studies and assays created by workspace members inside shared investigations."""

    def test_member_created_study_has_member_as_created_by(
        self, client, owner, workspace, investigation, member_user
    ):
        """created_by on a member-created study is the member — not the investigation owner."""
        _give_member_access(client, workspace, investigation, owner, member_user)

        client.force_login(member_user)
        client.post(
            reverse("create_study"),
            data={
                "investigation": investigation.pk,
                "title": "Cross Study",
                "description": "desc",
            },
        )

        study = Study.objects.get(investigation=investigation, title="Cross Study")
        assert study.created_by == member_user
        assert study.investigation.owner == owner  # investigation owner unchanged

    def test_member_created_assay_has_member_as_created_by(
        self, client, owner, workspace, investigation, member_user
    ):
        """created_by on a member-created assay is the member."""
        _give_member_access(client, workspace, investigation, owner, member_user)
        study = StudyFactory.create(investigation=investigation)

        client.force_login(member_user)
        client.post(
            reverse("create_assay"),
            data={
                "study": study.pk,
                "title": "Cross Assay",
                "description": "Assay description with enough detail.",
            },
        )

        assay = Assay.objects.get(study=study, title="Cross Assay")
        assert assay.created_by == member_user

    def test_investigation_owner_can_edit_member_created_study(
        self, client, owner, investigation, member_user
    ):
        """Investigation owner can edit a study created by a workspace member."""
        study = StudyFactory.create(investigation=investigation, created_by=member_user)

        client.force_login(owner)
        resp = client.post(
            reverse("update_study", kwargs={"pk": study.pk}),
            data={
                "investigation": investigation.pk,
                "title": "Owner Edited",
                "description": "desc",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        study.refresh_from_db()
        assert study.title == "Owner Edited"

    def test_member_created_study_not_accessible_after_removal(
        self, client, owner, workspace, investigation, member_user
    ):
        """After removal, member cannot edit the study they created (no lingering change perm)."""
        _give_member_access(client, workspace, investigation, owner, member_user)

        # Member creates study
        client.force_login(member_user)
        client.post(
            reverse("create_study"),
            data={
                "investigation": investigation.pk,
                "title": "My Study",
                "description": "desc",
            },
        )
        study = Study.objects.get(investigation=investigation, title="My Study")

        # Owner removes member
        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace.pk, "user_id": member_user.pk},
            )
        )

        # Ex-member tries to edit the study they created — denied
        client.force_login(member_user)
        resp = client.post(
            reverse("update_study", kwargs={"pk": study.pk}),
            data={
                "investigation": investigation.pk,
                "title": "Still Mine",
                "description": "",
            },
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Fixture: minimal question hierarchy for answer/history tests
# ---------------------------------------------------------------------------


@pytest.fixture
def question_setup(db, owner):
    """Return (assay, question) with a full QuestionSet hierarchy under owner's investigation."""
    # label=None avoids unique constraint clashes with pre-existing rows (e.g. 'v1' from migrations)
    qs = QuestionSetFactory.create(label=None)
    section = SectionFactory.create(question_set=qs)
    subsection = SubsectionFactory.create(section=section)
    question = QuestionFactory.create(subsection=subsection)
    investigation = InvestigationFactory.create(owner=owner)
    study = StudyFactory.create(investigation=investigation)
    assay = AssayFactory.create(study=study, question_set=qs)
    return assay, question


# ---------------------------------------------------------------------------
# TestCollaborationLastWriteWins
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollaborationLastWriteWins:
    """Answer collaboration: last write wins, with full history preserved via django-simple-history."""

    def test_second_save_overwrites_first_answer(
        self, client, owner, workspace, member_user, question_setup
    ):
        """When two users save the same question's answer, the last writer's text wins."""
        assay, question = question_setup
        investigation = assay.study.investigation
        _give_member_access(client, workspace, investigation, owner, member_user)

        # Owner saves first
        client.force_login(owner)
        resp = client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Owner's answer"},
        )
        assert resp.json()["success"] is True

        # Member saves second (different text → overwrites)
        client.force_login(member_user)
        resp = client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Member's overwrite"},
        )
        assert resp.json()["success"] is True

        answer = Answer.objects.get(assay=assay, question=question)
        assert answer.answer_text == "Member's overwrite"

    def test_overwritten_answer_preserved_in_history(
        self, client, owner, workspace, member_user, question_setup
    ):
        """The original answer text is retrievable from django-simple-history after being overwritten."""
        assay, question = question_setup
        investigation = assay.study.investigation
        _give_member_access(client, workspace, investigation, owner, member_user)

        client.force_login(owner)
        client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Original answer"},
        )

        client.force_login(member_user)
        client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Overwritten answer"},
        )

        answer = Answer.objects.get(assay=assay, question=question)
        assert answer.answer_text == "Overwritten answer"

        history_texts = list(
            answer.history.order_by("history_date").values_list("answer_text", flat=True)
        )
        assert "Original answer" in history_texts
        assert "Overwritten answer" in history_texts

    def test_same_text_save_does_not_create_history_entry(
        self, client, owner, workspace, member_user, question_setup
    ):
        """Re-saving with identical text does NOT add a new history record (no-op in forms.py:658)."""
        assay, question = question_setup
        investigation = assay.study.investigation
        _give_member_access(client, workspace, investigation, owner, member_user)

        client.force_login(owner)
        client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Unchanged text"},
        )
        answer = Answer.objects.get(assay=assay, question=question)
        count_after_first = answer.history.count()

        # Save again with same text → no new history record
        client.post(
            reverse("answer_assay_questions", kwargs={"assay_id": assay.pk}),
            data={f"question_{question.pk}": "Unchanged text"},
        )
        answer.refresh_from_db()
        assert answer.history.count() == count_after_first

    def test_workspace_member_can_view_version_history_page(
        self, client, owner, workspace, member_user, question_setup
    ):
        """Workspace member with view access can open the version history page for an answer."""
        assay, question = question_setup
        investigation = assay.study.investigation
        _give_member_access(client, workspace, investigation, owner, member_user)

        # Create an initial answer so the view doesn't 404
        Answer.objects.create(assay=assay, question=question, answer_text="Initial text")

        client.force_login(member_user)
        resp = client.get(
            reverse(
                "get_version_history",
                kwargs={"assay_id": assay.pk, "question_id": question.pk},
            )
        )
        assert resp.status_code == 200

    def test_outsider_cannot_view_version_history(
        self, client, outsider, question_setup
    ):
        """Non-member is denied access to the version history page (403)."""
        assay, question = question_setup
        Answer.objects.create(assay=assay, question=question, answer_text="text")

        client.force_login(outsider)
        resp = client.get(
            reverse(
                "get_version_history",
                kwargs={"assay_id": assay.pk, "question_id": question.pk},
            )
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMultiWorkspacePermOverlap:
    """Tests for the multi-workspace investigation overlap bug.

    When a user is a member of two workspaces (A and B) that both share the same
    investigation, removing them from workspace A should NOT revoke their
    view_investigation guardian perm — they still have access via workspace B.
    """

    def test_removing_from_one_workspace_preserves_access_via_other_workspace(
        self, client, owner, member_user, investigation
    ):
        """Bug: blind perm revocation removes access even when another workspace grants it."""
        workspace_a = WorkspaceFactory.create(owner=owner)
        workspace_b = WorkspaceFactory.create(owner=owner)

        _give_member_access(client, workspace_a, investigation, owner, member_user)
        _give_member_access(client, workspace_b, investigation, owner, member_user)

        # Confirm perm exists before removal
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        client.force_login(owner)
        resp = client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace_a.pk, "user_id": member_user.pk},
            )
        )
        assert resp.status_code == 200

        fresh = Person.objects.get(pk=member_user.pk)
        # Should still have perm — user is still a member of workspace_b which shares the same investigation
        assert "view_investigation" in get_perms(fresh, investigation), (
            "Bug: view_investigation was revoked even though user still has access via workspace_b"
        )

    def test_removing_from_last_workspace_revokes_perm(
        self, client, owner, member_user, investigation
    ):
        """Sanity check: removing from the only shared workspace DOES revoke the perm."""
        workspace_a = WorkspaceFactory.create(owner=owner)

        _give_member_access(client, workspace_a, investigation, owner, member_user)
        assert "view_investigation" in get_perms(
            Person.objects.get(pk=member_user.pk), investigation
        )

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_member",
                kwargs={"pk": workspace_a.pk, "user_id": member_user.pk},
            )
        )

        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" not in get_perms(fresh, investigation)

    def test_removing_member_by_email_preserves_access_via_other_workspace(
        self, client, owner, member_user, investigation
    ):
        """Same overlap bug via the remove-by-email endpoint."""
        workspace_a = WorkspaceFactory.create(owner=owner)
        workspace_b = WorkspaceFactory.create(owner=owner)

        _give_member_access(client, workspace_a, investigation, owner, member_user)
        _give_member_access(client, workspace_b, investigation, owner, member_user)

        client.force_login(owner)
        client.post(
            reverse("remove_workspace_member_by_email", kwargs={"pk": workspace_a.pk}),
            data={"email": member_user.email},
        )

        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh, investigation), (
            "Bug: view_investigation was revoked even though user still has access via workspace_b"
        )

    def test_removing_investigation_from_one_workspace_preserves_member_access_via_other_workspace(
        self, client, owner, member_user, investigation
    ):
        """Removing an investigation from workspace A should not revoke perm if workspace B also shares it."""
        workspace_a = WorkspaceFactory.create(owner=owner)
        workspace_b = WorkspaceFactory.create(owner=owner)

        _give_member_access(client, workspace_a, investigation, owner, member_user)
        _give_member_access(client, workspace_b, investigation, owner, member_user)

        client.force_login(owner)
        client.post(
            reverse(
                "remove_workspace_assay",
                kwargs={"pk": workspace_a.pk, "assay_id": investigation.pk},
            )
        )

        fresh = Person.objects.get(pk=member_user.pk)
        assert "view_investigation" in get_perms(fresh, investigation), (
            "Bug: view_investigation was revoked even though workspace_b still shares the investigation"
        )
