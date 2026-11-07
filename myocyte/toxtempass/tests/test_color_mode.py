"""Light, dark or system colour mode.

Chosen in the Settings tab (visitors: the footer menu) and applied by base.html.
"""

import pytest
from django.urls import reverse

from toxtempass import config
from toxtempass.context_processors import color_mode
from toxtempass.tests.fixtures.factories import PersonFactory

URL = "account_set_color_mode"


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_choosing_a_mode_is_remembered_and_rendered(client, mode):
    user = PersonFactory()
    client.force_login(user)
    response = client.post(reverse(URL), {"mode": mode})
    assert response.json() == {"success": True, "mode": mode}
    user.refresh_from_db()
    assert user.preferences["color_mode"] == mode

    html = client.get(reverse("overview")).content.decode()
    assert f'<html lang="en" data-color-mode="{mode}">' in html
    radio = f'name="color-mode" value="{mode}" class="js-color-mode" autocomplete="off"'
    assert radio + " checked" in html


@pytest.mark.django_db
def test_system_is_the_default_and_clears_the_choice(client):
    user = PersonFactory(preferences={"color_mode": "dark", "llm_model": "1:M0"})
    client.force_login(user)
    client.post(reverse(URL), {"mode": "system"})
    user.refresh_from_db()
    # Only the colour mode goes; other preferences stay.
    assert user.preferences == {"llm_model": "1:M0"}
    assert 'data-color-mode="system"' in client.get(reverse("overview")).content.decode()


@pytest.mark.django_db
def test_unknown_modes_are_refused(client):
    user = PersonFactory()
    client.force_login(user)
    response = client.post(reverse(URL), {"mode": "sepia"})
    assert response.status_code == 400
    user.refresh_from_db()
    assert "color_mode" not in (user.preferences or {})


@pytest.mark.django_db
def test_visitors_follow_their_system_and_cannot_save_a_mode(client):
    assert 'data-color-mode="system"' in client.get(reverse("about")).content.decode()
    response = client.post(reverse(URL), {"mode": "dark"})
    assert response.status_code == 302


@pytest.mark.django_db
def test_visitors_pick_a_mode_in_the_footer_and_it_stays_in_their_browser(client):
    html = client.get(reverse("about")).content.decode()
    footer = html.split("<footer")[1].split("</footer>")[0]
    assert 'id="id_visitor_color_mode"' in footer
    for mode in config._color_modes:
        assert f'data-mode="{mode}"' in footer
    # Kept in localStorage, not a cookie, so nothing is sent to the server.
    assert 'localStorage.getItem("color-mode")' in html
    assert "document.cookie" not in html


@pytest.mark.django_db
def test_logged_in_users_get_no_menu_and_ignore_the_browser_choice(client):
    client.force_login(PersonFactory(preferences={"color_mode": "light"}))
    html = client.get(reverse("overview")).content.decode()
    assert 'id="id_visitor_color_mode"' not in html
    assert "localStorage" not in html.split("</head>")[0]


@pytest.mark.django_db
def test_a_stale_stored_mode_falls_back_to_system(rf):
    request = rf.get("/")
    request.user = PersonFactory(preferences={"color_mode": "sepia"})
    assert color_mode(request)["color_mode"] == "system"


def test_the_switch_offers_exactly_the_allowed_modes(rf):
    request = rf.get("/")
    request.user = None
    choices = color_mode(request)["color_mode_choices"]
    assert tuple(value for value, _, _ in choices) == config._color_modes
