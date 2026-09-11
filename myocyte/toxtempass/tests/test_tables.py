import pytest
from django.test import RequestFactory
from django.urls import reverse

from toxtempass.models import AssayCost, LLMStatus
from toxtempass.tables import AssayTable
from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    PersonFactory,
    WorkspaceFactory,
    WorkspaceInvestigationFactory,
)


@pytest.mark.django_db
class TestAssayTableInvestigationColumn:
    def test_shared_investigation_icon_renders_inline_without_absolute_positioning(self):
        user = PersonFactory.create()
        investigation_owner = PersonFactory.create()
        assay = AssayFactory.create(study__investigation__owner=investigation_owner)

        visible_workspace = WorkspaceFactory.create(owner=user, name="Visible workspace")
        hidden_workspace = WorkspaceFactory.create(
            owner=PersonFactory.create(),
            name="Hidden workspace",
        )
        WorkspaceInvestigationFactory.create(
            workspace=visible_workspace,
            investigation=assay.study.investigation,
            added_by=visible_workspace.owner,
        )
        WorkspaceInvestigationFactory.create(
            workspace=hidden_workspace,
            investigation=assay.study.investigation,
            added_by=hidden_workspace.owner,
        )

        request = RequestFactory().get("/")
        request.user = user
        table = AssayTable([assay])
        table.context = {"request": request}

        rendered = str(table.render_investigation(assay))

        assert "bi-share" in rendered
        assert rendered.startswith(
            '<span class="d-inline-flex align-items-center flex-wrap gap-1">'
        )
        assert "d-inline-flex align-items-center flex-wrap gap-1" in rendered
        assert '<button type="button"' in rendered
        assert 'data-bs-toggle="offcanvas"' in rendered
        assert 'data-bs-target="#offcanvasUser"' in rendered
        assert 'aria-label="View workspaces sharing this investigation"' in rendered
        assert 'href="#offcanvasUser"' not in rendered
        assert '<span type="button"' not in rendered
        assert "position-absolute" not in rendered
        assert "start-100" not in rendered
        assert "translate-middle-y" not in rendered
        assert "Visible workspace" in rendered
        assert "Hidden workspace" not in rendered

    def test_non_shared_investigation_renders_title_only(self):
        assay = AssayFactory.create(study__investigation__title="Simple Investigation")
        request = RequestFactory().get("/")
        request.user = PersonFactory.create()
        table = AssayTable([assay])
        table.context = {"request": request}

        rendered = str(table.render_investigation(assay))

        assert rendered == "Simple Investigation"


@pytest.mark.django_db
class TestAssayTableCostColumn:
    def test_cost_breakdown_popover_renders_multiple_rows_and_total(self):
        assay = AssayFactory.create()
        AssayCost.objects.create(
            assay=assay,
            model_key="4:GPT4OMINI",
            model_id="gpt-4o-mini",
            input_tokens=813_838,
            output_tokens=9_142,
            cost_input_per_1m="0.150000",
            cost_output_per_1m="0.600000",
            cost_input="0.122076",
            cost_output="0.005485",
            cost_unit="Eur",
        )
        AssayCost.objects.create(
            assay=assay,
            model_key="4:GPT4O",
            model_id="gpt-4o",
            input_tokens=3_100,
            output_tokens=600,
            cost_input_per_1m="2.500000",
            cost_output_per_1m="10.000000",
            cost_input="0.007750",
            cost_output="0.006000",
            cost_unit="Eur",
        )
        table = AssayTable([assay])

        rendered = str(table.render_cost(None, assay))

        assert "table-responsive" in rendered
        assert "text-break" in rendered
        assert "text-nowrap" in rendered
        assert 'data-bs-custom-class="mw-100"' in rendered
        assert "4:GPT4OMINI" in rendered
        assert "4:GPT4O" in rendered
        assert "gpt-4o-mini" in rendered
        assert "gpt-4o" in rendered
        assert "€0.1413" in rendered


@pytest.mark.django_db
class TestAssayTableActionColumn:
    @pytest.mark.parametrize(
        "status,blocked",
        [
            (LLMStatus.DONE, False),
            (LLMStatus.ERROR, True),
            (LLMStatus.BUSY, True),
            (LLMStatus.SCHEDULED, True),
        ],
    )
    def test_delete_always_reachable_and_exports_gated_by_status(self, status, blocked):
        assay = AssayFactory.create(status=status)

        rendered = str(AssayTable([assay]).rows[0].get_cell("action"))

        # Delete shares the overflow menu with the exports, so it must survive every
        # status — especially error, the usual reason to delete an assay.
        assert "js-delete-link" in rendered
        assert rendered.count("feedback_export(") == 7
        assert rendered.count('aria-disabled="true"') == (7 if blocked else 0)


@pytest.mark.django_db
def test_sortable_headers_show_sort_state(client):
    client.force_login(PersonFactory.create())

    html = client.get(reverse("overview"), {"sort": "-study"}).content.decode()

    assert 'aria-sort="descending"' in html
    assert "bi-caret-down-fill" in html
    # Assay + Investigation stay sortable-but-inactive (Owner is superuser-only).
    assert html.count("bi-chevron-expand") == 2
