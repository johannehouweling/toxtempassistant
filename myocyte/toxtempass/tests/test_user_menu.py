"""Tests for the tabbed user menu (the offcanvas opened from the header)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse

from toxtempass.tests.fixtures.factories import PersonFactory

pytestmark = pytest.mark.django_db


def _menu(client, user, models=None):
    """Return the page HTML from the user menu onwards, optionally with fake models."""
    client.force_login(user)
    if models is None:
        html = client.get(reverse("beta_wait")).content.decode()
    else:
        registry = [
            SimpleNamespace(
                index=1,
                models=[
                    SimpleNamespace(
                        tag=f"M{i}", model_id=f"model-{i}", retirement_status="active"
                    )
                    for i in range(models)
                ],
            )
        ]
        with patch("toxtempass.azure_registry.get_registry", return_value=registry):
            html = client.get(reverse("beta_wait")).content.decode()
    return html[html.index('id="offcanvasUser"') :]


def test_menu_opens_on_workspaces_with_settings_in_a_second_tab(client):
    menu = _menu(client, PersonFactory())

    workspaces, account = menu.split('id="userMenuAccount"', 1)
    assert 'class="nav-link active" id="userMenuWorkspacesTab"' in menu
    assert "New Workspace" in workspaces
    assert "Email notifications" not in workspaces
    assert "Email notifications" in account
    # The footer comes after both tabs, so it shows whichever tab is open.
    assert "Guided Tour" in account
    assert "Logout" in account


@pytest.mark.parametrize(("models", "shown"), [(1, False), (2, True)])
def test_model_choice_only_appears_with_a_real_choice(client, models, shown):
    menu = _menu(client, PersonFactory(), models=models)
    assert ('id="llm-model-select"' in menu) is shown


def test_unconfirmed_address_is_flagged_on_the_account_tab(client):
    unconfirmed_menu = _menu(client, PersonFactory(email_confirmed_at=None))
    assert 'aria-label="Email address not confirmed"' in unconfirmed_menu
    account = unconfirmed_menu.split('id="userMenuAccount"', 1)[1]
    assert "Not confirmed" in account
    assert "js-resend-confirmation" in account

    confirmed_menu = _menu(client, PersonFactory())
    assert 'aria-label="Email address not confirmed"' not in confirmed_menu
    assert "js-resend-confirmation" not in confirmed_menu
