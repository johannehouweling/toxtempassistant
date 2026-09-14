"""Signup pushes back when the organization matches no ROR record."""

from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from toxtempass import Config, ror
from toxtempass.models import Person

pytestmark = pytest.mark.django_db

MATCH = "toxtempass.ror.match_organization"
RIVM_ID = "https://ror.org/01cesdt21"
RIVM_NAME = "National Institute for Public Health and the Environment"
SIGNUP = {
    "email": "lazy.person@rivm.nl",
    "first_name": "Lazy",
    "last_name": "Person",
    "organization": "rivm lab",
    "password1": "a-Long-and-unusual-pass-42",
    "password2": "a-Long-and-unusual-pass-42",
    "has_accepted_tos": "on",
}


@pytest.fixture(autouse=True)
def _ror_enabled(settings):
    settings.ROR_LOOKUP_ENABLED = True
    cache.clear()  # the signup rate limit counts per IP in the cache


def _ror_search(*names):
    """Patch the suggestion search to return organizations with these names."""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "items": [
            {
                "id": f"https://ror.org/{index}",
                "names": [{"value": name, "types": ["ror_display"]}],
                "locations": [{"geonames_details": {"country_name": "The Netherlands"}}],
            }
            for index, name in enumerate(names)
        ]
    }
    return patch("toxtempass.ror.requests.get", return_value=response)


def test_unmatched_organization_is_refused_with_suggestions(client):
    with patch(MATCH, return_value=None), _ror_search(RIVM_NAME):
        response = client.post(reverse("signup"), SIGNUP)

    assert response.json()["success"] is False
    assert response.json()["errors"]["organization"] == [
        Config.ror_unmatched_organization_message,
        f"Did you mean: {RIVM_NAME} (The Netherlands)?",
    ]
    assert not Person.objects.filter(email=SIGNUP["email"]).exists()


def test_submitting_again_is_not_enough_but_ticking_not_in_ror_is(client):
    with patch(MATCH, return_value=None), _ror_search():
        client.post(reverse("signup"), SIGNUP)
        again = client.post(reverse("signup"), SIGNUP)
    assert again.json()["success"] is False

    with patch(MATCH) as match:
        response = client.post(
            reverse("signup"), {**SIGNUP, "organization_not_in_ror": "on"}
        )

    assert response.json()["success"] is True
    match.assert_not_called()
    person = Person.objects.get(email=SIGNUP["email"])
    # Left unchecked, so the background lookup still tries.
    assert (person.organization, person.ror_checked_organization) == ("rivm lab", "")


def test_matched_organization_signs_up_straight_away(client):
    with (
        patch(MATCH, return_value=(RIVM_ID, RIVM_NAME)),
        patch("toxtempass.ror.requests.get") as search,
    ):
        response = client.post(reverse("signup"), {**SIGNUP, "organization": "RIVM"})

    assert response.json()["success"] is True
    search.assert_not_called()
    person = Person.objects.get(email=SIGNUP["email"])
    assert (person.ror_id, person.ror_name, person.ror_checked_organization) == (
        RIVM_ID,
        RIVM_NAME,
        "RIVM",
    )


def test_signup_goes_ahead_when_ror_cannot_be_reached(client):
    with patch(MATCH, side_effect=ror.RorLookupError("timeout")):
        response = client.post(reverse("signup"), SIGNUP)

    assert response.json()["success"] is True
    assert Person.objects.get(email=SIGNUP["email"]).ror_checked_organization == ""


def test_suggested_names_are_escaped(client):
    with patch(MATCH, return_value=None), _ror_search("Lab <b>&</b> Co"):
        response = client.post(reverse("signup"), SIGNUP)

    assert response.json()["errors"]["organization"][1] == (
        "Did you mean: Lab &lt;b&gt;&amp;&lt;/b&gt; Co (The Netherlands)?"
    )


def test_not_in_ror_checkbox_starts_hidden(client):
    content = client.get(reverse("signup")).content.decode()
    assert '<div id="organization-not-in-ror" hidden>' in content
    assert 'name="organization_not_in_ror"' in content
