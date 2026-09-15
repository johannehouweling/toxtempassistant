import logging
import mimetypes
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from functools import partial

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Q, Sum
from django.forms import widgets
from django.utils.html import escape, format_html
from django.utils.safestring import SafeText, mark_safe
from django_q.tasks import async_task
from guardian.shortcuts import get_objects_for_user

from toxtempass import config, ror
from toxtempass.demo import without_demo_investigations
from toxtempass.filehandling import (
    get_text_or_imagebytes_from_django_uploaded_file,
)
from toxtempass.models import (
    Answer,
    Assay,
    AssayTimeLog,
    Investigation,
    LLMStatus,
    Person,
    Question,
    QuestionSet,
    Section,
    Study,
    Workspace,
    WorkspaceRole,
)
from toxtempass.utilities import add_user_alert, provenance_label_for_item
from toxtempass.widgets import (
    BootstrapSelectWithButtonsWidget,
)  # Import the custom widget

# Form to submit answers to fixed questions for an assay

logger = logging.getLogger("forms")


class LoginForm(forms.Form):
    username = forms.CharField(
        label="Email or ORCID iD",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Enter email or ORCID iD"}),
    )
    password = forms.CharField(
        label="Password",
        required=True,
        widget=forms.PasswordInput(attrs={"placeholder": "Enter password"}),
    )

    def clean(self) -> dict:
        """Clean."""
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        if username:
            username = cleaned_data.get("username").lower()
            cleaned_data["username"] = username
        return cleaned_data


# Links ROR, since not everyone knows the registry; clicking it does not tick the box.
NOT_IN_ROR_LABEL = format_html(
    'My organization is not in <a href="{}" target="_blank" rel="noopener noreferrer">'
    "ROR</a>",
    config.ror_website_url,
)


class OrganizationRorCheckMixin:
    """Push back when an organization matches no ROR record (signup, Account tab).

    A match is not required, since ROR does not list every institution, but a name
    typed in a hurry should not slip through. An unmatched name fails validation,
    listing the closest ROR records for the name and the email domain, until the
    user corrects it or ticks ``organization_not_in_ror`` (declared by each form).
    A match is stored on the instance, so saving queues no second lookup. When ROR
    cannot be reached the form goes ahead unchecked (signals.resolve_ror queues one
    background lookup on save; nothing retries after that).
    """

    # The signup page inserts error messages as HTML, so they are escaped.
    escape_messages = True

    def _check_organization_in_ror(self, cleaned_data: dict, email: str) -> None:
        """Validate ``cleaned_data["organization"]`` against ROR; see the class doc."""
        # A new organization: a match stored for the previous one no longer applies.
        self.instance.ror_id = self.instance.ror_name = ""
        organization = cleaned_data.get("organization")
        if (
            not settings.ROR_LOOKUP_ENABLED
            or not organization
            or cleaned_data.get("organization_not_in_ror")
        ):
            return
        try:
            match = ror.match_organization(organization)
        except ror.RorLookupError as exc:
            logger.warning("ROR check of an organization failed: %s", exc)
            return
        if match is not None:
            self.instance.ror_id, self.instance.ror_name = match
            self.instance.ror_checked_organization = organization
            return

        messages = [config.ror_unmatched_organization_message]
        suggestions = ror.suggest_organizations(organization, email)
        if suggestions:
            shown = suggestions[: config.ror_unmatched_organization_suggestions]
            labels = [item["label"] for item in shown]
            if self.escape_messages:
                labels = [escape(label) for label in labels]
            messages.append(f"Did you mean: {', '.join(labels)}?")
        self.add_error("organization", messages)


class SignupFormOrcid(OrganizationRorCheckMixin, UserCreationForm):
    # Honeypot: hidden in the signup template, so only bots fill it in.
    website = forms.CharField(
        required=False,
        label="Leave this field empty",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )
    # Hidden on the signup page until the organization matched no ROR record.
    organization_not_in_ror = forms.BooleanField(
        required=False, label=NOT_IN_ROR_LABEL
    )

    def __init__(self, *args, **kwargs):
        """Initialize signup form fields."""
        super().__init__(*args, **kwargs)
        organization = self.fields["organization"]
        organization.required = True
        organization.help_text = config.ror_lookup_help_text
        organization.widget.attrs.update(
            {
                "autocomplete": "organization",
                "list": "ror-organization-suggestions",
            }
        )

    class Meta:
        model = Person
        # Include fields you want the user to fill in.
        # Since your model only adds an 'orcid_id' to AbstractUser,
        # you might include username, email, first_name, last_name, etc.
        fields = (
            "email",
            "first_name",
            "last_name",
            "organization",
            "orcid_id",
            "has_accepted_tos",
        )
        # if we come through Orcid this is already known and should not be changed
        widgets = {
            "orcid_id": widgets.TextInput(attrs={"readonly": True}),
        }

    def clean(self) -> dict:
        """Clean."""
        cleaned_data = super().clean()
        if cleaned_data.get("website"):
            logger.warning("Signup rejected: the honeypot field was filled in")
            raise forms.ValidationError("Your signup could not be processed.")
        email = cleaned_data.get("email")
        if email:
            email = cleaned_data.get("email").lower()
            cleaned_data["email"] = email  # make sure we only store lower case emails
        if email and Person.objects.filter(email=email).exists():
            self.add_error("email", "This email address is already in use.")
        # enforce that the user has accepted the terms of service
        has_accepted_tos = cleaned_data.get("has_accepted_tos")
        if not has_accepted_tos:
            self.add_error(
                "has_accepted_tos",
                "You must accept the terms of service to continue.",
            )
        self._check_organization_in_ror(cleaned_data, cleaned_data.get("email") or "")
        return cleaned_data

class SignupForm(SignupFormOrcid):
    class Meta(SignupFormOrcid.Meta):
        fields = tuple(
            field for field in SignupFormOrcid.Meta.fields if field != "orcid_id"
        )


class ProfileForm(OrganizationRorCheckMixin, forms.ModelForm):
    """Name and organization, edited in the Account tab of the user menu."""

    # Shown in the Account tab once a new organization matched no ROR record.
    organization_not_in_ror = forms.BooleanField(
        required=False, label=NOT_IN_ROR_LABEL
    )
    # The user menu shows messages as text.
    escape_messages = False

    class Meta:
        model = Person
        fields = ("first_name", "last_name", "organization")

    def clean_organization(self) -> str:
        """Require an organization, as signup does."""
        organization = (self.cleaned_data.get("organization") or "").strip()
        if not organization:
            raise forms.ValidationError("Please enter your organization.")
        return organization

    def clean(self) -> dict:
        """Check a changed organization against ROR; an unchanged one is left alone."""
        cleaned_data = super().clean()
        # The instance still holds the saved values until _post_clean.
        if cleaned_data.get("organization") != self.instance.organization:
            self._check_organization_in_ror(cleaned_data, self.instance.email)
        return cleaned_data


class EmailChangeForm(forms.Form):
    """A new email address, confirmed with the current password."""

    new_email = forms.EmailField(label="New email address")
    password = forms.CharField(label="Current password", widget=forms.PasswordInput)

    def __init__(self, user: Person, *args, **kwargs):
        """Bind the form to the account whose address changes."""
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_email(self) -> str:
        """Refuse the current address and addresses of other accounts."""
        email = self.cleaned_data["new_email"].strip().lower()
        if email == self.user.email.lower():
            raise forms.ValidationError("This is already your email address.")
        if Person.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("This email address is already in use.")
        return email

    def clean_password(self) -> str:
        """Check the current password, so a left-open session cannot take over."""
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("The password is not correct.")
        return password


class AccountDeletionForm(forms.Form):
    """The current password and an explicit confirmation, to delete an account."""

    password = forms.CharField(label="Current password", widget=forms.PasswordInput)
    confirm = forms.BooleanField(
        error_messages={
            "required": "Please confirm that you understand this cannot be undone."
        }
    )

    def __init__(self, user: Person, *args, **kwargs):
        """Bind the form to the account that is deleted."""
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self) -> str:
        """Check the current password."""
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("The password is not correct.")
        return password


class MultipleFileInput(forms.ClearableFileInput):
    """FileInput for multiple files with upload progress bar and modal."""

    allow_multiple_selected = True

    def render(
        self,
        name: str,
        value: datetime | Decimal | float | str | None,
        attrs: dict | None = None,
        renderer: object | None = None,
    ) -> SafeText:
        """Render the widget with progress bar and modal."""
        # Render the default file input widget
        original_html = super().render(name, value, attrs, renderer)

        # Inline progress bar and filename display (optional inline display)
        inline_progress_html = mark_safe(
            """
            <div class="progress mt-2" style="height: 20px; display:none;" id="uploadProgressContainer">
              <div
                class="progress-bar progress-bar-striped progress-bar-animated"
                role="progressbar"
                aria-valuemin="0"
                aria-valuemax="100"
                aria-valuenow="0"
                style="width: 0%;"
                id="uploadProgressBar">
                0%
              </div>
            </div>
            <div id="uploadProgressFilename" class="small mt-1 text-truncate"></div>
            <div id="accumulated-file-list" class="mt-1 d-flex flex-wrap gap-1"></div>
            """
        )

        # Bootstrap modal for centralized upload progress
        modal_html = mark_safe(
            """
            <div class="modal fade" id="uploadProgressModal" tabindex="-1" aria-labelledby="uploadProgressModalLabel" aria-hidden="true">
              <div class="modal-dialog modal-dialog-centered" role="document">
                <div class="modal-content">
                  <div class="modal-header">
                    <h5 class="modal-title" id="uploadProgressModalLabel">File Upload Progress</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <div class="modal-body">
                    <div class="progress" style="height: 25px;">
                      <div
                        class="progress-bar progress-bar-striped progress-bar-animated"
                        role="progressbar"
                        aria-valuemin="0"
                        aria-valuemax="100"
                        aria-valuenow="0"
                        style="width: 0%;"
                        id="uploadProgressBarModal">
                        0%
                      </div>
                    </div>
                    <div id="uploadProgressFilenameModal" class="small mt-2 text-truncate"></div>
                  </div>
                </div>
              </div>
            </div>
            """
        )

        # Return combined HTML for the widget: original input + inline progress + modal
        return original_html + inline_progress_html + modal_html


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        """Set MultipleFileInput."""
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data: object, initial: object | None = None) -> list:
        """Clean."""
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [single_file_clean(data, initial)]
        return result


class StartingForm(forms.Form):
    question_set = forms.ModelChoiceField(
        queryset=QuestionSet.objects.all(),
        required=True,
        help_text="Select the Question Set to use for this ToxTemp.",
        widget=BootstrapSelectWithButtonsWidget(
            button_url_names=[],
            button_labels=[],
            button_classes=[
                "d-flex align-items-center btn btn-outline-secondary",
                "d-flex align-items-center btn btn-outline-secondary disabled",
                "d-flex align-items-center btn btn-outline-danger rounded-end disabled",
            ],
            label=QuestionSet._meta.verbose_name,  # Use the verbose name of the model
        ),
    )

    investigation = forms.ModelChoiceField(
        queryset=Investigation.objects.none(),  # default to none until filtered
        required=True,
        help_text="Select the Investigation or create a new one.",
        widget=BootstrapSelectWithButtonsWidget(
            button_url_names=["create_investigation", "", ""],
            button_labels=["Create", "Modify", "Delete"],
            button_classes=[
                "d-flex align-items-center btn btn-outline-secondary",
                "d-flex align-items-center btn btn-outline-secondary disabled",
                "d-flex align-items-center btn btn-outline-danger rounded-end disabled",
            ],
        ),
    )
    study = forms.ModelChoiceField(
        queryset=Study.objects.none(),
        required=True,
        help_text="Select the Study or create a new one.",
        widget=BootstrapSelectWithButtonsWidget(
            button_url_names=["create_study", "", ""],
            button_labels=["Create", "Modify", "Delete"],
            button_classes=[
                "d-flex align-items-center btn btn-outline-secondary",
                "d-flex align-items-center btn btn-outline-secondary disabled",
                "d-flex align-items-center btn btn-outline-danger rounded-end disabled",
            ],
        ),
    )
    assay = forms.ModelChoiceField(
        queryset=Assay.objects.none(),
        required=True,
        help_text="Select the Assay or create a new one.",
        widget=BootstrapSelectWithButtonsWidget(
            button_url_names=["create_assay", "", ""],
            button_labels=["Create", "Modify", "Delete"],
            button_classes=[
                "d-flex align-items-center btn btn-outline-secondary",
                "d-flex align-items-center btn btn-outline-secondary disabled",
                "d-flex align-items-center btn btn-outline-danger rounded-end disabled",
            ],
        ),
    )
    files = forms.FileField(
        widget=MultipleFileInput(attrs={"multiple": True}),
        required=False,
        help_text=(
            """Upload documents relevant to your test method to provide context for the
              LLM-generated answers."""
        ),
    )
    overwrite = forms.BooleanField(
        required=False,
        initial=False,
        label="Overwrite",
        help_text="Permission to overwrite.",
    )

    extract_images = forms.BooleanField(
        required=False,
        initial=False,
        label="Extract images from uploaded documents",
        help_text=(
            "If checked, images found in uploaded documents (PDFs, DOCX) will be "
            "extracted and used as additional context for generating answers."
        ),
    )

    consent_file_storage = forms.BooleanField(
        required=False,
        initial=False,
        label="Consent to share uploaded files for benchmarking and improvement",
        help_text=mark_safe(
            """
        Optional: allow the development team to use your uploaded context files to
         benchmark and improve automated pre-population of ToxTemp questionnaires.
        <a class="link-secondary"
           data-bs-toggle="collapse"
           href="#consentDetails"
           role="button"
           aria-expanded="false"
           aria-controls="consentDetails">
          Details
        </a>
        """
        ),
    )

    def __init__(self, *args, user: Person = None, **kwargs):
        """Expect a 'user' keyword argument to filter the querysets.

        Hides the question_set field if only one is available and ensures it's submitted.
        """
        super().__init__(*args, **kwargs)

        visible_question_sets = QuestionSet.objects.filter(is_visible=True)

        if visible_question_sets.count() == 1:
            single_qs = visible_question_sets.first()
            self.fields["question_set"].initial = single_qs.pk
            self.fields["question_set"].widget = forms.HiddenInput()

            # If form is bound (POST), make sure the value is in self.data
            if self.is_bound:
                self.data = self.data.copy()
                self.data["question_set"] = str(single_qs.pk)
        else:
            self.fields["question_set"].queryset = visible_question_sets
            most_recent_qs = visible_question_sets.order_by("-created_at").first()
            if most_recent_qs:
                self.fields["question_set"].initial = most_recent_qs.pk

        if user is not None:
            # New drafts never go into the demo (see demo.without_demo_investigations).
            accessible_investigations = without_demo_investigations(
                get_objects_for_user(user, "toxtempass.view_investigation")
            )
            self.fields["investigation"].queryset = accessible_investigations
            # Bind the current_user into the provenance helper so Django will call
            # the resulting function with a single 'instance' argument as expected.
            self.fields["investigation"].label_from_instance = partial(
                provenance_label_for_item, current_user=user
            )
            self.fields["study"].label_from_instance = partial(
                provenance_label_for_item, current_user=user
            )
            self.fields["study"].queryset = Study.objects.filter(
                investigation__in=accessible_investigations
            )
            self.fields["assay"].queryset = Assay.objects.filter(
                study__investigation__in=accessible_investigations
            )
            self.fields["assay"].label_from_instance = partial(
                provenance_label_for_item, current_user=user
            )

    def clean(self) -> dict:
        """Clean Form."""
        cleaned_data = super().clean()
        assay = cleaned_data.get("assay")
        overwrite = cleaned_data.get("overwrite", False)

        if assay is not None and not overwrite:
            if assay.question_set is not None:
                self.add_error(
                    "overwrite",
                    (
                        "Check here to overwrite. Eventual previous answers will still "
                        "be visible in the history."
                    ),
                )
        return cleaned_data


_DESCRIPTION_TEXTAREA_ATTRS = {
    "style": "min-height: 15vh;",
}

_ASSAY_DESCRIPTION_TEXTAREA_ATTRS = {
    **_DESCRIPTION_TEXTAREA_ATTRS,
    "placeholder": (
        "(1) Test purpose (e.g., cytotoxicity);\n"
        "(2) Test system (e.g., human neural stem cells in a 2D monolayer);\n"
        "(3) Measured endpoint (e.g., cell viability by formazan conversion)."
    ),
}


# Form to create an Investigation
class InvestigationForm(forms.ModelForm):
    class Meta:
        model = Investigation
        fields = ["title", "description", "public_release_date"]
        widgets = {
            "public_release_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs=_DESCRIPTION_TEXTAREA_ATTRS),
        }
    def __init__(self, *args, user: Person, **kwargs):
        """Add owner indication for users who are not the owner to mark provenance."""
        super().__init__(*args, **kwargs)
        if self.instance.pk and getattr(self.instance, "owner", None) != user:
            # change __str__ for title into titel (<owner>)
            original_str = self.instance.__str__
            self.instance.__str__ = lambda: f"{original_str()} (by {self.instance.owner})"


# Form to create a Study
class StudyForm(forms.ModelForm):
    class Meta:
        model = Study
        fields = ["investigation", "title", "description"]
        widgets = {
            "description": forms.Textarea(attrs=_DESCRIPTION_TEXTAREA_ATTRS),
        }

    def __init__(self, *args, user: Person = None, **kwargs):
        """Filter the 'investigation' field.

        Include only those Investigations that the user is permitted to view.
        """
        super().__init__(*args, **kwargs)
        if user is not None:
            # Not into the demo; an edited study keeps its own investigation.
            self.fields["investigation"].queryset = without_demo_investigations(
                get_objects_for_user(user, "toxtempass.view_investigation"),
                keep_pk=self.instance.investigation_id,
            )
            self.fields["investigation"].label_from_instance = partial(
                provenance_label_for_item, current_user=user
            )

    def clean(self) -> dict:
        """If the study is being created by a user who is not the investigation owner,
        attach a warning flag into cleaned_data so the view can show the UI banner.
        """
        cleaned = super().clean()
        inv = cleaned.get("investigation")
        if inv and self.instance.pk is None:
            cleaned["is_workspace_creation"] = getattr(inv, "owner", None) != None
        return cleaned


# Form to create an Assay


class AssayForm(forms.ModelForm):
    class Meta:
        model = Assay
        fields = ["study", "title", "description"]
        widgets = {
            "description": forms.Textarea(attrs=_ASSAY_DESCRIPTION_TEXTAREA_ATTRS),
        }
        help_texts = {
            "description": (
                "Please provide a concise description of the assay that"
                " specifies: (1) the test purpose (e.g., cytotoxicity);"
                " (2) the test system (e.g., human neural stem cells"
                " differentiated into a neuron-astrocyte co-culture in a"
                " 2D monolayer); and (3) the measured endpoint (e.g., "
                "cell viability assessed by formazan conversion using "
                "a luminescence assay)."
            )
        }

    def __init__(self, *args, user: Person = None, **kwargs):
        """Filter the 'study' field based on accessible Investigations."""
        super().__init__(*args, **kwargs)
        if user is not None:
            accessible_investigations = get_objects_for_user(
                user, "toxtempass.view_investigation"
            )
            # Not into the demo; an edited assay keeps its own study.
            self.fields["study"].queryset = Study.objects.filter(
                investigation__in=accessible_investigations
            ).filter(
                Q(investigation__in=without_demo_investigations(accessible_investigations))
                | Q(pk=self.instance.study_id)
            )
            # Show provenance for Study choices when the study's investigation owner differs
            def _study_label_af(obj, current_user=user):
                try:
                    inv_owner = getattr(obj.investigation, "owner", None)
                    creator = getattr(obj, "created_by", None)
                    # Prefer showing the Investigation owner when different; otherwise show who created the Study
                    if inv_owner and inv_owner != current_user and creator and creator != current_user:
                        return f"{obj.title} (by {creator.email} on behalf of {inv_owner.email})"
                    if inv_owner and inv_owner == current_user and creator and creator != current_user:
                        return f"{obj.title} (by {creator.email})"
                    if inv_owner and inv_owner != current_user:
                        return f"{obj.title} (by {inv_owner.email})"
                    if creator and creator != current_user:
                        return f"{obj.title} (by {creator.email})"
                except Exception:
                    pass
                return str(obj.title)

            self.fields["study"].label_from_instance = _study_label_af


class AssayAnswerForm(forms.Form):
    def __init__(self, *args, **kwargs):
        """Expect both 'assay' and optionally 'user' to be provided.

        The user is checked against the assay's access permissions.
        """
        self.assay = kwargs.pop("assay")
        self.user = kwargs.pop("user", None)
        user = self.user
        if user is not None and not self.assay.is_accessible_by(user):
            raise PermissionDenied("You do not have access to this assay.")
        super().__init__(*args, **kwargs)

        accepted_files = ",".join(config.IMAGE_ACCEPT_FILES + config.TEXT_ACCEPT_FILES)
        self.fields["file_upload"] = MultipleFileField(
            widget=MultipleFileInput(
                attrs={
                    "multiple": True,
                    "id": "fileUpload",
                    "accept": accepted_files,
                }
            ),
            required=False,
            help_text=(
                "Context documents for update of selected answers. "
                f"Supported formats: {accepted_files}."
                f" Max size: {config.max_size_mb} MB per file."
            ),
        )
        self.fields["extract_images"] = forms.BooleanField(
            required=False,
            initial=False,
            label="Extract images",
            help_text="Extract images from uploaded PDFs/DOCX and include them in the LLM context.",
        )

        qs = self.assay.question_set

        # Dynamically add fields for each question in the question_set
        # (assuming Sections, Subsections, and Questions are public)
        sections = Section.objects.filter(question_set=qs).prefetch_related(
            "subsections__questions"
        )
        for section in sections:
            for subsection in section.subsections.all():
                for question in subsection.questions.all():
                    # Create a text field for the answer.
                    field_name = f"question_{question.id}"
                    self.fields[field_name] = forms.CharField(
                        label=question.question_text,
                        required=False,
                        widget=forms.Textarea(
                            attrs={
                                "rows": 2,
                                "oninput": (
                                    'this.style.height = "";'
                                    "this.style.height = "
                                    'this.scrollHeight + 3 + "px"'
                                ),
                            }
                        ),
                    )
                    # Prepopulate if an Answer already exists.
                    try:
                        answer = Answer.objects.get(assay=self.assay, question=question)
                        self.fields[field_name].initial = answer.answer_text
                    except Answer.DoesNotExist:
                        self.fields[field_name].initial = ""
                        answer = None

                    # Add a checkbox for marking the answer as accepted.
                    accepted_field_name = f"accepted_{question.id}"
                    self.fields[accepted_field_name] = forms.BooleanField(
                        widget=forms.CheckboxInput(
                            attrs={
                                "class": "form-check-input",
                                "role": "switch",
                                "type": "checkbox",
                            }
                        ),
                        label="Accepted",
                        required=False,
                    )
                    self.fields[accepted_field_name].initial = (
                        answer.accepted if answer else False
                    )

                    # Add a checkbox for earmarking the answer for LLM update.
                    earmarked_field_name = f"earmarked_{question.id}"
                    self.fields[earmarked_field_name] = forms.BooleanField(
                        label="LLM Update",
                        required=False,
                    )
                    self.fields[earmarked_field_name].initial = False

    def clean_file_upload(self) -> list[UploadedFile]:
        """Validate uploaded files and return the accepted list."""
        files: list[UploadedFile] = self.files.getlist("file_upload")
        if not files:
            return []

        allowed_types = config.ALLOWED_MIME_TYPES
        max_size_bytes = int(config.max_size_mb * 1024 * 1024)

        errors: list[forms.ValidationError] = []
        cleaned: list[UploadedFile] = []

        for f in files:
            # Be resilient: fall back to guessing by filename if content_type is missing
            ct = getattr(f, "content_type", "") or (mimetypes.guess_type(f.name)[0] or "")

            if ct not in allowed_types:
                errors.append(
                    forms.ValidationError(
                        f"{f.name}: unsupported file type ({ct or 'unknown'})"
                    )
                )
                continue

            if f.size > max_size_bytes:
                mb = f.size / (1024 * 1024)
                errors.append(
                    forms.ValidationError(
                        f"{f.name}: {mb:.1f} MB exceeds {config.max_size_mb} MB"
                    )
                )
                continue

            cleaned.append(f)

        if errors:
            # Show all problems at once
            raise forms.ValidationError(errors)

        return cleaned

    def save(self) -> bool:
        """Save the form data.

        - Saves answers and their flags.
        - Processes uploaded files.
        - Queues asynchronous refresh for earmarked answers when files are provided.
        """
        if getattr(self.assay, "demo_lock", False):
            self.add_error(
                None,
                "This assay is locked for demo purposes and cannot be edited.",
            )
            return False

        earmarked_answers = []  # for update
        uploaded_files = self.cleaned_data.get("file_upload", [])
        extract_images = self.cleaned_data.get("extract_images", False)
        self.async_enqueued = False

        if uploaded_files:
            doc_dict, unreadable = get_text_or_imagebytes_from_django_uploaded_file(
                uploaded_files, extract_images=False
            )
            logger.debug(f"Received {len(uploaded_files)} uploaded files for processing.")
            if unreadable:
                for name in unreadable:
                    add_user_alert(
                        self.assay,
                        f"'{name}' could not be read and was not used to generate answers.",
                        level="warning",
                    )
                if not doc_dict:
                    self.add_error(
                        "file_upload",
                        "None of the uploaded files could be read. "
                        "Please check that the files are not corrupted or password-protected.",
                    )
                    # Persist alerts without changing assay status — no task is
                    # queued so SCHEDULED would be misleading.
                    self.assay.save(update_fields=["user_alerts"])
                    return False
        else:
            doc_dict = {}
            logger.debug("No files uploaded.")

        # Grouping fields by question ID.
        questions_data = defaultdict(dict)
        for field_name, value in self.cleaned_data.items():
            if field_name.startswith("question_"):
                qid = field_name.split("_")[1]
                questions_data[qid]["answer_text"] = value
            elif field_name.startswith("accepted_"):
                qid = field_name.split("_")[1]
                questions_data[qid]["accepted"] = value
            elif field_name.startswith("earmarked_"):
                qid = field_name.split("_")[1]
                questions_data[qid]["earmarked"] = value

        question_ids = questions_data.keys()
        logger.debug(f"Processing {len(question_ids)} questions.")

        questions = Question.objects.filter(id__in=question_ids)
        questions_map = {str(q.id): q for q in questions}

        for qid, data in questions_data.items():
            question = questions_map.get(qid)
            if not question:
                logger.error(f"Question with id {qid} does not exist.")
                continue

            answer, created = Answer.objects.get_or_create(
                assay=self.assay,
                question=question,
                defaults={"answer_text": data.get("answer_text", "")},
            )
            if not created and answer.answer_text != data.get("answer_text", ""):
                answer.answer_text = data.get("answer_text", "")
                answer.save()
                logger.debug(f"Updated answer_text for question id {qid}.")

            if answer.accepted != data.get("accepted", False):
                answer.accepted = data.get("accepted", False)
                answer.save()
                logger.debug(f"Updated accepted flag for question id {qid}.")

            if data.get("earmarked", False):
                earmarked_answers.append(answer)
                logger.info(f"Question id {qid} marked for LLM update.")

        if uploaded_files and not earmarked_answers:
            self.add_error(
                "file_upload",
                "Select at least one question for LLM update when uploading files.",
            )
            return False

        if earmarked_answers and not uploaded_files:
            self.add_error(
                "file_upload",
                "Upload at least one context document to update selected questions.",
            )
            return False

        if uploaded_files and earmarked_answers:
            # The edits above are kept; only the LLM update needs a confirmed address.
            if self.user is not None and not self.user.has_confirmed_email:
                self.add_error("file_upload", config._email_confirmation_required_message)
                return False
            answer_ids = [answer.id for answer in earmarked_answers]
            try:
                from toxtempass.llm import current_llm_key
                from toxtempass.views import process_llm_async

                for answer in earmarked_answers:
                    if answer.accepted:
                        answer.accepted = False
                        answer.save(update_fields=["accepted"])
                self.assay.status = LLMStatus.SCHEDULED
                async_task(
                    process_llm_async,
                    self.assay.id,
                    doc_dict,
                    extract_images,
                    answer_ids,
                    user_id=getattr(self.user, "pk", None),
                    # Snapshot the model now, like new_form_view, so the run and its
                    # cost record use the model chosen when the update was requested.
                    llm_model=current_llm_key(self.user) if self.user else None,
                )
                self.assay.save(update_fields=["status", "user_alerts"])
                self.async_enqueued = True
                logger.info(
                    "Queued asynchronous update for answers %s on assay %s.",
                    answer_ids,
                    self.assay.id,
                )
                return True
            except Exception as exc:
                logger.exception(
                    "Failed to enqueue async update for assay %s", self.assay.id
                )
                self.add_error("file_upload", str(exc))
                return False

        # Auto-capture completion time when all answers are first accepted.
        # Skipped when an async LLM task is queued (answers will change again).
        if (
            not self.async_enqueued
            and self.assay.completion_time_seconds is None
            and self.assay.all_answers_accepted
        ):
            # Compute aggregate inside a transaction and use a conditional DB
            # update to avoid a race when two collaborators accept the last
            # answers simultaneously.
            with transaction.atomic():
                total = (
                    AssayTimeLog.objects.filter(assay=self.assay).aggregate(
                        total=Sum("seconds")
                    )["total"]
                    or 0
                )
                updated = Assay.objects.filter(
                    pk=self.assay.pk,
                    completion_time_seconds__isnull=True,
                ).update(completion_time_seconds=total)
            if updated:
                self.assay.completion_time_seconds = total
                logger.info(
                    "Assay %s marked complete; completion_time_seconds=%s",
                    self.assay.id,
                    self.assay.completion_time_seconds,
                )

        return self.async_enqueued


class WorkspaceForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ["name", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class WorkspaceMemberForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=Person.objects.all(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    role = forms.ChoiceField(
        choices=WorkspaceRole.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].label_from_instance = lambda obj: obj.email


class WorkspaceInvestigationForm(forms.Form):
    investigation = forms.ModelChoiceField(
        queryset=Investigation.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            accessible_investigations = get_objects_for_user(
                user, "toxtempass.view_investigation"
            )
            self.fields["investigation"].queryset = Investigation.objects.filter(
                id__in=[i.id for i in accessible_investigations]
            )
            self.fields["investigation"].label_from_instance = lambda x: provenance_label_for_item(x, user)
