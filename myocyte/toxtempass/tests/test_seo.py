"""Crawler-facing behaviour: landing page at "/", robots.txt, sitemap.xml, favicon."""

import pytest
from django.test import Client
from django.urls import reverse

from toxtempass.tests.fixtures.factories import PersonFactory


@pytest.mark.django_db
def test_anonymous_root_serves_landing_page_without_redirect():
    """Crawlers must get the landing page at "/" itself, not a 302 to /login/."""
    response = Client().get(reverse("overview"))
    assert response.status_code == 200
    assert "login.html" in [t.name for t in response.templates]
    # The login form must post to the login view, not back to "/".
    assert f'action="{reverse("login")}"' in response.content.decode()


@pytest.mark.django_db
def test_landing_page_head_metadata():
    """One title, a description, a canonical link to "/", and structured data."""
    html = Client().get(reverse("overview")).content.decode()
    assert html.count("<title>") == 1
    assert html.count("<h1") == 1
    assert '<meta name="description"' in html
    assert '<link rel="canonical" href="http://testserver/">' in html
    assert "application/ld+json" in html


@pytest.mark.django_db
def test_login_page_is_canonicalised_to_root():
    """/login/ serves the same content, so it must point search engines at "/"."""
    html = Client().get(reverse("login")).content.decode()
    assert '<link rel="canonical" href="http://testserver/">' in html


@pytest.mark.django_db
def test_authenticated_root_still_shows_overview():
    """Logged-in users keep getting their assay overview at "/"."""
    client = Client()
    client.force_login(PersonFactory.create())
    response = client.get(reverse("overview"))
    assert response.status_code == 200
    assert "toxtempass/overview.html" in [t.name for t in response.templates]


def test_robots_txt():
    """robots.txt keeps crawlers out of the app and advertises the sitemap."""
    response = Client().get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    body = response.content.decode()
    assert "Disallow: /admin/" in body
    assert "Disallow: /login/\n" not in body
    assert "Sitemap: http://testserver/sitemap.xml" in body


def test_sitemap_lists_public_pages():
    """The sitemap lists the landing page and the signup page."""
    response = Client().get("/sitemap.xml")
    assert response.status_code == 200
    body = response.content.decode()
    assert "<loc>http://testserver/</loc>" in body
    assert f"<loc>http://testserver{reverse('signup')}</loc>" in body


@pytest.mark.django_db
def test_link_preview_image_exists_at_1200_by_630():
    """og:image points at a real 1200x630 file, the size link previews expect."""
    from django.contrib.staticfiles import finders
    from PIL import Image

    html = Client().get(reverse("overview")).content.decode()
    assert "toxtempass/img/og-image.jpg" in html
    with Image.open(finders.find("toxtempass/img/og-image.jpg")) as image:
        assert image.size == (1200, 630)


@pytest.mark.django_db
def test_signup_page_has_heading_and_description():
    """Signup is in the sitemap, so it needs its own <h1> and meta description."""
    html = Client().get(reverse("signup")).content.decode()
    assert html.count("<h1") == 1
    assert "Create a free ToxTempAssistant beta account" in html


def test_favicon_redirects_to_static_file():
    """/favicon.ico is requested by convention and must not 404."""
    response = Client().get("/favicon.ico")
    assert response.status_code == 301
    assert response["Location"].endswith("favicon.ico")
