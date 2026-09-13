from io import StringIO
from unittest.mock import Mock, patch

import pytest
import requests
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from toxtempass import ror
from toxtempass.models import Person
from toxtempass.tests.fixtures.factories import PersonFactory

GET = "toxtempass.ror.requests.get"
RIVM_ID = "https://ror.org/01cesdt21"
RIVM_NAME = "National Institute for Public Health and the Environment"
RIVM_NAMES = [
    {"value": RIVM_NAME, "types": ["ror_display", "label"]},
    {"value": "RIVM", "types": ["acronym"]},
]


def _resp(items, status=200):
    response = Mock(status_code=status)
    response.json.return_value = {"items": items}
    return response


def _affiliation(chosen):
    return {
        "chosen": chosen,
        "score": 1.0,
        "organization": {"id": RIVM_ID, "names": RIVM_NAMES},
    }


def _query_item(ror_id=RIVM_ID, names=RIVM_NAMES):
    return {"id": ror_id, "names": names}


def test_chosen_affiliation_match():
    with patch(GET, return_value=_resp([_affiliation(True)])) as get:
        assert ror.lookup_organization(f"  {RIVM_NAME} ") == (RIVM_ID, RIVM_NAME)
    get.assert_called_once()
    assert get.call_args.kwargs["params"] == {"affiliation": RIVM_NAME}
    assert get.call_args.kwargs["headers"] == {}


def test_acronym_falls_back_to_exact_query_match(monkeypatch):
    monkeypatch.setenv("ROR_CLIENT_ID", "test-client")
    near_miss = _query_item(
        "https://ror.org/other", [{"value": "RIVM Foundation", "types": ["ror_display"]}]
    )
    with patch(GET, side_effect=[_resp([]), _resp([near_miss, _query_item()])]) as get:
        assert ror.lookup_organization("rivm") == (RIVM_ID, RIVM_NAME)
    assert get.call_args.kwargs["params"] == {"query": "rivm"}
    assert get.call_args.kwargs["headers"] == {"Client-Id": "test-client"}


def test_ambiguous_unmatched_or_blank_returns_none():
    twins = [_query_item("https://ror.org/a"), _query_item("https://ror.org/b")]
    responses = [_resp([_affiliation(False)]), _resp(twins), _resp([]), _resp([])]
    with patch(GET, side_effect=responses) as get:
        assert ror.lookup_organization("RIVM") is None
        assert ror.lookup_organization("Nowhere Lab") is None
        assert ror.lookup_organization("   ") is None
    assert get.call_count == 4


broken_json = Mock(status_code=200)
broken_json.json.side_effect = ValueError("not json")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("down"),
        _resp([], status=503),
        broken_json,
        _resp([{"chosen": True}]),  # no organization key
    ],
)
def test_api_failure_returns_none_and_leaves_person_unchecked(failure):
    person = PersonFactory(organization="RIVM")
    with patch(GET, side_effect=[failure, failure]):
        assert ror.lookup_organization("RIVM") is None
    with patch(GET, side_effect=[failure, failure]):
        ror.resolve_person(person.pk)
    person.refresh_from_db()
    assert (person.ror_id, person.ror_checked_organization) == ("", "")


@pytest.mark.django_db
@override_settings(ROR_LOOKUP_ENABLED=True)
def test_resolve_person_writes_fields_without_retriggering_signal(
    django_capture_on_commit_callbacks,
):
    person = PersonFactory()  # no organization: nothing queued
    Person.objects.filter(pk=person.pk).update(organization=RIVM_NAME)
    with (
        patch(GET, return_value=_resp([_affiliation(True)])),
        patch("toxtempass.signals.queue_ror_lookup") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        ror.resolve_person(person)
    queued.assert_not_called()
    person.refresh_from_db()
    assert (person.ror_id, person.ror_name, person.ror_checked_organization) == (
        RIVM_ID,
        RIVM_NAME,
        RIVM_NAME,
    )

    with patch(GET) as get:  # already checked: no API call
        ror.resolve_person(person.pk)
    get.assert_not_called()

    Person.objects.filter(pk=person.pk).update(organization="Nowhere Lab")
    with patch(GET, side_effect=[_resp([]), _resp([])]):
        ror.resolve_person(person.pk)
    person.refresh_from_db()
    assert (person.ror_id, person.ror_name, person.ror_checked_organization) == (
        "",
        "",
        "Nowhere Lab",
    )


@pytest.mark.django_db
def test_signal_gated_off_under_tests(django_capture_on_commit_callbacks):
    assert settings.ROR_LOOKUP_ENABLED is False
    with (
        patch("toxtempass.signals.queue_ror_lookup") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        PersonFactory(organization="RIVM")
    queued.assert_not_called()


@pytest.mark.django_db
@override_settings(ROR_LOOKUP_ENABLED=True)
def test_signal_queues_lookup_when_organization_changes(
    django_capture_on_commit_callbacks,
):
    with (
        patch("toxtempass.signals.queue_ror_lookup") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        person = PersonFactory(organization="RIVM")
        PersonFactory(organization="")
    queued.assert_called_once_with(person.pk)


@pytest.mark.django_db
@override_settings(ROR_LOOKUP_ENABLED=True)
def test_signal_skips_saves_that_do_not_write_organization(
    django_capture_on_commit_callbacks,
):
    person = PersonFactory()
    Person.objects.filter(pk=person.pk).update(organization="RIVM")  # still unchecked
    person.refresh_from_db()
    with (
        patch("toxtempass.signals.queue_ror_lookup") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        person.save(update_fields=["last_login"])  # what every login does
        person.save_base(raw=True)  # what loaddata does
        person.save(update_fields=["organization"])
    queued.assert_called_once_with(person.pk)


@pytest.mark.django_db
def test_backfill_dedupes_organizations_and_dry_run_writes_nothing():
    rivm = [PersonFactory(organization="RIVM") for _ in range(2)]
    nowhere = PersonFactory(organization="Nowhere Lab")
    down = PersonFactory(organization="Down Institute")
    PersonFactory(organization="")
    done = PersonFactory(organization="Checked")
    Person.objects.filter(pk=done.pk).update(ror_checked_organization="Checked")

    def fake_get(url, params, headers, timeout):
        if "Down Institute" in params.values():
            raise requests.ConnectionError("down")
        return _resp([_query_item()] if params == {"query": "RIVM"} else [])

    sleep = "toxtempass.management.commands.backfill_ror.time.sleep"
    out = StringIO()
    with patch(GET, side_effect=fake_get) as get, patch(sleep):
        call_command("backfill_ror", "--dry-run", stdout=out)
    # Down: 1 failing call; Nowhere Lab: 2; RIVM: 2 — once each despite 2 RIVM persons.
    assert get.call_count == 5
    assert "3 organizations: 1 matched, 1 unmatched, 1 errors" in out.getvalue()
    checked = Person.objects.exclude(pk=done.pk).values_list(
        "ror_checked_organization", flat=True
    )
    assert set(checked) == {""}

    with patch(GET, side_effect=fake_get), patch(sleep):
        call_command("backfill_ror", stdout=StringIO())
    for person in rivm:
        person.refresh_from_db()
        assert (person.ror_id, person.ror_checked_organization) == (RIVM_ID, "RIVM")
    nowhere.refresh_from_db()
    down.refresh_from_db()
    assert (nowhere.ror_id, nowhere.ror_checked_organization) == ("", "Nowhere Lab")
    assert down.ror_checked_organization == ""
