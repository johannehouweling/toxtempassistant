"""Tests for the tabbed user menu (the offcanvas opened from the header)."""

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse

from toxtempass.models import LLMConfig
from toxtempass.tests.fixtures.factories import PersonFactory

pytestmark = pytest.mark.django_db

PANES = ("userMenuWorkspaces", "userMenuAccount", "userMenuSettings", "userMenuPrivacy")


def _menu(client, user, models=None, allowed=None):
    """Return the page HTML from the user menu onwards, optionally with fake models.

    ``allowed`` lists the fake model numbers the admin ticked; all of them by default.
    """
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
        llm_config = LLMConfig.load()
        llm_config.allowed_models = [
            f"1:M{i}" for i in (range(models) if allowed is None else allowed)
        ]
        llm_config.save()
        with patch("toxtempass.azure_registry.get_registry", return_value=registry):
            html = client.get(reverse("beta_wait")).content.decode()
    return html[html.index('id="offcanvasUser"') :]


def _pane(menu, name):
    """Return one tab pane of the menu: from its id to the next pane or the footer."""
    start = menu.index(f'id="{name}"')
    ends = [
        menu.index(f'id="{other}"')
        for other in PANES
        if f'id="{other}"' in menu and menu.index(f'id="{other}"') > start
    ]
    return menu[start : min(ends, default=menu.index('class="border-top px-3 pt-3"'))]


def test_menu_opens_on_workspaces_and_puts_privacy_in_its_own_tab(client):
    menu = _menu(client, PersonFactory())

    assert 'class="nav-link active px-2" id="userMenuWorkspacesTab"' in menu
    assert "New Workspace" in _pane(menu, "userMenuWorkspaces")
    account = _pane(menu, "userMenuAccount")
    assert 'class="js-profile"' in account
    assert "Delete account" in account
    privacy = _pane(menu, "userMenuPrivacy")
    assert "Email notifications" in privacy
    assert "Shared documents" in privacy
    assert "is no longer used to improve ToxTempAssistant" in privacy
    assert "Email notifications" not in account


def test_tabs_are_workspaces_settings_account_privacy(client):
    menu = _menu(client, PersonFactory(is_staff=True))
    tabs = [
        "userMenuWorkspacesTab",
        "userMenuSettingsTab",
        "userMenuAccountTab",
        "userMenuPrivacyTab",
    ]
    positions = [menu.index(f'id="{tab}"') for tab in tabs]
    assert positions == sorted(positions)


def test_settings_tab_only_appears_when_it_has_something_to_show(client):
    menu = _menu(client, PersonFactory(), models=1)
    assert 'id="userMenuSettingsTab"' not in menu

    staff_menu = _menu(client, PersonFactory(is_staff=True), models=1)
    assert 'id="userMenuSettingsTab"' in staff_menu
    assert "KPI dashboard" in _pane(staff_menu, "userMenuSettings")
    assert "KPI dashboard" not in menu


@pytest.mark.parametrize(("models", "shown"), [(1, False), (2, True)])
def test_model_choice_only_appears_with_a_real_choice(client, models, shown):
    menu = _menu(client, PersonFactory(), models=models)
    assert ('id="llm-model-select"' in menu) is shown
    if shown:
        assert 'id="llm-model-select"' in _pane(menu, "userMenuSettings")


def test_users_only_get_the_models_the_admin_ticked(client):
    no_choice = _menu(client, PersonFactory(), models=3, allowed=[])
    assert 'id="llm-model-select"' not in no_choice

    two_ticked = _menu(client, PersonFactory(), models=3, allowed=[0, 2])
    assert 'value="1:M0"' in two_ticked
    assert 'value="1:M1"' not in two_ticked
    assert 'value="1:M2"' in two_ticked


def test_unconfirmed_address_is_flagged_on_the_account_tab(client):
    unconfirmed_menu = _menu(client, PersonFactory(email_confirmed_at=None))
    assert 'aria-label="Email address not confirmed"' in unconfirmed_menu
    account = _pane(unconfirmed_menu, "userMenuAccount")
    assert "Not confirmed" in account
    assert "js-resend-confirmation" in account

    confirmed_menu = _menu(client, PersonFactory())
    assert 'aria-label="Email address not confirmed"' not in confirmed_menu
    assert "js-resend-confirmation" not in confirmed_menu


def test_pending_email_change_is_shown_with_a_cancel_button(client):
    menu = _menu(client, PersonFactory(pending_email="new@example.org"))
    account = _pane(menu, "userMenuAccount")
    assert "new@example.org" in account
    assert "Cancel this change" in account


def test_linked_orcid_icon_opens_the_record_but_the_id_is_plain_text(client):
    orcid_id = "0000-0002-1825-0097"
    account = _pane(_menu(client, PersonFactory(orcid_id=orcid_id)), "userMenuAccount")

    assert f'href="https://orcid.org/{orcid_id}"' in account
    assert f">{orcid_id}</a>" not in account
    assert f"<span>{orcid_id}</span>" in account
    assert "Link ORCID" not in account
    # The iD is a row under the email address, with a small unlink link beside it.
    row = account.index(f"<span>{orcid_id}</span>")
    assert account.index("bi-envelope") < row < account.index("Change email address")
    assert f'data-url="{reverse("account_unlink_orcid")}"' in account[row:]


def test_account_tab_lists_who_you_are_then_buttons_without_headings(client):
    account = _pane(_menu(client, PersonFactory(orcid_id=None)), "userMenuAccount")
    assert "<h5" not in account  # each row's value or button says what it is
    order = [
        'class="js-profile"',
        "bi-envelope",
        "Link ORCID",
        "Change email address",
        "Change password",
        "Delete account</button>",
    ]
    positions = [account.index(item) for item in order]
    assert positions == sorted(positions)
    assert "Delete account…" not in account


def test_link_orcid_spans_the_tab_like_the_other_buttons(client):
    account = _pane(_menu(client, PersonFactory(orcid_id=None)), "userMenuAccount")
    button = (
        r'<div class="d-grid mb-2">\s*<a class="btn btn-sm btn-outline-secondary d-flex '
        r'align-items-center justify-content-center" '
        f'href="{re.escape(reverse("orcid_login"))}">'
    )
    assert re.search(button, account)


def test_not_in_ror_checkbox_is_hidden_in_the_account_tab(client):
    account = _pane(_menu(client, PersonFactory()), "userMenuAccount")
    assert '<div class="form-check mt-1 js-not-in-ror" hidden>' in account
    assert 'My organization is not in <a href="https://ror.org"' in account


def test_organization_errors_show_between_the_field_and_the_not_in_ror_box(client):
    account = _pane(_menu(client, PersonFactory()), "userMenuAccount")
    organization = account.index('id="account-organization"')
    error = account.index("js-organization-error")
    checkbox = account.index("js-not-in-ror")
    assert organization < error < checkbox


def test_your_details_are_text_to_click_and_edit_without_a_save_button(client):
    user = PersonFactory(first_name="Ada", last_name="Lovelace", organization="RIVM")
    account = _pane(_menu(client, user), "userMenuAccount")
    start = account.index('class="js-profile"')
    details = account[start : account.index("</form>", start)]

    assert '<span class="js-name-value">Ada Lovelace</span>' in details
    assert '<span class="js-organization-value">RIVM</span>' in details
    assert 'aria-label="Edit name"' in details
    assert 'aria-label="Edit organization"' in details
    assert '<div class="row g-2 js-profile-inputs" hidden>' in details
    assert "Save</button>" not in details


def test_ror_match_is_a_badge_naming_the_record_on_hover(client):
    matched = PersonFactory(
        organization="Avient",
        ror_id="https://ror.org/00example",
        ror_name="Avient Corporation (United States)",
        ror_checked_organization="Avient",
    )
    account = _pane(_menu(client, matched), "userMenuAccount")
    assert (
        'data-bs-toggle="tooltip" title="Matched to Avient Corporation (United States) '
        'in the Research Organization Registry">ROR matched</span>'
    ) in account
    assert "Matched to <span" not in account  # no sentence under the field any more

    unmatched = PersonFactory(organization="Avient", ror_checked_organization="Avient")
    account = _pane(_menu(client, unmatched), "userMenuAccount")
    assert ' hidden>ROR matched</span>' in account


def test_footer_icons_are_in_order_and_each_has_a_tooltip(client):
    from toxtempass import config

    menu = _menu(client, PersonFactory())
    start = menu.index('class="border-top px-3 pt-3"')
    footer = menu[start : menu.index("<!-- Modal -->")]
    top_row, icons = footer.split('class="btn-group"', 1)

    assert "If useful, please cite" not in footer
    assert f'href="{reverse("about")}"' not in top_row  # About is one of the icons
    titles = ["GitHub", "About", "How to cite", "Contact via Email", "Legal", "License"]
    positions = [icons.index(f'title="{title}" aria-label="{title}"') for title in titles]
    assert positions == sorted(positions)
    assert icons.count("js-footer-tooltip") == len(titles)
    assert (
        f'href="{config.github_repo_url}" target="_blank" rel="noopener noreferrer"'
    ) in icons
