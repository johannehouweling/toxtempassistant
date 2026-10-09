"""The script reader must not miss the tag spellings a naive regex would."""

from toxtempass.tests.html_helpers import script_blocks


def test_finds_scripts_whatever_their_case_or_spacing():
    html = (
        "<p>x</p><script>one()</script>"
        "<SCRIPT>two()</SCRIPT>"
        '<Script type="text/javascript" defer>three()</script >'
    )
    assert script_blocks(html) == ["one()", "two()", "three()"]


def test_keeps_markup_like_text_inside_a_script():
    html = "<script>const a = '<b>not a tag</b>'; if (1 < 2) {}</script><p>after</p>"
    assert script_blocks(html) == ["const a = '<b>not a tag</b>'; if (1 < 2) {}"]


def test_can_pick_one_type():
    html = (
        '<script type="application/ld+json">{"a": 1}</script>'
        "<script>plain()</script>"
        '<SCRIPT TYPE="application/ld+json">{"b": 2}</SCRIPT>'
    )
    assert script_blocks(html, "application/ld+json") == ['{"a": 1}', '{"b": 2}']


def test_a_page_without_scripts_has_none():
    assert script_blocks("<p>hello</p>") == []
