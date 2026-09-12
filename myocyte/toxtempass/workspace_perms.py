"""Shared permission side effects for workspace sharing.

Sharing an investigation into a workspace is two writes, not one: the
``WorkspaceInvestigation`` row *and* a ``view_investigation`` guardian perm for
every member. A row without the perms leaves members seeing a share that grants
them nothing; perms without the row leave access nothing can revoke.

The views in ``workspace.py`` and the admin in ``admin.py`` are two entry points
into the same rules, so the rules live here once. In particular
``members_losing_access`` holds the two exemptions that are easy to get wrong:

* the investigation's **owner** never loses ``view_investigation`` — it is
  baseline access granted by ``Investigation.save()``, not workspace-derived;
* a member who reaches the same investigation through **another** workspace they
  belong to keeps it.
"""

from guardian.shortcuts import assign_perm, remove_perm

from toxtempass.models import (
    Investigation,
    Person,
    Workspace,
    WorkspaceInvestigation,
    WorkspaceMember,
)


def members_losing_access(
    workspace: Workspace, investigation: Investigation
) -> list[Person]:
    """Return the members who should lose ``view_investigation``.

    Call this *after* the ``WorkspaceInvestigation`` row is gone, so the
    cross-workspace lookup does not count the link being removed. Batched into
    three queries regardless of member count.
    """
    members = list(
        WorkspaceMember.objects.filter(workspace=workspace).select_related("user")
    )
    if not members:
        return []

    # Workspaces other than this one that share the same investigation.
    sharing_workspace_ids = set(
        WorkspaceInvestigation.objects.filter(investigation=investigation)
        .exclude(workspace=workspace)
        .values_list("workspace_id", flat=True)
    )
    retained_user_ids = (
        set(
            WorkspaceMember.objects.filter(
                user_id__in=[m.user_id for m in members],
                workspace_id__in=sharing_workspace_ids,
            ).values_list("user_id", flat=True)
        )
        if sharing_workspace_ids
        else set()
    )

    return [
        m.user
        for m in members
        if m.user_id != investigation.owner_id and m.user_id not in retained_user_ids
    ]


def grant_investigation_to_members(
    workspace: Workspace, investigation: Investigation
) -> int:
    """Give every current member ``view_investigation``. Returns the count."""
    members = WorkspaceMember.objects.filter(workspace=workspace).select_related("user")
    granted = 0
    for member in members:
        assign_perm("view_investigation", member.user, investigation)
        granted += 1
    return granted


def revoke_investigation_from_members(
    workspace: Workspace, investigation: Investigation
) -> int:
    """Revoke ``view_investigation`` where the exemptions allow. Returns the count."""
    losing = members_losing_access(workspace, investigation)
    for user in losing:
        remove_perm("view_investigation", user, investigation)
    return len(losing)


def grant_shared_investigations_to_member(workspace: Workspace, user: Person) -> int:
    """Give one member ``view_investigation`` for everything already shared."""
    shared = WorkspaceInvestigation.objects.filter(
        workspace=workspace
    ).select_related("investigation")
    granted = 0
    for link in shared:
        assign_perm("view_investigation", user, link.investigation)
        granted += 1
    return granted


def revoke_shared_investigations_from_member(workspace: Workspace, user: Person) -> int:
    """Revoke a leaving member's workspace-derived perms, honouring the exemptions.

    Call this *before* the ``WorkspaceMember`` row is deleted — the caller's
    membership still being present is what the cross-workspace check excludes.
    """
    other_workspace_ids = set(
        WorkspaceMember.objects.filter(user=user)
        .exclude(workspace=workspace)
        .values_list("workspace_id", flat=True)
    )
    shared = WorkspaceInvestigation.objects.filter(
        workspace=workspace
    ).select_related("investigation")

    revoked = 0
    for link in shared:
        investigation = link.investigation
        # Baseline perm from Investigation.save(); never workspace-derived.
        if user.id == investigation.owner_id:
            continue
        if other_workspace_ids and WorkspaceInvestigation.objects.filter(
            investigation=investigation, workspace_id__in=other_workspace_ids
        ).exists():
            continue
        remove_perm("view_investigation", user, investigation)
        revoked += 1
    return revoked
