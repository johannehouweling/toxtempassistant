"""Per-workspace tabs on the assay overview (AssayListView ?workspace= filter)."""

import pytest
from django.test import Client
from django.urls import reverse
from guardian.shortcuts import assign_perm

from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionSetFactory,
    StudyFactory,
    WorkspaceFactory,
    WorkspaceInvestigationFactory,
    WorkspaceMemberFactory,
)


@pytest.fixture
def qset(db):
    # Explicit label: demo seeding (signals.post_save on Person) also creates a
    # QuestionSet, and label is unique — the factory sequence alone can collide.
    return QuestionSetFactory.create(label="ws-tabs")


def _assay(owner, qset, title):
    """An assay the owner can view, with a questionnaire so it hits the list."""
    investigation = InvestigationFactory.create(owner=owner)
    assign_perm("view_investigation", owner, investigation)
    study = StudyFactory.create(investigation=investigation)
    return AssayFactory.create(study=study, title=title, question_set=qset)


@pytest.mark.django_db
def test_workspace_tab_filters_and_counts(qset):
    """?workspace=<pk> shows only that workspace's assays; the badge matches."""
    user = PersonFactory.create()
    shared = _assay(user, qset, "Shared assay")
    _assay(user, qset, "Private assay")

    workspace = WorkspaceFactory.create(owner=user)
    WorkspaceInvestigationFactory.create(
        workspace=workspace, investigation=shared.study.investigation
    )

    client = Client()
    client.force_login(user)
    url = reverse("overview")

    # Unfiltered: both assays, and the tab badge counts only the shared one.
    unfiltered = client.get(url)
    assert unfiltered.status_code == 200
    titles = {a.title for a in unfiltered.context["table"].data}
    assert titles == {"Shared assay", "Private assay"}
    assert unfiltered.context["workspace_tabs"] == [
        {"pk": workspace.pk, "name": workspace.name, "count": 1}
    ]

    # Filtered: only the investigation shared into that workspace.
    filtered = client.get(url, {"workspace": workspace.pk})
    assert [a.title for a in filtered.context["table"].data] == ["Shared assay"]
    assert filtered.context["selected_workspace_pk"] == workspace.pk


@pytest.mark.django_db
def test_foreign_workspace_pk_is_ignored(qset):
    """A workspace the user is not a member of falls back to the full list."""
    user = PersonFactory.create()
    _assay(user, qset, "Mine")

    stranger = PersonFactory.create()
    foreign = WorkspaceFactory.create(owner=stranger)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("overview"), {"workspace": foreign.pk})

    assert response.context["selected_workspace_pk"] is None
    assert response.context["workspace_tabs"] == []
    assert [a.title for a in response.context["table"].data] == ["Mine"]


@pytest.mark.django_db
def test_joined_workspace_appears_with_zero_count(qset):
    """Membership alone earns a tab, even before anything is shared into it."""
    user = PersonFactory.create()
    workspace = WorkspaceFactory.create(owner=PersonFactory.create())
    WorkspaceMemberFactory.create(workspace=workspace, user=user)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("overview"))

    assert response.context["workspace_tabs"] == [
        {"pk": workspace.pk, "name": workspace.name, "count": 0}
    ]


@pytest.mark.django_db
def test_search_matches_assay_study_and_investigation_titles(qset):
    """?q= matches any of the three titles the table shows."""
    user = PersonFactory.create()
    investigation = InvestigationFactory.create(owner=user, title="Thyroid programme")
    assign_perm("view_investigation", user, investigation)
    study = StudyFactory.create(investigation=investigation, title="WP2.4 neural cells")
    AssayFactory.create(study=study, title="Deiodinase assay", question_set=qset)
    other = _assay(user, qset, "Kidney LDH release")

    client = Client()
    client.force_login(user)
    url = reverse("overview")

    def titles(**params):
        return {a.title for a in client.get(url, params).context["table"].data}

    assert titles(q="deiodinase") == {"Deiodinase assay"}  # assay title, case-insensitive
    assert titles(q="WP2.4") == {"Deiodinase assay"}  # study title
    assert titles(q="Thyroid") == {"Deiodinase assay"}  # investigation title
    assert titles(q="  ") == {"Deiodinase assay", other.title}  # blank is no filter
    assert titles(q="nothing here") == set()


@pytest.mark.django_db
def test_search_narrows_workspace_tab_counts(qset):
    """A tab badge always matches what that tab would show under the search."""
    user = PersonFactory.create()
    hit = _assay(user, qset, "Deiodinase assay")
    miss = _assay(user, qset, "LDH release")

    workspace = WorkspaceFactory.create(owner=user)
    for assay in (hit, miss):
        WorkspaceInvestigationFactory.create(
            workspace=workspace, investigation=assay.study.investigation
        )

    client = Client()
    client.force_login(user)
    response = client.get(reverse("overview"), {"q": "deiodinase"})

    assert response.context["workspace_tabs"][0]["count"] == 1
    assert response.context["search_query"] == "deiodinase"
