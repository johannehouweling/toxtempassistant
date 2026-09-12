"""Workspace view functions.

Standalone AJAX/form endpoints for workspace CRUD, member management, and
investigation sharing.  These were extracted from views.py to keep that file
manageable; none of the functions here are interleaved with non-workspace view
logic.
"""

import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponseRedirect
from django.http.response import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from guardian.shortcuts import assign_perm, remove_perm

from toxtempass.forms import (
    WorkspaceForm,
    WorkspaceInvestigationForm,
    WorkspaceMemberForm,
)
from toxtempass.models import (
    Investigation,
    Person,
    Workspace,
    WorkspaceInvestigation,
    WorkspaceMember,
    WorkspaceRole,
)
from toxtempass.workspace_perms import revoke_investigation_from_members

logger = logging.getLogger(__name__)


@login_required(login_url="/login/")
def get_workspace_list(request: HttpRequest) -> dict:
    """List all workspaces the user is a member of."""
    # memberships holds WorkspaceMember rows for the current user so we can
    # present workspaces the user belongs to even when they are not the owner.
    memberships = WorkspaceMember.objects.filter(user=request.user).select_related(
        "workspace", "workspace__owner"
    )

    # Workspaces owned by the user
    owned_workspaces = list(
        Workspace.objects.filter(owner=request.user).order_by("-created_at")
    )
    # Ensure owned workspaces have a role attribute so template logic can be unified
    for ws in owned_workspaces:
        setattr(ws, "current_user_role", WorkspaceRole.OWNER)

    # Workspaces where the user is a member (but not the owner)
    member_workspaces = []
    for m in memberships:
        ws = m.workspace
        # skip owned ones (already included)
        if ws.owner_id == request.user.id:
            continue
        # attach the current user's role for template checks (owner/admin/member)
        setattr(ws, "current_user_role", m.role)
        member_workspaces.append(ws)

    # Only show investigations owned by the current user in the Add modal to avoid allowing
    # users to share investigations they do not own via the UI. Server-side check will also enforce ownership.
    owned_investigations = Investigation.objects.filter(owner=request.user)

    return {
        "owned_workspaces": owned_workspaces,
        "member_workspaces": member_workspaces,
        "accessible_investigations": owned_investigations,
    }


@login_required(login_url="/login/")
def create_or_update_workspace(
    request: HttpRequest, pk: int | None = None
) -> JsonResponse:
    """Create or update a Workspace."""
    if pk:
        workspace = get_object_or_404(Workspace, pk=pk)
        if workspace.owner != request.user:
            raise PermissionDenied("You do not own this workspace.")
    else:
        workspace = None

    if request.method == "POST":
        form = WorkspaceForm(request.POST, instance=workspace)
        if form.is_valid():
            grp = form.save(commit=False)
            if not workspace:  # only set owner on creation, not update
                grp.owner = request.user
                grp.save()
            else:
                grp.save()
            # Return redirect_url containing the workspace id so client-side JS
            # can extract the created/updated workspace primary key and
            # update the DOM without a full page reload. The JS expects a
            # URL matching /workspace/<pk>/ so return that form (it does not
            # need to be a real routable view — it's only used to extract pk).
            redirect_url = f"/workspace/{grp.pk}/"
            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": redirect_url,
                    "workspace_id": grp.pk,
                }
            )
        else:
            return JsonResponse({"success": False, "errors": form.errors})
    else:
        return JsonResponse(
            {"success": False, "error": "POST request required"}, status=405
        )


@login_required(login_url="/login/")
@require_POST
def delete_workspace(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Delete a workspace if the user is the owner.

    POST-only and CSRF-protected: GET would be triggerable by browser
    prefetchers, link scanners, or cross-site image/link tags.

    Before deleting the workspace we explicitly revoke the guardian
    ``view_investigation`` permissions that were granted to workspace members
    (including the workspace owner) via this workspace.  The CASCADE delete of
    WorkspaceMember / WorkspaceInvestigation rows removes the DB records but
    does **not** clean up guardian's object-level permission rows — without
    this step, ex-members would retain read access to the shared
    investigations.

    Two exceptions apply when deciding whether to revoke a perm:

    1. The member is the *owner* of that investigation — their
       ``view_investigation`` perm was granted by ``Investigation.save()`` as
       baseline access and is not workspace-derived.
    2. The member still has the same investigation shared through a *different*
       workspace they belong to.

    The whole revoke+delete block runs inside a single DB transaction so a
    partial failure cannot leave permissions inconsistent with the workspace
    membership state.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    if workspace.owner != request.user:
        raise PermissionDenied("You do not own this workspace.")

    with transaction.atomic():
        # Lock the workspace row first so delete is serialized against
        # concurrent membership / sharing changes for this workspace.
        workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)

        # Snapshot investigations and ALL members *before* the CASCADE delete.
        # We include the workspace owner because they can also receive
        # workspace-derived view_investigation perms when other members share
        # their own investigations into this workspace.
        shared_invs = list(
            WorkspaceInvestigation.objects.select_for_update()
            .filter(workspace=workspace)
            .select_related("investigation")
        )
        shared_inv_ids = {winv.investigation_id for winv in shared_invs}
        members = list(
            WorkspaceMember.objects.select_for_update()
            .filter(workspace=workspace)
            .select_related("user")
        )

        if members and shared_inv_ids:
            member_user_ids = [m.user_id for m in members]

            # Batch 1: other workspace IDs for all members (single query).
            user_other_workspaces: dict[int, set[int]] = {}
            for row in (
                WorkspaceMember.objects.filter(user_id__in=member_user_ids)
                .exclude(workspace=workspace)
                .values("user_id", "workspace_id")
            ):
                user_other_workspaces.setdefault(row["user_id"], set()).add(
                    row["workspace_id"]
                )

            all_other_workspace_ids = {
                wid
                for ws_ids in user_other_workspaces.values()
                for wid in ws_ids
            }

            # Batch 2: which of the shared investigations are accessible from
            # those other workspaces (single query).
            workspace_inv_map: dict[int, set[int]] = {}
            if all_other_workspace_ids:
                for row in WorkspaceInvestigation.objects.filter(
                    investigation_id__in=shared_inv_ids,
                    workspace_id__in=all_other_workspace_ids,
                ).values("workspace_id", "investigation_id"):
                    workspace_inv_map.setdefault(row["workspace_id"], set()).add(
                        row["investigation_id"]
                    )

            for member in members:
                # Investigation IDs still accessible to this member via other
                # workspaces.
                retained_inv_ids: set[int] = set()
                for ws_id in user_other_workspaces.get(member.user_id, set()):
                    retained_inv_ids.update(workspace_inv_map.get(ws_id, set()))

                for winv in shared_invs:
                    if winv.investigation_id in retained_inv_ids:
                        continue
                    # Never revoke the investigation owner's own perm — it was
                    # granted by Investigation.save() and is baseline access,
                    # not workspace-derived.
                    if member.user_id == winv.investigation.owner_id:
                        continue
                    try:
                        remove_perm(
                            "view_investigation", member.user, winv.investigation
                        )
                    except Exception:
                        logger.exception(
                            "Failed to remove view_investigation perm for user %s "
                            "on investigation %s during workspace %s deletion",
                            getattr(member.user, "email", None),
                            getattr(winv.investigation, "id", None),
                            pk,
                        )
                        raise

        workspace.delete()
    return redirect("overview")


@login_required(login_url="/login/")
def add_workspace_member(request: HttpRequest, pk: int) -> JsonResponse:
    """Add a member to a workspace."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()

    if not membership or membership.role not in [
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    ]:
        return JsonResponse(
            {"success": False, "error": "You do not have permission"}, status=404
        )

    form = WorkspaceMemberForm(request.POST)
    if form.is_valid():
        user = form.cleaned_data["user"]
        role = form.cleaned_data["role"]

        # Only the actual workspace owner may hold the OWNER role.
        if role == WorkspaceRole.OWNER and workspace.owner_id != getattr(user, "id", None):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Only the workspace owner may be assigned the owner role",
                },
                status=400,
            )

        if WorkspaceMember.objects.filter(workspace=workspace, user=user).exists():
            return JsonResponse(
                {"success": False, "error": "User is already a member"}, status=400
            )

        try:
            with transaction.atomic():
                WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)

                # Ensure the newly added member receives object permissions for any
                # Investigations already shared into this workspace.
                shared_invs = WorkspaceInvestigation.objects.filter(
                    workspace=workspace
                ).select_related("investigation")
                for winv in shared_invs:
                    assign_perm("view_investigation", user, winv.investigation)
        except Exception:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Failed to add member and assign investigation permissions",
                },
                status=500,
            )

        return JsonResponse({"success": True, "errors": {}})
    else:
        return JsonResponse({"success": False, "errors": form.errors})


@login_required(login_url="/login/")
def add_workspace_member_by_email(request: HttpRequest, pk: int) -> JsonResponse:
    """Add a member to a workspace by email (AJAX friendly).

    Accepts POST with form-encoded fields:
      - email: user email address
      - role: one of WorkspaceRole values (optional, default MEMBER)

    Returns JSON similar to add_workspace_member.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()

    if not membership or membership.role not in [
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    ]:
        return JsonResponse(
            {"success": False, "error": "You do not have permission"}, status=404
        )

    email = request.POST.get("email", "").strip().lower()
    role = request.POST.get("role", WorkspaceRole.MEMBER)
    if not email:
        return JsonResponse({"success": False, "error": "email required"}, status=400)

    try:
        user = Person.objects.get(email__iexact=email)
    except Person.DoesNotExist:
        return JsonResponse({"success": False, "error": "User not found"}, status=404)

    if WorkspaceMember.objects.filter(workspace=workspace, user=user).exists():
        return JsonResponse(
            {"success": False, "error": "User is already a member"}, status=400
        )

    # normalize role — do not allow creating extra OWNER-role memberships via this
    # endpoint; those cannot be removed through the member-management flow.
    if role not in dict(WorkspaceRole.choices) or role == WorkspaceRole.OWNER:
        role = WorkspaceRole.MEMBER

    try:
        with transaction.atomic():
            WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)

            # Assign view permissions for all investigations already shared into this workspace
            shared_invs = WorkspaceInvestigation.objects.filter(
                workspace=workspace
            ).select_related("investigation")
            for winv in shared_invs:
                assign_perm("view_investigation", user, winv.investigation)
    except Exception:
        logger.exception(
            "Failed to add workspace member %s to workspace %s with investigation permissions",
            getattr(user, "email", None),
            getattr(workspace, "id", None),
        )
        return JsonResponse(
            {
                "success": False,
                "error": "Failed to add member and assign investigation permissions",
            },
            status=500,
        )

    return JsonResponse({"success": True, "member_email": user.email})


@login_required(login_url="/login/")
def remove_workspace_member(request: HttpRequest, pk: int, user_id: int) -> JsonResponse:
    """Remove a member from a workspace."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()

    if not membership or membership.role not in [
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    ]:
        return JsonResponse(
            {"success": False, "error": "You do not have permission"}, status=404
        )

    try:
        member_to_remove = WorkspaceMember.objects.get(
            workspace=workspace, user_id=user_id
        )
    except WorkspaceMember.DoesNotExist:
        return JsonResponse({"success": False, "error": "Member not found"}, status=404)

    if member_to_remove.role == WorkspaceRole.OWNER:
        return JsonResponse(
            {"success": False, "error": "Cannot remove the owner"}, status=400
        )

    # Revoke any object-level permissions the user received due to this workspace,
    # but only if the user does not retain access via another workspace that also shares
    # the same investigation. Revocation and membership deletion must succeed or fail
    # together so we do not leave stale access behind.
    try:
        with transaction.atomic():
            other_workspace_ids = (
                WorkspaceMember.objects.filter(user=member_to_remove.user)
                .exclude(workspace=workspace)
                .values_list("workspace_id", flat=True)
            )
            shared_invs = WorkspaceInvestigation.objects.filter(
                workspace=workspace
            ).select_related("investigation")
            for winv in shared_invs:
                if WorkspaceInvestigation.objects.filter(
                    investigation=winv.investigation, workspace_id__in=other_workspace_ids
                ).exists():
                    continue  # user still has access via another workspace — keep the perm
                # Never revoke the investigation owner's baseline perm.
                if member_to_remove.user_id == winv.investigation.owner_id:
                    continue
                remove_perm(
                    "view_investigation", member_to_remove.user, winv.investigation
                )

            member_to_remove.delete()
    except Exception:
        logger.exception(
            "Failed while revoking workspace-based permissions for removed member %s",
            user_id,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "Failed to remove member because permission cleanup did not complete",
            },
            status=500,
        )

    return JsonResponse({"success": True, "errors": {}})


@login_required(login_url="/login/")
def remove_workspace_member_by_email(request: HttpRequest, pk: int) -> JsonResponse:
    """AJAX endpoint to remove a member by email from a workspace.

    Expects POST 'email' field. Only OWNER/ADMIN can remove members.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    # Allow three cases to remove a member by email:
    # 1) requester is OWNER or ADMIN (can remove other members, but not the owner)
    # 2) requester is removing themself (allowed as long as they are not the owner)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()
    requester_role = membership.role if membership else None

    email = request.POST.get("email", "").strip().lower()
    if not email:
        return JsonResponse({"success": False, "error": "email required"}, status=400)

    try:
        user = Person.objects.get(email__iexact=email)
    except Person.DoesNotExist:
        return JsonResponse({"success": False, "error": "User not found"}, status=404)

    try:
        gm = WorkspaceMember.objects.get(workspace=workspace, user=user)
    except WorkspaceMember.DoesNotExist:
        return JsonResponse({"success": False, "error": "Not a member"}, status=404)

    # Prevent removal of the owner in all cases
    if gm.role == WorkspaceRole.OWNER:
        return JsonResponse(
            {"success": False, "error": "Cannot remove the owner"}, status=400
        )

    # If the requester is removing themself, allow it (unless owner)
    if user.id == request.user.id:
        # proceed to delete membership
        pass
    else:
        # requester is removing someone else -> must be owner or admin
        if not membership or requester_role not in [
            WorkspaceRole.OWNER,
            WorkspaceRole.ADMIN,
        ]:
            return JsonResponse(
                {"success": False, "error": "You do not have permission"}, status=404
            )

    # Revoke permissions granted via this workspace for the removed user,
    # but only if the user does not retain access via another workspace that also shares
    # the same investigation. If revocation fails, do not delete the membership.
    try:
        with transaction.atomic():
            other_workspace_ids = (
                WorkspaceMember.objects.filter(user=user)
                .exclude(workspace=workspace)
                .values_list("workspace_id", flat=True)
            )
            shared_invs = WorkspaceInvestigation.objects.filter(
                workspace=workspace
            ).select_related("investigation")
            for winv in shared_invs:
                if WorkspaceInvestigation.objects.filter(
                    investigation=winv.investigation, workspace_id__in=other_workspace_ids
                ).exists():
                    continue  # user still has access via another workspace — keep the perm
                # Never revoke the investigation owner's baseline perm.
                if user.id == winv.investigation.owner_id:
                    continue
                remove_perm("view_investigation", user, winv.investigation)

            gm.delete()
    except Exception:
        logger.exception(
            "Failed while revoking workspace-based permissions for removed member %s; membership was not deleted",
            user.id,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "Failed to revoke permissions; membership was not removed",
            },
            status=500,
        )

    return JsonResponse({"success": True})


@login_required(login_url="/login/")
def add_workspace_assay(request: HttpRequest, pk: int) -> JsonResponse:
    """Add an assay to a workspace."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()

    if not membership or membership.role not in [
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    ]:
        return JsonResponse(
            {"success": False, "error": "You do not have permission"}, status=404
        )

    # Accept either 'investigation' (preferred) or legacy 'assay' param containing an investigation id
    form = WorkspaceInvestigationForm(request.POST, user=request.user)
    if form.is_valid():
        investigation = form.cleaned_data.get("investigation") or None
    else:
        # fallback: try to read legacy param
        inv_id = request.POST.get("investigation") or request.POST.get("assay")
        investigation = None
        if inv_id:
            try:
                investigation = Investigation.objects.get(pk=int(inv_id))
            except Exception:
                investigation = None

    if not investigation:
        return JsonResponse(
            {
                "success": False,
                "errors": form.errors or {"investigation": ["Missing investigation"]},
            },
            status=400,
        )

    # Enforce owner-only sharing: only the owner of the investigation may add it to a workspace.
    if investigation.owner != request.user:
        return JsonResponse(
            {"success": False, "error": "You do not own this investigation."}, status=403
        )

    if WorkspaceInvestigation.objects.filter(
        workspace=workspace, investigation=investigation
    ).exists():
        return JsonResponse(
            {
                "success": False,
                "error": "Investigation is already shared in this workspace",
            },
            status=400,
        )

    try:
        with transaction.atomic():
            workspace_inv = WorkspaceInvestigation.objects.create(
                workspace=workspace, investigation=investigation, added_by=request.user
            )

            members = WorkspaceMember.objects.filter(workspace=workspace).select_related("user")
            for member in members:
                assign_perm("view_investigation", member.user, investigation)
    except Exception:
        logger.exception(
            "Failed to share investigation %s with workspace %s",
            investigation.pk,
            workspace.pk,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "Unable to share investigation with workspace members.",
            },
            status=500,
        )

    return JsonResponse({"success": True, "workspace_investigation_id": workspace_inv.id})


@login_required(login_url="/login/")
def remove_workspace_assay(request: HttpRequest, pk: int, assay_id: int) -> JsonResponse:
    """Remove an assay from a workspace."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    workspace = get_object_or_404(Workspace, pk=pk)
    membership = WorkspaceMember.objects.filter(
        workspace=workspace, user=request.user
    ).first()

    if not membership or membership.role not in [
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    ]:
        return JsonResponse(
            {"success": False, "error": "You do not have permission"}, status=404
        )

    # Treat the provided assay_id as the investigation id for the new model
    try:
        workspace_inv = WorkspaceInvestigation.objects.get(
            workspace=workspace, investigation_id=assay_id
        )
    except WorkspaceInvestigation.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Investigation not found in workspace"},
            status=404,
        )

    investigation = workspace_inv.investigation

    with transaction.atomic():
        workspace_inv.delete()
        # Revoke only where the rules allow: the investigation owner keeps their
        # baseline perm, and so does anyone who still reaches it through another
        # workspace. Shared with the admin in workspace_perms so the two entry
        # points cannot drift.
        revoke_investigation_from_members(workspace, investigation)

    return JsonResponse({"success": True, "errors": {}})
