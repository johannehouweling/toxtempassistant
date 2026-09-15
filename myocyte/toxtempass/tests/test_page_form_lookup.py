"""Page scripts must find the page's own form, not one in the user menu.

The user menu is rendered before the page content and has forms of its own. A
script that takes the first form on the page then attaches to the menu, and the
page's form posts without JavaScript: the browser shows the JSON reply.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse

from toxtempass.tests.fixtures.factories import PersonFactory

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


class _Forms(HTMLParser):
    """Collect the form tags, noting which sit inside the user menu."""

    def __init__(self):
        super().__init__()
        self.div_depth = 0
        self.menu_depth = None
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            if attrs.get("id") == "offcanvasUser":
                self.menu_depth = self.div_depth
        elif tag == "form":
            self.forms.append((attrs, self.menu_depth is not None))

    def handle_endtag(self, tag):
        if tag == "div":
            if self.menu_depth == self.div_depth:
                self.menu_depth = None
            self.div_depth -= 1


def test_no_template_script_takes_the_first_form_on_the_page():
    first_form = re.compile(r"""querySelector\(\s*['"]form['"]\s*\)""")
    offenders = [
        str(path.relative_to(TEMPLATES))
        for path in TEMPLATES.rglob("*.html")
        if first_form.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name", ["add_new", "create_investigation", "create_study", "create_assay"]
)
def test_page_form_is_the_first_form_outside_the_user_menu(client, url_name):
    # A superuser skips the beta gate, which would redirect /add/.
    client.force_login(PersonFactory(is_superuser=True))
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    parser = _Forms()
    parser.feed(response.content.decode())

    in_menu = [attrs for attrs, inside in parser.forms if inside]
    outside = [attrs for attrs, inside in parser.forms if not inside]
    assert in_menu, "the user menu has forms, so taking the first form would be wrong"
    assert outside[0].get("method") == "post"
