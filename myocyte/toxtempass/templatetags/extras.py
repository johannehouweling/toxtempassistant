import json
import re
from functools import lru_cache

from django import template
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Split guidance text before each explicit Note/Example marker, so each
# annotation becomes its own paragraph in the help popover.
_NE_SPLIT = re.compile(r"(?=Note[:\s]|Examples?[:\s]|Example problem|Example \d)")


@register.filter()
def cost(amount: object, unit: str = "") -> str:
    """Format a money amount, e.g. ``{{ total|cost:"Eur" }}`` -> ``€1.23``.

    See :func:`toxtempass.costs.format_cost`; ``unit`` may be a currency code
    or a symbol.
    """
    from toxtempass.costs import format_cost

    return format_cost(amount, unit)


@register.filter(name="getattr")
def getattr_custom(obj: object, attr_name: str) -> object:
    """Get an attribute dynamically from an object."""
    return getattr(obj, attr_name, None)


@register.filter()
def form_field(form: object, field_name: str) -> object:
    """Filter form by field name.

    Usage: {{ form|form_field:"field_name" }}
    """
    return form[field_name]


@register.filter()
def add_asstring(a: str, b: object) -> str:
    """Concatenate a string with another value.

    Converting the value to a string if necessary.
    Usage: {{ a|add_asstring:b }}
    """
    return a + str(b)


@register.filter()
def intdivperc(a: float, b: float) -> int:
    """Calculate the percentage of a divided by b."""
    if b != 0:
        return int(a / b * 100)
    else:
        return 0


@lru_cache(maxsize=1)
def _toxtemp_sections() -> tuple[dict, ...]:
    """Load ToxTemp sections (questions, sub-questions, guidance) from ToxTemp_v1.json.

    Cached for the process lifetime; empty when the seed file is missing or invalid.
    """
    try:
        data = json.loads((settings.BASE_DIR / "ToxTemp_v1.json").read_text())
    except (FileNotFoundError, ValueError, OSError):
        return ()
    return tuple(data.get("sections", []))


@lru_cache(maxsize=1)
def _question_guidance() -> dict:
    """Map section/subsection title -> ToxTemp guidance from the ToxTemp_v1.json seed.

    The guidance is the ToxTemp supplementary text (Krebs et al. 2019), stored on
    each section's and subsection's ``guidance`` key in ToxTemp_v1.json. Cached
    for the process lifetime.
    """
    out: dict = {}
    for section in _toxtemp_sections():
        section_guidance = (section.get("guidance") or "").strip()
        if section_guidance:
            out[(section.get("title") or "").strip()] = section_guidance
        for sub in section.get("subsections", []):
            guidance = (sub.get("guidance") or "").strip()
            if guidance:
                out[(sub.get("title") or "").strip()] = guidance
    return out


@register.simple_tag
def toxtemp_sections() -> tuple[dict, ...]:
    """Return the ToxTemp sections, for pages that list the whole template.

    Usage: {% toxtemp_sections as sections %}
    """
    return _toxtemp_sections()


@register.filter()
def guidance_paragraphs(title: object) -> list[str]:
    """Split the ToxTemp guidance for a section/subsection title into Note/Example blocks.

    Empty list when there is no guidance.
    Usage: {% for paragraph in subsection.title|guidance_paragraphs %}
    """
    text = _question_guidance().get((str(title).strip() if title else ""), "")
    return [p.strip() for p in _NE_SPLIT.split(text) if p.strip()]


@register.filter()
def question_help(title: object) -> str:
    """Return the ToxTemp guidance for a section/subsection title as safe HTML.

    One ``<p>`` per Note/Example block; empty string when there is no guidance
    (so the template hides the help icon). The text is escaped — only the wrapper
    markup is trusted. Rendered inside the Bootstrap guidance popover.
    Usage: {{ subsection.title|question_help }}
    """
    parts = guidance_paragraphs(title)
    if not parts:
        return ""
    body = "".join(f"<p class='mb-2'>{escape(p)}</p>" for p in parts)
    return mark_safe(  # noqa: S308 - dynamic text is escaped; wrapper markup is static
        "<div class='small text-start overflow-auto' style='max-height:60vh'>"
        f"{body}</div>"
    )
