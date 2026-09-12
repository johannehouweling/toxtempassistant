"""Terms of service and license: public pages, lazy modal fragments, no page bloat."""

import pytest
from django.test import Client
from django.urls import reverse

from toxtempass.tests.fixtures.factories import PersonFactory


@pytest.mark.parametrize(
    ("url_name", "title"),
    [("terms_of_service", "Terms of Service"), ("license", "License")],
)
@pytest.mark.django_db
def test_legal_document_page(url_name, title):
    """Each document has its own page with one title, an <h1> and a canonical link."""
    url = reverse(url_name)
    html = Client().get(url).content.decode()
    assert html.count("<title>") == 1
    assert f"<title>{title} | ToxTempAssistant</title>" in html
    assert f'<h1 class="fs-3">{title}</h1>' in html
    assert f'<link rel="canonical" href="http://testserver{url}">' in html


@pytest.mark.parametrize("url_name", ["terms_of_service", "license"])
def test_legal_document_fragment_for_modal(url_name):
    """?partial=1 returns just the document, kept out of search results."""
    response = Client().get(reverse(url_name), {"partial": "1"})
    assert response.status_code == 200
    assert response["X-Robots-Tag"] == "noindex"
    body = response.content.decode()
    assert "<html" not in body
    assert "<title" not in body


@pytest.mark.django_db
def test_anonymous_pages_do_not_embed_the_documents():
    """The landing page ships neither the user menu nor its modals."""
    html = Client().get(reverse("overview")).content.decode()
    assert 'id="offcanvasUser"' not in html
    assert 'id="copyrightModal"' not in html
    assert 'id="termsModal"' not in html


@pytest.mark.django_db
def test_signup_has_one_lazy_terms_modal():
    """Signup keeps its terms modal, once, loading the text on open."""
    html = Client().get(reverse("signup")).content.decode()
    assert html.count('id="termsModal"') == 1
    assert f'data-lazy-src="{reverse("terms_of_service")}?partial=1"' in html


@pytest.mark.django_db
def test_logged_in_user_menu_modals_load_lazily():
    """Logged-in users keep both modals in the user menu, loading on open."""
    client = Client()
    client.force_login(PersonFactory.create())
    html = client.get(reverse("overview")).content.decode()
    assert 'id="offcanvasUser"' in html
    assert f'data-lazy-src="{reverse("license")}?partial=1"' in html
    assert f'data-lazy-src="{reverse("terms_of_service")}?partial=1"' in html


@pytest.mark.django_db
def test_public_footer_links_the_legal_pages():
    """Visitors and crawlers reach the terms and license through the footer."""
    html = Client().get(reverse("overview")).content.decode()
    assert f'href="{reverse("terms_of_service")}"' in html
    assert f'href="{reverse("license")}"' in html
