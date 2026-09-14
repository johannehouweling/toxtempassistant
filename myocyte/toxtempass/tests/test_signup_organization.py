import json
from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

from toxtempass.forms import SignupForm
from toxtempass.views import ror_organization_lookup


def _mock_ror_response(*items):
    """Create a successful mocked ROR API response containing the given items."""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"items": list(items)}
    return response


def _ror_item(
    *,
    ror_id: str,
    display_name: str,
    country_name: str,
    acronym: str | None = None,
    labels: list[tuple[str | None, str]] | None = None,
    website: str | None = None,
    domain: str | None = None,
) -> dict:
    """Build a realistic ROR v2 organization item for signup lookup tests."""
    names = [{"lang": "en", "types": ["ror_display", "label"], "value": display_name}]
    if acronym:
        names.append({"lang": None, "types": ["acronym"], "value": acronym})
    for lang, value in labels or []:
        names.append({"lang": lang, "types": ["label"], "value": value})

    item = {
        "id": ror_id,
        "links": [{"type": "website", "value": website}] if website else [],
        "locations": [{"geonames_details": {"country_name": country_name}}],
        "names": names,
    }
    if domain:
        item["domains"] = [domain]
    return item


def test_signup_form_marks_organization_as_required():
    form = SignupForm()
    assert form.fields["organization"].required is True


@pytest.mark.django_db
def test_signup_form_rejects_missing_organization():
    form = SignupForm(
        data={
            "email": "newuser@example.com",
            "first_name": "New",
            "last_name": "User",
            "organization": "   ",
            "password1": "VeryStrongPass123!",
            "password2": "VeryStrongPass123!",
            "has_accepted_tos": True,
        }
    )

    assert form.is_valid() is False
    assert "organization" in form.errors


def test_ror_lookup_ignores_short_query():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "ab"})

    with patch("toxtempass.views.requests.get") as mocked_get:
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload == {"items": []}
    mocked_get.assert_not_called()


def test_ror_lookup_rejects_invalid_query_chars():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "abc<script>"})

    with patch("toxtempass.views.requests.get") as mocked_get:
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload == {"items": []}
    mocked_get.assert_not_called()


def test_ror_lookup_returns_suggestions():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Leiden"})
    mocked_response = _mock_ror_response(
        _ror_item(
            ror_id="https://ror.org/027bh9e22",
            display_name="Leiden University",
            country_name="Netherlands",
            acronym="LU",
            website="https://www.universiteitleiden.nl/en",
            domain="universiteitleiden.nl",
        )
    )

    with patch("toxtempass.views.requests.get", return_value=mocked_response):
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"] == [
        {
            "name": "Leiden University",
            "label": "Leiden University",
            "id": "https://ror.org/027bh9e22",
        }
    ]


def test_ror_lookup_returns_suggestions_for_v2_payload():
    """ROR v2 schema: names[] tagged with types, locations[].geonames_details."""
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Leiden"})
    mocked_response = _mock_ror_response(
        {
            "id": "https://ror.org/027bh9e22",
            "names": [
                {"lang": None, "types": ["acronym"], "value": "LU"},
                {
                    "lang": "en",
                    "types": ["ror_display", "label"],
                    "value": "Leiden University",
                },
                {
                    "lang": "nl",
                    "types": ["label"],
                    "value": "Universiteit Leiden",
                },
            ],
            "links": [
                {"type": "website", "value": "https://www.universiteitleiden.nl/en"}
            ],
            "locations": [{"geonames_details": {"country_name": "Netherlands"}}],
        }
    )

    with patch("toxtempass.views.requests.get", return_value=mocked_response):
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"] == [
        {
            "name": "Leiden University",
            "label": "Leiden University",
            "id": "https://ror.org/027bh9e22",
        }
    ]


def test_ror_lookup_returns_suggestions_for_nested_ror_payload():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Leiden"})
    mocked_response = _mock_ror_response(
        {
            "organization": _ror_item(
                ror_id="https://ror.org/027bh9e22",
                display_name="Leiden University",
                country_name="Netherlands",
                acronym="LU",
                labels=[("nl", "Universiteit Leiden")],
                website="https://www.universiteitleiden.nl/en",
                domain="universiteitleiden.nl",
            )
        }
    )

    with patch("toxtempass.views.requests.get", return_value=mocked_response):
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"] == [
        {
            "name": "Leiden University",
            "label": "Leiden University",
            "id": "https://ror.org/027bh9e22",
        }
    ]


def test_ror_lookup_uses_advanced_query_param():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Leiden"})
    mocked_response = Mock()
    mocked_response.raise_for_status.return_value = None
    mocked_response.json.return_value = {"items": []}

    with patch(
        "toxtempass.views.requests.get", return_value=mocked_response
    ) as mocked_get:
        ror_organization_lookup(request)

    mocked_get.assert_called_once()
    assert mocked_get.call_args.kwargs["params"] == {
        "query.advanced": 'names.value:"Leiden"'
    }


def test_ror_lookup_tries_domain_queries_first_when_email_given():
    request = RequestFactory().get(
        "/login/signup/ror-organizations/",
        {"q": "Nat", "email": "person@rivm.nl"},
    )
    strict_response = _mock_ror_response()

    domain_response = _mock_ror_response(
        _ror_item(
            ror_id="https://ror.org/01cesdt21",
            display_name="National Institute for Public Health and the Environment",
            country_name="The Netherlands",
            acronym="RIVM",
            labels=[("nl", "Rijksinstituut voor Volksgezondheid en Milieu")],
            website="https://www.rivm.nl",
            domain="rivm.nl",
        )
    )

    with patch(
        "toxtempass.views.requests.get", side_effect=[strict_response, domain_response]
    ) as mocked_get:
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"][0]["name"] == (
        "National Institute for Public Health and the Environment"
    )
    assert mocked_get.call_count == 2
    assert mocked_get.call_args_list[0].kwargs["params"] == {
        "query.advanced": (
            'links.value:"rivm.nl" AND '
            'names.value:"Nat"'
        )
    }
    assert mocked_get.call_args_list[1].kwargs["params"] == {
        "query.advanced": 'links.value:"rivm.nl"'
    }


def test_ror_lookup_falls_back_to_wide_query_when_domain_queries_are_empty():
    request = RequestFactory().get(
        "/login/signup/ror-organizations/",
        {"q": "Leiden", "email": "person@rivm.nl"},
    )
    empty_response_one = _mock_ror_response()
    empty_response_two = _mock_ror_response()
    wide_response = _mock_ror_response(
        _ror_item(
            ror_id="https://ror.org/027bh9e22",
            display_name="Leiden University",
            country_name="Netherlands",
            acronym="LU",
            labels=[("nl", "Universiteit Leiden")],
            website="https://www.universiteitleiden.nl/en",
            domain="universiteitleiden.nl",
        )
    )

    with patch(
        "toxtempass.views.requests.get",
        side_effect=[empty_response_one, empty_response_two, wide_response],
    ) as mocked_get:
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"][0]["name"] == "Leiden University"
    assert mocked_get.call_count == 3
    assert mocked_get.call_args_list[2].kwargs["params"] == {
        "query.advanced": 'names.value:"Leiden"'
    }


def test_ror_lookup_ignores_invalid_email_domain():
    request = RequestFactory().get(
        "/login/signup/ror-organizations/",
        {"q": "Leiden", "email": "person@localhost"},
    )
    mocked_response = _mock_ror_response()

    with patch("toxtempass.views.requests.get", return_value=mocked_response) as mocked_get:
        ror_organization_lookup(request)

    mocked_get.assert_called_once()
    assert mocked_get.call_args.kwargs["params"] == {
        "query.advanced": 'names.value:"Leiden"'
    }


def test_ror_lookup_keeps_email_domain_as_is():
    request = RequestFactory().get(
        "/login/signup/ror-organizations/",
        {"q": "Leiden", "email": "person@www.rivm.nl"},
    )
    first_response = _mock_ror_response()
    second_response = _mock_ror_response()
    third_response = _mock_ror_response()

    with patch(
        "toxtempass.views.requests.get",
        side_effect=[first_response, second_response, third_response],
    ) as mocked_get:
        ror_organization_lookup(request)

    assert mocked_get.call_args_list[0].kwargs["params"] == {
        "query.advanced": (
            'links.value:"www.rivm.nl" AND '
            'names.value:"Leiden"'
        )
    }
    assert mocked_get.call_args_list[1].kwargs["params"] == {
        "query.advanced": 'links.value:"www.rivm.nl"'
    }


def test_ror_lookup_uses_domain_lookup_after_single_keystroke_when_email_given():
    request = RequestFactory().get(
        "/login/signup/ror-organizations/",
        {"q": "R", "email": "person@rivm.nl"},
    )
    mocked_response = _mock_ror_response(
        _ror_item(
            ror_id="https://ror.org/01cesdt21",
            display_name="National Institute for Public Health and the Environment",
            country_name="The Netherlands",
            acronym="RIVM",
            labels=[("nl", "Rijksinstituut voor Volksgezondheid en Milieu")],
            website="https://www.rivm.nl",
            domain="rivm.nl",
        )
    )

    with patch(
        "toxtempass.views.requests.get", return_value=mocked_response
    ) as mocked_get:
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"][0]["name"] == (
        "National Institute for Public Health and the Environment"
    )
    mocked_get.assert_called_once()
    assert mocked_get.call_args.kwargs["params"] == {
        "query.advanced": 'links.value:"rivm.nl"'
    }


def test_ror_lookup_shows_company_names_without_their_country():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Avient"})
    mocked_response = _mock_ror_response(
        _ror_item(
            ror_id="https://ror.org/05768zd89",
            display_name="Avient Corporation (United States)",
            country_name="United States",
            labels=[("en", "Avient Corporation")],
        ),
        # No plain name in ROR: without the country it might not match any more.
        _ror_item(
            ror_id="https://ror.org/000000000",
            display_name="Avient Lab (United States)",
            country_name="United States",
        ),
    )

    with patch("toxtempass.views.requests.get", return_value=mocked_response):
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert [(item["name"], item["label"]) for item in payload["items"]] == [
        ("Avient Corporation", "Avient Corporation"),
        ("Avient Lab (United States)", "Avient Lab (United States)"),
    ]


def test_ror_lookup_deduplicates_same_name_without_id():
    request = RequestFactory().get("/login/signup/ror-organizations/", {"q": "Leiden"})
    mocked_response = Mock()
    mocked_response.raise_for_status.return_value = None
    mocked_response.json.return_value = {
        "items": [
            {"name": "Leiden University", "country": {"country_name": "Netherlands"}},
            {"name": "Leiden University", "country": {"country_name": "Netherlands"}},
        ]
    }

    with patch("toxtempass.views.requests.get", return_value=mocked_response):
        response = ror_organization_lookup(request)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["items"] == [
        {
            "name": "Leiden University",
            "label": "Leiden University",
            "id": None,
        }
    ]
