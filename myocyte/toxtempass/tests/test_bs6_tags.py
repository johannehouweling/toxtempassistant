"""The bs6 form tags render Bootstrap 6 markup and refuse options they don't know."""

import pytest
from django import forms
from django.template import Context, Template, TemplateSyntaxError

from toxtempass.widgets import BootstrapSelectWithButtonsWidget


class SampleForm(forms.Form):
    title = forms.CharField(help_text="A <b>short</b> title.")
    notes = forms.CharField(widget=forms.Textarea, required=False)
    kind = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
    agree = forms.BooleanField(label="I agree", required=False)
    accepted = forms.BooleanField(
        required=False, widget=forms.CheckboxInput(attrs={"role": "switch"})
    )
    secret = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self):
        raise forms.ValidationError("Something is off.")


def render(source: str, **context) -> str:
    return Template("{% load bs6 %}" + source).render(Context(context))


def test_default_field_has_label_control_and_help():
    html = render("{% bootstrap_field form.title %}", form=SampleForm())
    assert '<div class="form-field mb-5">' in html
    assert '<label for="id_title" class="form-label">Title</label>' in html
    assert 'class="form-control"' in html
    assert 'placeholder="Title"' in html
    help_html = '<div id="id_title_helptext" class="form-text">'
    help_html += 'A <b>short</b> title.</div>'
    assert help_html in html


def test_select_uses_form_control():
    html = render("{% bootstrap_field form.kind %}", form=SampleForm())
    assert '<select name="kind" class="form-control"' in html
    assert "form-select" not in html


def test_floating_label_comes_before_its_control():
    html = render(
        '{% bootstrap_field form.title layout="floating" placeholder="" %}',
        form=SampleForm(),
    )
    floating = html.index('<div class="form-floating">')
    assert floating < html.index("<label") < html.index("<input")
    # A floating label needs a placeholder to know when to float, but not the
    # label again: Bootstrap 6 shows it under the floated label on focus.
    assert 'placeholder=" "' in html


def test_checkbox_and_switch():
    form = SampleForm()
    check = render("{% bootstrap_field form.agree %}", form=form)
    assert 'class="check"' in check
    assert check.index("<input") < check.index('<label for="id_agree">I agree</label>')

    switch = render("{% bootstrap_field form.accepted %}", form=form)
    assert '<div class="switch"><input type="checkbox"' in switch
    assert " switch" in switch and 'role="switch"' in switch
    assert "form-check" not in switch


def test_show_label_false_hides_label_for_screen_readers_only():
    html = render("{% bootstrap_field form.agree show_label=False %}", form=SampleForm())
    assert '<label for="id_agree" class="visually-hidden">I agree</label>' in html


def test_errors_mark_the_control_invalid():
    form = SampleForm(data={})
    html = render("{% bootstrap_form form %}", form=form)
    assert '<ul class="list-unstyled fg-danger">' in html
    assert "Something is off." in html
    assert 'class="form-control is-invalid"' in html
    assert '<div class="invalid-feedback d-block">This field is required.</div>' in html
    # Valid fields get no success styling.
    assert "is-valid" not in html


def test_form_skips_excluded_and_renders_hidden_fields_bare():
    html = render('{% bootstrap_form form exclude="notes, agree" %}', form=SampleForm())
    assert 'name="notes"' not in html and 'name="agree"' not in html
    assert '<input type="hidden" name="secret" id="id_secret">' in html


def test_widget_with_own_label_is_placed_as_is():
    class ButtonsForm(forms.Form):
        pick = forms.ChoiceField(
            choices=[("1", "One")],
            widget=BootstrapSelectWithButtonsWidget(button_url_names=[], label="Pick"),
        )

    html = render('{% bootstrap_form form layout="floating" %}', form=ButtonsForm())
    assert html.count("<label") == 1
    assert html.index('<label for="id_pick">Pick</label>') < html.index("<select")


def test_button_and_link():
    button = render(
        '{% bootstrap_button content="Save" button_class="btn-outline theme-danger"'
        ' form="F" %}'
    )
    assert button.strip() == (
        '<button class="btn-outline theme-danger" type="submit" form="F">Save</button>'
    )
    link = render('{% bootstrap_button button_type="link" content="Back" href="/x/" %}')
    assert '<a class="btn-solid theme-primary" href="/x/" role="button">Back</a>' in link


@pytest.mark.parametrize(
    "source",
    [
        '{% bootstrap_field form.title size="lg" %}',
        '{% bootstrap_form form layout="horizontal" %}',
        '{% bootstrap_button content="x" button_type="danger" %}',
    ],
)
def test_unsupported_options_fail_loudly(source):
    with pytest.raises(TemplateSyntaxError):
        render(source, form=SampleForm())


def test_radio_widgets_are_refused():
    class RadioForm(forms.Form):
        choice = forms.ChoiceField(choices=[("a", "A")], widget=forms.RadioSelect)

    with pytest.raises(TemplateSyntaxError):
        render("{% bootstrap_field form.choice %}", form=RadioForm())
