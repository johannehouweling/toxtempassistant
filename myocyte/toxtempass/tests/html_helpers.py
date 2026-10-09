"""Helpers for reading the HTML a view returns.

Use these instead of a regular expression such as ``<script>(.*?)</script>``: a
regex written for lowercase, tidy tags misses ``<SCRIPT>`` and ``</script >``, and
CodeQL rightly flags it. The parser below follows the HTML rules instead.
"""

from html.parser import HTMLParser


class _ScriptCollector(HTMLParser):
    """Collect the attributes and text of every ``<script>`` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self._attrs: dict[str, str | None] | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._attrs, self._parts = dict(attrs), []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._attrs is not None:
            self.scripts.append((self._attrs, "".join(self._parts)))
            self._attrs = None

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._parts.append(data)


def script_blocks(html: str, type_: str | None = None) -> list[str]:
    """Return the text of each ``<script>`` in ``html``, optionally of one ``type``."""
    collector = _ScriptCollector()
    collector.feed(html)
    collector.close()
    return [
        text
        for attrs, text in collector.scripts
        if type_ is None or attrs.get("type") == type_
    ]
