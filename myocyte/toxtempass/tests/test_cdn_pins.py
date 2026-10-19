"""CDN files loaded with an integrity hash must name an exact version."""

import re
from pathlib import Path

MYOCYTE = Path(__file__).resolve().parents[2]
TAG = re.compile(r"<(?:link|script)\b[^>]*>", re.S)
URL = re.compile(r'(?:href|src)="([^"]+)"')


def test_integrity_checked_cdn_urls_pin_an_exact_version():
    """A range such as bootstrap-icons@1 moves to each new release, the hash stops
    matching, and the browser silently drops the file (all icons vanished once)."""
    pages = [
        *(MYOCYTE / "toxtempass" / "templates").rglob("*.html"),
        *(MYOCYTE / "dockerfiles" / "nginx" / "error_pages").glob("*.html"),
    ]
    urls = [
        URL.search(tag).group(1)
        for page in pages
        for tag in TAG.findall(page.read_text())
        if "integrity=" in tag
    ]
    assert urls
    assert [u for u in urls if not re.search(r"@\d+\.\d+\.\d+/", u)] == []
