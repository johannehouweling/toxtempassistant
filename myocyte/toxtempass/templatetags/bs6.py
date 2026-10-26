"""Bootstrap 6 form tags, standing in for django-bootstrap5 until django-bootstrap6.

``{% load bs6 %}`` offers ``bootstrap_form``, ``bootstrap_field`` and
``bootstrap_button`` with the names and arguments django-bootstrap5 used, so a
template moves to a future django-bootstrap6 by changing its ``{% load %}`` line
(and nothing else, if that package keeps the same API).

Only the options our templates use are supported. Anything else raises
``TemplateSyntaxError`` when the template renders, rather than being ignored and
rendering the wrong markup. Differences from django-bootstrap5:

* Valid fields of a bound form get no ``is-valid`` class; success styling is
  opt-in in Bootstrap 6 and we never asked for it.
* Radio and multiple-choice widgets are not supported yet (no template uses them).
* A widget that sets ``renders_own_label = True`` (``BootstrapSelectWithButtons
  Widget``) is placed as it is, without a label of ours or a floating wrapper.
* ``bootstrap_button`` writes ``button_class`` as given, without adding ``btn``;
  Bootstrap 6 variants (``btn-solid``, ``btn-outline`` …) are complete classes.

The markup follows Bootstrap 6.0.0-alpha.1 (``.form-field``, ``.check``,
``.switch``, floating label before its control).
"""

from django import template
from django.forms import (
    BoundField,
    CheckboxInput,
    CheckboxSelectMultiple,
    MultiWidget,
    RadioSelect,
    Select,
)
from django.forms.widgets import Input, Textarea
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

FIELD_OPTIONS = {"layout", "show_label", "placeholder", "wrapper_class"}
FORM_OPTIONS = {"layout", "show_label", "exclude"}
LAYOUTS = {"", "floating"}
# Bootstrap 6 spacer 5 is 1rem, the gap django-bootstrap5's default ``mb-3`` gave.
DEFAULT_WRAPPER_CLASS = "mb-5"
TEXT_INPUT_TYPES = {"text", "email", "url", "tel", "number", "password", "search"}
FLOATING_INPUT_TYPES = TEXT_INPUT_TYPES | {"date", "time", "datetime-local"}


def _check_options(tag: str, given: dict, allowed: set[str]) -> None:
    unknown = set(given) - allowed
    if unknown:
        raise template.TemplateSyntaxError(
            f"{tag} does not support {', '.join(sorted(unknown))} (see toxtempass/"
            "templatetags/bs6.py)."
        )
    if given.get("layout", "") not in LAYOUTS:
        raise template.TemplateSyntaxError(f"{tag}: unknown layout {given['layout']!r}.")


def _merge(*classes: str) -> str:
    seen = []
    for name in " ".join(c for c in classes if c).split():
        if name not in seen:
            seen.append(name)
    return " ".join(seen)


def _input_type(widget: object) -> str | None:
    return widget.input_type if isinstance(widget, Input) else None


def _is_switch(widget: object) -> bool:
    return isinstance(widget, CheckboxInput) and widget.attrs.get("role") == "switch"


def _can_float(widget: object) -> bool:
    if isinstance(widget, Textarea):
        return True
    if isinstance(widget, Select):
        return not widget.allow_multiple_selected
    return _input_type(widget) in FLOATING_INPUT_TYPES


def _takes_placeholder(widget: object) -> bool:
    return isinstance(widget, Textarea) or _input_type(widget) in TEXT_INPUT_TYPES


def _widget_class(widget: object) -> str:
    if isinstance(widget, CheckboxInput):
        # A switch's look is on its ``.switch`` wrapper; the input carries no class.
        return "" if _is_switch(widget) else "check"
    if _input_type(widget) == "color":
        return "form-control form-control-color"
    return "form-control"


def render_field(
    field: BoundField,
    layout: str = "",
    show_label: bool = True,
    placeholder: str | None = None,
    wrapper_class: str = DEFAULT_WRAPPER_CLASS,
) -> SafeString:
    """Return the Bootstrap 6 markup for one bound field."""
    if field.is_hidden:
        return mark_safe(str(field))  # noqa: S308 - Django's own widget HTML
    widget = field.field.widget
    if isinstance(widget, (RadioSelect, CheckboxSelectMultiple, MultiWidget)):
        raise template.TemplateSyntaxError(
            f"bootstrap_field: {type(widget).__name__} ({field.name}) is not supported "
            "by toxtempass/templatetags/bs6.py yet."
        )

    attrs = {}
    css = _merge(widget.attrs.get("class", ""), _widget_class(widget))
    if field.errors:
        css = _merge(css, "is-invalid")
    if css:
        attrs["class"] = css
    if _is_switch(widget):
        attrs["switch"] = True

    own_label = getattr(widget, "renders_own_label", False)
    floating = layout == "floating" and _can_float(widget) and not own_label
    if _takes_placeholder(widget) and "placeholder" not in widget.attrs:
        text = field.label if placeholder is None else placeholder
        # A floating label shows while the placeholder is shown, so it needs one.
        if text or floating:
            attrs["placeholder"] = strip_tags(str(text or field.label))

    if isinstance(widget, CheckboxInput):
        kind = "switch" if _is_switch(widget) else "check"
    elif own_label:
        kind = "own_label"
    elif floating:
        kind = "floating"
    else:
        kind = "default"

    return render_to_string(
        "bs6/field.html",
        {
            "field": field,
            "kind": kind,
            "widget_html": field.as_widget(attrs=attrs),
            "label_class": "" if show_label else "visually-hidden",
            "wrapper_class": _merge("form-field", wrapper_class),
        },
    )


@register.simple_tag
def bootstrap_field(field: BoundField, **options: object) -> SafeString:
    """Render one field: ``{% bootstrap_field form.title layout="floating" %}``."""
    _check_options("bootstrap_field", options, FIELD_OPTIONS)
    if not isinstance(field, BoundField):
        raise template.TemplateSyntaxError("bootstrap_field needs a bound form field.")
    return render_field(field, **options)


@register.simple_tag
def bootstrap_form(form: object, exclude: str = "", **options: object) -> SafeString:
    """Render a form's non-field errors and its fields, skipping ``exclude``."""
    _check_options("bootstrap_form", {"exclude": exclude, **options}, FORM_OPTIONS)
    skip = {name.strip() for name in exclude.split(",") if name.strip()}
    errors = render_to_string("bs6/form_errors.html", {"errors": form.non_field_errors()})
    fields = "".join(
        render_field(field, **options) for field in form if field.name not in skip
    )
    return mark_safe(errors + fields)  # noqa: S308 - both parts rendered and escaped above


@register.simple_tag
def bootstrap_button(
    content: str,
    button_type: str = "submit",
    button_class: str = "btn-solid theme-primary",
    href: str = "",
    **attrs: object,
) -> SafeString:
    """Render a ``<button>``, or an ``<a role="button">`` for ``button_type="link"``.

    Extra keyword arguments become attributes (``form="QuestionsForm"``).
    """
    if button_type not in {"submit", "reset", "button", "link"}:
        raise template.TemplateSyntaxError(
            f"bootstrap_button: unknown type {button_type!r}."
        )
    return render_to_string(
        "bs6/button.html",
        {
            "content": content,
            "button_type": button_type,
            "button_class": button_class,
            "href": href,
            "attrs": attrs,
        },
    )
