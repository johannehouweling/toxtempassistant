"""Public about page: head metadata, FAQ structured data, and links to the page."""

import json
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from toxtempass import config


@pytest.mark.django_db
def test_about_page_renders_for_anonymous_visitors():
    """The page is public, with one title, one <h1> and a canonical link."""
    response = Client().get(reverse("about"))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("<title>") == 1
    assert html.count("<h1") == 1
    assert f'<link rel="canonical" href="http://testserver{reverse("about")}">' in html


@pytest.mark.django_db
def test_about_faq_structured_data_matches_visible_faq():
    """FAQPage data must mirror the visible FAQ, or search engines ignore it."""
    html = Client().get(reverse("about")).content.decode()
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(json.loads(block) for block in blocks if "FAQPage" in block)
    entries = [(q["name"], q["acceptedAnswer"]["text"]) for q in faq["mainEntity"]]
    assert entries == list(config._about_faq)
    for question, answer in config._about_faq:
        assert escape(question) in html
        assert escape(answer) in html


@pytest.mark.django_db
def test_about_is_linked_from_landing_page_header_and_sitemap():
    """Crawlers find the page from the landing page, the header and the sitemap."""
    about = reverse("about")
    landing = Client().get(reverse("overview")).content.decode()
    assert landing.count(f'href="{about}"') == 3  # header button, About card, footer
    sitemap = Client().get("/sitemap.xml").content.decode()
    assert f"<loc>http://testserver{about}</loc>" in sitemap


@pytest.mark.django_db
def test_about_demo_video_is_embedded_from_peertube_with_video_data():
    """The video comes from PeerTube, not YouTube, and is described as a VideoObject."""
    embed_url = "https://video.edu.nl/videos/embed/mXiqzVCSYytLb4i2YgT7a6"
    html = Client().get(reverse("about")).content.decode()
    assert f'src="{embed_url}"' in html
    assert "youtube.com/embed" not in html
    assert "youtube-nocookie.com" not in html
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    video = next(json.loads(block) for block in blocks if "VideoObject" in block)
    assert video["embedUrl"] == embed_url
    assert video["uploadDate"] and video["thumbnailUrl"] and video["duration"]


@pytest.mark.django_db
def test_about_page_header_links_to_login_instead_of_itself():
    """On /about/ the header button leads to login and signup, not back to /about/."""
    html = Client().get(reverse("about")).content.decode()
    header = html.split("</header>")[0]
    assert f'href="{reverse("overview")}">Sign up / Log in</a>' in header
    assert f'href="{reverse("about")}">About</a>' not in header


@pytest.mark.django_db
def test_about_page_does_not_publish_the_maintainer_email():
    """The address stays off public pages; logged-in users find it in the user menu."""
    html = Client().get(reverse("about")).content.decode()
    assert config.maintainer_email not in html


@pytest.mark.django_db
def test_how_to_cite_also_credits_the_toxtemp_authors():
    """Besides our paper, one short sentence asks to also consider citing ToxTemp."""
    html = Client().get(reverse("about")).content.decode()
    section = html.split('id="how-to-cite"', 1)[1].split("</section>", 1)[0]
    assert f'href="{config.reference_toxtempassistant_paper}"' in section
    assert f'href="{config.reference_toxtemp}"' in section
    assert "Please also consider citing" in section
    assert "ToxTemp (Krebs et al., 2019)</a>" in section


@pytest.mark.django_db
def test_landing_page_citation_request_links_to_how_to_cite():
    """The request for a citation on the landing page points to the full guidance."""
    landing = Client().get(reverse("overview")).content.decode()
    paragraph = landing.split("we would appreciate a citation", 1)[1].split("</p>", 1)[0]
    assert f'href="{reverse("about")}#how-to-cite">How to cite</a>' in paragraph


@pytest.mark.django_db
def test_about_names_the_projects_using_toxtemp_with_links():
    """VHP4Safety, RISK-HUNT3R and ONTOX are all listed as ToxTemp users, linked."""
    html = Client().get(reverse("about")).content.decode()
    sentence = html.split("used by projects such as", 1)[1].split("Filling it in", 1)[0]
    for url, name in (
        ("https://vhp4safety.nl/", "VHP4Safety"),
        ("https://www.risk-hunt3r.eu/", "RISK-HUNT3R"),
        ("https://ontox-project.eu/", "ONTOX"),
    ):
        link = f'href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>'
        assert link in sentence

