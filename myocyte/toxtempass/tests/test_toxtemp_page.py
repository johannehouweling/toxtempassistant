"""Public ToxTemp page: Krebs et al. credited first, every question listed, linked."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from toxtempass.templatetags.extras import _toxtemp_sections, guidance_paragraphs


def _all_questions():
    for section in _toxtemp_sections():
        for sub in section["subsections"]:
            yield sub["question"]
            for subquestion in sub.get("subquestions", []):
                yield subquestion["question"]


@pytest.fixture
def html(db):
    return Client().get(reverse("toxtemp_questions")).content.decode()


def test_page_head_metadata(html):
    """One title, one <h1>, and a canonical link."""
    assert html.count("<title>") == 1
    assert html.count("<h1") == 1
    url = reverse("toxtemp_questions")
    assert f'<link rel="canonical" href="http://testserver{url}">' in html


def test_krebs_et_al_are_credited_before_any_question(html):
    """Source and license come first, so nobody mistakes the template for ours."""
    first_question = escape(next(_all_questions()))
    doi = "https://doi.org/10.14573/altex.1909271"
    assert html.index('id="source"') < html.index(first_question)
    assert html.index(doi) < html.index(first_question)
    assert "It is not the work of the ToxTempAssistant team." in html
    assert "https://creativecommons.org/licenses/by/4.0/" in html


def test_every_question_and_note_is_listed(html):
    """All 77 questions (70 plus 7 abstract sub-questions) and their notes render."""
    questions = list(_all_questions())
    assert len(questions) == 77
    for question in questions:
        assert escape(question) in html
    for section in _toxtemp_sections():
        for sub in section["subsections"]:
            for paragraph in guidance_paragraphs(sub["title"]):
                assert escape(paragraph) in html


def test_page_is_linked_from_about_landing_and_sitemap(db):
    """Crawlers reach the page from the about page, the landing page and the sitemap."""
    url = reverse("toxtemp_questions")
    client = Client()
    assert f'href="{url}"' in client.get(reverse("about")).content.decode()
    assert f'href="{url}"' in client.get(reverse("overview")).content.decode()
    sitemap = client.get("/sitemap.xml").content.decode()
    assert f"<loc>http://testserver{url}</loc>" in sitemap
