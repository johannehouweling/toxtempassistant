"""An assay keeps the history of what its draft was made from.

The description (with the title and the questionnaire) goes into the prompt, so a
bad draft can be traced to a bad description that was corrected afterwards. Status,
logs and flags change on every run and are not kept.
"""

import pytest
from django.urls import reverse

from toxtempass.models import Assay, LLMStatus
from toxtempass.tests.fixtures.factories import AdminFactory, AssayFactory, PersonFactory

pytestmark = pytest.mark.django_db


def _rows(assay):
    return list(assay.history.order_by("history_id"))


def test_creating_an_assay_records_its_starting_point():
    assay = AssayFactory(title="HepG2", description="Cytotoxicity")
    (row,) = _rows(assay)
    assert row.history_type == "+"
    assert (row.title, row.description) == ("HepG2", "Cytotoxicity")


def test_a_corrected_description_keeps_the_one_the_draft_was_made_from():
    assay = AssayFactory(description="asdf")
    assay.description = "A proper description of the method"
    assay.save()
    old, new = _rows(assay)
    assert (old.description, new.description) == ("asdf", assay.description)
    assert new.history_type == "~" and new.history_date >= old.history_date


def test_changes_of_title_and_questionnaire_are_kept_too():
    assay = AssayFactory(title="One")
    assay.title = "Two"
    assay.save()
    assert [row.title for row in _rows(assay)] == ["One", "Two"]


def test_who_changed_it_is_kept_when_a_person_did():
    assay = AssayFactory(description="before")
    person = PersonFactory()
    assay.description = "after"
    assay._history_user = person
    assay.save()
    assert _rows(assay)[-1].history_user == person


@pytest.mark.parametrize(
    "change",
    [
        {"status": LLMStatus.BUSY},
        {"processing_log": "[abc] file processed\n" * 50},
        {"user_alerts": [{"message": "x", "level": "info", "ts": 1}]},
        {"completion_time_seconds": 1200},
        {"demo_lock": True},
    ],
)
def test_changes_that_say_nothing_about_the_draft_write_no_row(change):
    assay = AssayFactory()
    for field, value in change.items():
        setattr(assay, field, value)
    assay.save()
    assert len(_rows(assay)) == 1  # only the creation


def test_saving_an_unchanged_assay_writes_no_row():
    assay = AssayFactory()
    assay.save()
    assay.save()
    assert len(_rows(assay)) == 1


def test_a_change_between_status_updates_is_still_recorded():
    assay = AssayFactory(description="before")
    assay.status = LLMStatus.BUSY
    assay.save()
    assay.description = "after"
    assay.status = LLMStatus.DONE
    assay.save()
    assert [row.description for row in _rows(assay)] == ["before", "after"]


def test_the_noisy_fields_are_not_stored_at_all():
    names = {field.name for field in Assay.history.model._meta.concrete_fields}
    assert {"title", "description", "question_set", "study", "created_by"} <= names
    assert not names & {"processing_log", "user_alerts", "status"}


def test_the_history_goes_with_the_assay():
    assay = AssayFactory(description="x")
    pk = assay.pk
    assay.description = "y"
    assay.save()
    assert Assay.history.model.objects.filter(id=pk).count() == 2
    assay.delete()
    assert not Assay.history.model.objects.filter(id=pk).exists()


def test_the_admin_shows_the_history(client):
    admin = AdminFactory()
    assay = AssayFactory(description="asdf")
    assay.description = "A proper description"
    assay._history_user = admin
    assay.save()
    client.force_login(admin)
    page = client.get(reverse("admin:toxtempass_assay_history", args=[assay.pk]))
    assert page.status_code == 200
    html = page.content.decode()
    assert "Changed by" in html and admin.email in html
    # The page shows what the description was, and what it was changed to.
    assert "asdf" in html and "A proper description" in html
    first = assay.history.order_by("history_id").first()
    detail = reverse(
        "admin:toxtempass_assay_simple_history", args=[assay.pk, first.history_id]
    )
    assert client.get(detail).status_code == 200
