"""New drafts, studies and assays can't be added to the demo investigation."""

from types import SimpleNamespace

import pytest

from toxtempass.forms import AssayForm, StartingForm, StudyForm
from toxtempass.models import Assay, Investigation, Study
from toxtempass.tests.fixtures.factories import PersonFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def work():
    """A user with a demo copy (as seeded for new accounts) and their own work."""
    user = PersonFactory()
    demo_investigation = Investigation.objects.create(owner=user, title="Example (Demo)")
    demo_study = Study.objects.create(
        investigation=demo_investigation, title="Study (Demo)"
    )
    demo_assay = Assay.objects.create(
        study=demo_study, title="Assay (Demo)", description="", demo_lock=True
    )
    investigation = Investigation.objects.create(owner=user, title="My investigation")
    study = Study.objects.create(investigation=investigation, title="My study")
    assay = Assay.objects.create(study=study, title="My assay", description="")
    return SimpleNamespace(
        user=user,
        demo_investigation=demo_investigation,
        demo_study=demo_study,
        demo_assay=demo_assay,
        investigation=investigation,
        study=study,
        assay=assay,
    )


def test_add_page_offers_nothing_from_the_demo(work):
    form = StartingForm(user=work.user)
    assert list(form.fields["investigation"].queryset) == [work.investigation]
    assert list(form.fields["study"].queryset) == [work.study]
    assert list(form.fields["assay"].queryset) == [work.assay]


def test_a_new_study_cannot_go_into_the_demo_investigation(work):
    form = StudyForm(
        data={"investigation": work.demo_investigation.pk, "title": "New study"},
        user=work.user,
    )
    assert not form.is_valid()
    assert "investigation" in form.errors
    assert list(form.fields["investigation"].queryset) == [work.investigation]


def test_a_new_assay_cannot_go_into_the_demo_study(work):
    form = AssayForm(
        data={"study": work.demo_study.pk, "title": "New assay", "description": "x"},
        user=work.user,
    )
    assert not form.is_valid()
    assert "study" in form.errors
    assert list(form.fields["study"].queryset) == [work.study]


def test_editing_the_demo_keeps_its_own_investigation_and_study(work):
    study_form = StudyForm(instance=work.demo_study, user=work.user)
    assert set(study_form.fields["investigation"].queryset) == {
        work.investigation,
        work.demo_investigation,
    }
    assay_form = AssayForm(instance=work.demo_assay, user=work.user)
    assert set(assay_form.fields["study"].queryset) == {work.study, work.demo_study}
