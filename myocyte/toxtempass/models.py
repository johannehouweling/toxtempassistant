from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from guardian.shortcuts import assign_perm
from simple_history.models import HistoricalRecords

from toxtempass import config


class LLMStatus(models.TextChoices):
    NONE = "none", "None"
    SCHEDULED = "scheduled", "Scheduled"
    BUSY = "busy", "Busy"
    DONE = "done", "Done"
    ERROR = "error", "Error"


# we are desinging user access that inherits from the parent object.
# That way if Investigation is shared, all the children objects will be shared as well.
class AccessibleModel(models.Model):
    """Abstract base model for objects that may have hierarchical permissions."""

    class Meta:
        abstract = True

    def get_parent(self) -> None:
        """Return the immediate parent object in the hierarchy, if any.

        Override this method in child models that have a parent.
        """
        return None

    def is_accessible_by(self, user: "Person", perm_prefix: str = "view") -> bool:
        """Check if a user has permission to access this object.

        The check is recursive: if the user does not have direct permission on
        this instance, check its parent (if any).

        :param user: The user to check permissions for.
        :param perm_prefix: The permission prefix (e.g., 'view', 'change', 'delete').
        :return: True if the permission is granted on this instance or any parent.
        """
        # Construct the permission codename, e.g., 'view_investigation'
        codename = f"{perm_prefix}_{self._meta.model_name}"
        full_permission = f"{self._meta.app_label}.{codename}"

        # Direct permission check using Django's permission system (or django-guardian)
        if user.has_perm(full_permission, self):
            return True

        # Otherwise, try checking the parent's permissions, if a parent exists
        parent = self.get_parent()
        if parent is not None:
            return parent.is_accessible_by(user, perm_prefix=perm_prefix)

        # No permission found in the chain
        return False


class PersonManager(BaseUserManager):
    def create_user(self, email: str = None, password: str = None, **kwargs) -> "Person":
        """Create user."""
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **kwargs)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, *args, **kwargs) -> "Person":
        """Create superuser."""
        user = self.model(**kwargs)
        user.set_password(kwargs.get("password"))
        user.is_superuser = True
        user.is_staff = True
        user.save(using=self._db)
        return user


class Person(AbstractUser):
    objects = PersonManager()

    # Remove the username field by setting it to None.
    username = None
    email = models.EmailField("email address", unique=True, validators=[validate_email])
    organization = models.CharField(default="", blank=True, max_length=255)
    ror_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="ROR identifier matched to organization "
        "(e.g. https://ror.org/01cesdt21); empty if unmatched.",
    )
    ror_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="ROR display name of the matched institution; empty if unmatched.",
    )
    ror_checked_organization = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The organization value the ROR lookup last ran for; lets the lookup "
        "skip unchanged names.",
    )
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # Add List of fields which you want to be required
    orcid_id = models.CharField(
        max_length=19,
        blank=True,
        unique=True,
        null=True,
        # editable=False,
        help_text=(
            "When the user authenticates with ORCID, "
            "this field will be populated with the ORCID iD."
        ),
    )
    has_accepted_tos = models.BooleanField(
        default=False,
        null=False,
        blank=False,
        verbose_name="I have read, understood and accept the terms of service.",
        help_text=(
            "<button type='button' class='btn btn-sm btn-outline-secondary'"
            " data-bs-toggle='modal' data-bs-target='#termsModal'>"
            "Terms of service</button>"
        ),
    )
    preferences = models.JSONField(
        null=True,
        blank=True,
        help_text="Miscelanous stuff about the user can be stored here",
    )

    @property
    def num_assays(self) -> int:
        """Return the number of assays owned by this user."""
        return sum(
            study.assays.count()
            for investigation in self.investigations.all()
            for study in investigation.studies.all()
        )


# Investigation Model
class Investigation(AccessibleModel):
    owner = models.ForeignKey(
        Person, on_delete=models.PROTECT, related_name="investigations"
    )
    title = models.CharField(max_length=255, blank=False, null=False)
    description = models.TextField(blank=True, default="")
    submission_date = models.DateTimeField(auto_now_add=True)
    public_release_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        """Investigation as string."""
        return self.title

    def save(self, *args, **kwargs) -> None:
        """Save the object and assign object-level permissions to the owner."""
        super().save(*args, **kwargs)
        # Ensure the owner always gets object-level permissions on their own
        # Investigation.
        assign_perm("view_investigation", self.owner, self)
        assign_perm("change_investigation", self.owner, self)
        assign_perm("delete_investigation", self.owner, self)

    def share(self, user: Person) -> None:
        """Grant full access to the specified user for this Investigation."""
        assign_perm("view_investigation", user, self)
        assign_perm("change_investigation", user, self)
        assign_perm("delete_investigation", user, self)

    def get_parent(self) -> None:
        """Return the parent object in the hierarchy, if any."""
        return None


# Study Model
class Study(AccessibleModel):
    investigation = models.ForeignKey(
        Investigation, on_delete=models.CASCADE, related_name="studies"
    )
    title = models.CharField(max_length=255, blank=False, null=False)
    description = models.TextField(blank=True)
    submission_date = models.DateTimeField(auto_now_add=True)
    # Track who created this Study (may be a workspace member who is not the investigation owner)
    created_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_studies",
        help_text="The user who created this Study (may differ from the Investigation owner).",
    )

    def __str__(self):
        """Study as string."""
        return self.title

    def get_parent(self) -> Investigation:
        """Get parent."""
        return self.investigation


# To allow different Versions of ToxTempQuestions
class QuestionSet(models.Model):
    """A named version of the entire question hierarchy."""

    label = models.CharField(max_length=10, unique=True, null=True)  # v1  # noqa: DJ001
    display_name = models.CharField(
        max_length=50,
        default="v2019",
        help_text="A user-friendly name for this question set version, e.g.,"
        " 'ToxTemp Questions v1.0'.",
    )  # noqa: DJ001
    hide_from_display = models.BooleanField(
        default=True,
        help_text="If true, this question set will not be displayed in the UI.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="questionsets",  # so you can do some_person.questionsets.all()
        null=True,
        blank=False,
        help_text="The user who created this question set version.",
    )
    is_visible = models.BooleanField(
        default=True,
        help_text="Control whether this QuestionSet version is shown to users.",
    )

    class Meta:
        verbose_name = "ToxTemp Question Set Version"
        verbose_name_plural = "ToxTemp Question Set Versions"

    def __str__(self) -> str:
        """Questionset as string."""
        if self.display_name:
            return f"{self.display_name} ({self.created_at.strftime('%b %Y')})"
        elif self.label:
            return f"{self.label} ({self.created_at.strftime('%b %Y')})"
        else:
            return f"Unnamed Question Set ({self.created_at.strftime('%b %Y')})"

    def __expr__(self) -> str:
        """Return a string representation of the QuestionSet for debugging."""
        return f"QuestionSet(label={self.label}, display_name={self.display_name})"

    def is_accessible_by(self, user: Person, perm_prefix: str = "view") -> bool:
        """Can user access this."""
        # Always return True since questions are public.
        return True

    def display(self) -> str:
        """Return a safe HTML representation of the question set."""
        return self.display_name or self.label


# Assay Model
class Assay(AccessibleModel):
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="assays")
    title = models.CharField(max_length=255, blank=False, null=False)
    description = models.TextField(blank=False, default="")
    submission_date = models.DateTimeField(auto_now_add=True)
    # Track who created this Assay (may be a workspace member who is not the investigation owner)
    created_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_assays",
        help_text="The user who created this Assay (may differ from the Investigation owner).",
    )
    status = models.CharField(
        max_length=10,
        choices=LLMStatus.choices,
        default=LLMStatus.NONE,
    )
    demo_lock = models.BooleanField(
        default=False,
        help_text="Prevent edits so this assay can be used as a read-only demo.",
    )
    demo_template = models.BooleanField(
        default=False,
        help_text="Marks this assay as the master template used to seed demo copies.",
    )
    demo_source = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demo_copies",
        help_text="Template assay this demo copy originated from.",
    )
    processing_log = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Internal append-only log of file-processing and LLM events for "
            "this assay (correlation ids, exception traces, info notices). "
            "May contain debug-grade detail and is NOT shown to end users."
        ),
    )
    user_alerts = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "User-visible alerts rendered as dismissible banners on the assay "
            "page. List of {message, level, ts} entries. Only pre-vetted "
            "messages should be added — never raw exception text."
        ),
    )
    question_set = models.ForeignKey(
        QuestionSet,
        on_delete=models.PROTECT,
        related_name="assays",
        blank=True,
        null=True,
        help_text="Which version of the questionnaire this assay is using",
    )
    completion_time_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Aggregated active time (in seconds) across all collaborators at the "
            "moment every answer was accepted for the first time. Set automatically; "
            "never overwritten once captured."
        ),
    )

    def __str__(self) -> str:
        """Assay as string."""
        # if unique title within study, just show title otherwise title + submission date
        if Assay.objects.filter(title=self.title).count() == 1:
            return self.title
        return f"{self.title} ({self.submission_date.strftime('%d %b, %Y - %H:%M')})"

    def clean(self) -> None:
        """Keep the demo template in a sane, seedable state.

        A demo *template* is the master that new users get cloned from, so it must
        be a clean source: it cannot itself be a demo copy, and it must carry a
        question_set (otherwise the seeded copy is hidden from the overview).
        """
        super().clean()
        if self.demo_template:
            if self.demo_source_id is not None:
                raise ValidationError(
                    "A demo template cannot itself be a demo copy — clear "
                    "'demo source' before marking this assay as the template."
                )
            if self.question_set_id is None:
                raise ValidationError(
                    "A demo template must have a question set, otherwise the "
                    "seeded demo copy would be hidden from users' overview."
                )

    @property
    def get_n_questions(self) -> float:
        """Get number of questions associated with assay."""
        # Count all questions related to this assay
        # Navigate through the study -> sections -> subsections -> questions
        return Question.objects.filter(
            subsection__section__subsections__questions__answers__assay=self
        ).count()

    @property
    def get_n_answers(self) -> float:
        """Get number of answers associtated with assay."""
        # Count all answers related to this assay
        return self.answers.count()

    @property
    def get_n_accepted_answers(self) -> float:
        """Get number of accepted answers associtated with assay."""
        # Count all answers that are marked as accepted
        return self.answers.filter(accepted=True).count()

    @property
    def all_answers_accepted(self) -> bool:
        """Return True when every existing answer row is accepted (at least one exists).

        Uses a single aggregation query to avoid N+1.
        """
        agg = self.answers.aggregate(
            total=Count("id"),
            accepted_count=Count("id", filter=Q(accepted=True)),
        )
        return agg["total"] > 0 and agg["total"] == agg["accepted_count"]

    def get_parent(self) -> Study:
        """Get Study."""
        return self.study

    def is_accessible_by(self, user: "Person", perm_prefix: str = "view") -> bool:
        """Check if this assay is accessible by the user.

        Checks:
        1. Direct permission on this assay
        2. Workspace membership: user is in a workspace that has this assay shared
        3. Parent permissions (Study -> Investigation)
        """
        codename = f"{perm_prefix}_{self._meta.model_name}"
        full_permission = f"{self._meta.app_label}.{codename}"

        if user.has_perm(full_permission, self):
            return True

        user_workspaces = WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
        # If the parent Investigation is shared to any workspace the user is a member of,
        # the assay should be accessible as well — but delete is restricted to the assay
        # creator or the investigation owner to prevent members from deleting others' work.
        from toxtempass.models import WorkspaceInvestigation

        if WorkspaceInvestigation.objects.filter(
            investigation=self.study.investigation, workspace_id__in=user_workspaces
        ).exists():
            if perm_prefix == "delete":
                return (
                    self.created_by_id == user.pk
                    or self.study.investigation.owner_id == user.pk
                )
            return True

        parent = self.get_parent()
        if parent is not None:
            return parent.is_accessible_by(user, perm_prefix=perm_prefix)

        return False

    @property
    def has_feedback(self) -> bool:
        """Check if this assay has feedback."""
        # Check if there are any feedbacks related to this assay
        return hasattr(self, "feedback")

    @property
    def number_answers_not_found(self) -> int:
        """Check if there are any answers not found for this assay."""
        not_found_string = config.not_found_string
        # Get all questions related to this assay
        return Answer.objects.filter(
            assay=self, answer_text__icontains=not_found_string
        ).count()

    @property
    def number_processed_answers(self) -> int:
        """Check if there are any answers processed for this assay."""
        not_found_string = config.not_found_string
        # Get all questions related to this assay
        return (
            Answer.objects.filter(
                assay=self,
            )
            .filter(~Q(Q(answer_text="") | Q(answer_text__isnull=True)))
            .count()
        )

    @property
    def number_answers_found_but_not_accepted(self) -> int:
        """Check if there are any answers found but not yet accepted for this assay."""
        not_found_string = config.not_found_string
        # Get all questions related to this assay
        return (
            Answer.objects.filter(
                assay=self,
                accepted=False,
            )
            .filter(
                ~Q(
                    Q(answer_text__icontains=not_found_string)
                    | Q(answer_text="")
                    | Q(answer_text__isnull=True)
                )
            )
            .count()
        )

    @property
    def accepted_by_riskhunt3r_label(self) -> list[dict]:
        """The 5 NAM-readiness-level cells for the split progress bar.

        Powers the optional, click-to-reveal breakdown: one equal-width rounded
        bar per RISK-HUNT3R readiness level (Basic → Level 3+), laid side by side
        to span the same width as the main bar. Like the main bar, each level
        bar shows accepted answers (solid) and drafted-but-not-accepted answers
        (striped), so a method whose answers are prefilled reads as in-progress
        per level rather than empty. Levels as adapted from the RISK-HUNT3R
        test-method DB (https://risk-hunt3r.net/test-methods/).

        Returns one entry per level in ``Config.RISKHUNT3R_LABEL_ORDER`` (so the
        row always has the same number of bars), each carrying the Bootstrap
        contextual class, label, the level's accepted/drafted/total, and the
        within-bar fill percentages ``pct`` (accepted) and ``draft_pct``
        (drafted). Returns ``[]`` when the assay has no answers; uncategorised
        answers (blank label) form no bar. Two grouped queries.
        """
        nf = config.not_found_string
        rows = self.answers.values("question__riskhunt3r_db_label").annotate(
            total=Count("id"),
            accepted=Count("id", filter=Q(accepted=True)),
        )
        by_label = {r["question__riskhunt3r_db_label"]: r for r in rows}
        total_all = sum(r["total"] for r in by_label.values())
        if not total_all:
            return []
        # "drafted" = a real answer the LLM produced that is not yet accepted
        # (mirrors number_answers_found_but_not_accepted). Computed as its own
        # grouped query: an answer_text LIKE inside a filtered Count() trips
        # SQLite with "misuse of aggregate function COUNT()".
        draft_rows = (
            self.answers.filter(accepted=False)
            .exclude(answer_text__icontains=nf)
            .exclude(answer_text="")
            .values("question__riskhunt3r_db_label")
            .annotate(n=Count("id"))
        )
        drafted_by = {r["question__riskhunt3r_db_label"]: r["n"] for r in draft_rows}
        levels = [v for v in config.RISKHUNT3R_LABEL_ORDER if v in config.RISKHUNT3R_LABEL_META]
        # Each level renders as its own equal-width rounded bar (flex-fill in the
        # template), so pct/draft_pct are the fill within that level's own bar.
        segments = []
        for value in levels:
            meta = config.RISKHUNT3R_LABEL_META[value]
            row = by_label.get(value)
            total = row["total"] if row else 0
            accepted = row["accepted"] if row else 0
            drafted = drafted_by.get(value, 0)
            segments.append(
                {
                    "value": value,
                    "css_class": meta["css_class"],
                    "label": meta["label"],
                    "accepted": accepted,
                    "drafted": drafted,
                    "total": total,
                    "pct": int(accepted / total * 100) if total else 0,
                    "draft_pct": int(drafted / total * 100) if total else 0,
                }
            )
        return segments

    @property
    def is_saved(self) -> bool:
        """Check if saved.

        Returns True if this assay has at least one
        Answer row (i.e. it's been seeded/saved).
        """
        return self.answers.exists()

    @property
    def owner(self) -> Person:
        """Return the owner of this assay (i.e., the owner of the parent investigation)."""
        return self.study.investigation.owner


class DemoAssay(Assay):
    """Admin-only proxy of Assay, surfacing demo-related assays in their own section.

    Adds no database table or columns — it exists purely to give demo template /
    demo copy assays a dedicated entry in the admin sidebar.
    """

    class Meta:
        proxy = True
        verbose_name = "Demo assay"
        verbose_name_plural = "Demo assays"


# New model to track individual user's assay views
class AssayView(models.Model):
    user = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="assay_views")
    assay = models.ForeignKey(Assay, on_delete=models.CASCADE, related_name="views")
    last_viewed = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("user", "assay")

    def __str__(self):
        return f"AssayView(user={self.user.email}, assay={self.assay.title}, last_viewed={self.last_viewed})"


class AssayTimeLog(models.Model):
    """Server-side record of how many active seconds a single user spent on an assay.

    One row per (user, assay) pair.  The client writes the cumulative total on
    every periodic sync; the server derives the aggregate across all collaborators
    by summing rows for the same assay.
    """

    user = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="assay_time_logs"
    )
    assay = models.ForeignKey(
        Assay, on_delete=models.CASCADE, related_name="time_logs"
    )
    seconds = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "assay")

    def __str__(self):
        return (
            f"AssayTimeLog(user={self.user.email}, assay={self.assay.id},"
            f" seconds={self.seconds})"
        )


# Section, Subsection, and Question Models (fixed)
class Section(AccessibleModel):
    question_set = models.ForeignKey(
        QuestionSet,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    title = models.CharField(max_length=255)

    def __str__(self):
        """Section as string."""
        return self.title + f"({self.question_set.display()})"

    @property
    def all_answers_accepted(self) -> bool:
        """Check if all answers within this section are marked as accepted."""
        # Get all answers related to all questions in all subsections under this section
        answers = Answer.objects.filter(question__subsection__section=self)

        # Check if there are any answers and if all are accepted
        return answers.exists() and all(answer.accepted for answer in answers)

    def is_accessible_by(self, user: Person, perm_prefix: str = "view") -> bool:
        """Check if this section is accessible by the user."""
        # Always return True since questions are public.
        return True


class Subsection(AccessibleModel):
    section = models.ForeignKey(
        Section, on_delete=models.CASCADE, related_name="subsections"
    )
    title = models.CharField(max_length=255)

    def __str__(self):
        """Return a string representation of the subsection."""
        return self.title + f" ({self.section.question_set.display()})"

    @property
    def all_answers_accepted(self) -> bool:
        """Check if all answers within this subsection are marked as accepted."""
        # Get all answers related to the questions in this subsection
        answers = Answer.objects.filter(question__subsection=self)

        # Check if there are any answers and if all are accepted
        return answers.exists() and all(answer.accepted for answer in answers)

    def is_accessible_by(self, user: Person, perm_prefix: str = "view") -> bool:
        """Check if this subsection is accessible by the user."""
        # Always return True since questions are public.
        return True


class RiskHunt3rLabel(models.TextChoices):
    """RISK-HUNT3R test-method-database readiness category for a ToxTemp question.

    Sourced from the RISK-HUNT3R test-method DB, which tags every ToxTemp field
    with a colour denoting how core vs. late-stage the information is. We store
    the colour token; the human label and Bootstrap contextual class live in
    ``Config.RISKHUNT3R_LABEL_META`` so the copy/colour mapping changes in one
    place.

    BLUE    Identity & overview (title, names, version, depositor, contacts)
    GREEN   Core scientific description (abstract, cells, endpoints, prediction)
    YELLOW  Quality criteria & methodological detail (acceptance, variability)
    ORANGE  Supplementary documentation (SOP/protocol links, IP, storage, AOPs)
    RED     Validation, transferability, regulatory & safety (late-stage)
    """

    BLUE = "blue", "Basic"
    GREEN = "green", "Level 1"
    YELLOW = "yellow", "Level 2"
    ORANGE = "orange", "Level 3"
    RED = "red", "Level 3+"


class Question(AccessibleModel):
    subsection = models.ForeignKey(
        Subsection, on_delete=models.CASCADE, related_name="questions"
    )
    parent_question = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="subquestions",
        null=True,
        blank=True,
    )
    question_text = models.TextField()
    subsections_for_context = models.ManyToManyField(
        Subsection,
        related_name="context_questions",
        blank=True,
        help_text="List of subsections that provide context for this question.",
    )
    only_subsections_for_context = models.BooleanField(
        default=False,
        help_text=(
            "If true, only answers from the subsections listed in"
            " subsections_for_context will be used to answer this question."
        ),
    )
    answering_round = models.PositiveSmallIntegerField(
        default=1,
        help_text="Which round (1, 2, 3, ...) this question should be answered in.",
    )
    additional_llm_instruction = models.TextField(
        blank=True,
        default="",
        help_text="Extra prompt instructions for the LLM when answering this question.",
    )
    only_additional_llm_instruction = models.BooleanField(
        blank=False,
        null=False,
        default=False,
        help_text="Extra flag to determine if additional llm instruction shall replace all others.",
    )
    riskhunt3r_db_label = models.CharField(
        max_length=10,
        blank=True,
        default="",
        choices=RiskHunt3rLabel.choices,
        help_text=(
            "RISK-HUNT3R test-method-DB readiness category (rendered as a colour). "
            "Drives the optional per-category progress breakdown. "
            "Blank = uncategorised."
        ),
    )

    answer = models.TextField(blank=True)

    def __str__(self):
        """Return a string representation of the question."""
        return str(self.question_text)

    def is_accessible_by(self, user: Person, perm_prefix: str = "view") -> bool:
        """Check if this question is accessible by the user."""
        # Always return True since questions are public.
        return True


class FileAsset(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    bucket = models.CharField(max_length=255, blank=True)
    object_key = models.CharField(max_length=1024, unique=True)

    original_filename = models.CharField(max_length=512)
    content_type = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)

    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.AVAILABLE
    )

    uploaded_by = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="uploaded_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.original_filename


# Answer Model (linked to Assay)
class Answer(AccessibleModel):
    assay = models.ForeignKey(Assay, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        blank=False,
        null=False,
    )
    answer_documents = models.JSONField(
        null=True,
        blank=True,
        help_text="Store list of Filenames used to answer this question.",
    )  # change this to VectorField with for a real database.
    files = models.ManyToManyField(
        FileAsset,
        through="AnswerFile",
        related_name="answers",
        blank=True,
        help_text="Actual stored files (only present if user consented to storage).",
    )
    answer_text = models.TextField(blank=True, default="")
    accepted = models.BooleanField(
        null=True, blank=True, help_text="Marked as final answer."
    )
    history = HistoricalRecords()

    def __str__(self):
        """Return a string representation of the answer."""
        return f"Answer to: {self.question} for assay {self.assay}"

    def get_parent(self) -> Assay:
        """Return the parent Assay object."""
        return self.assay

    @property
    def preview_text(self, max_length: int = 75) -> str:
        """Return a preview of the answer text, truncated to max_length."""
        if len(self.answer_text) <= max_length:
            return self.answer_text
        return self.answer_text[:max_length].rsplit(" ", 1)[0] + "..."


class AnswerFile(models.Model):
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE)
    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE)

    # optional per-link metadata, useful later
    label = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["answer", "file"], name="uq_answer_file"),
        ]


class FileDownloadLog(models.Model):
    """Audit log for file downloads (staff/superuser only)."""

    file = models.ForeignKey(
        FileAsset,
        on_delete=models.CASCADE,
        related_name="download_logs",
    )
    user = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="file_download_logs",
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, help_text="IP address of the download request"
    )
    downloaded_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-downloaded_at"]
        verbose_name = "File Download Log"
        verbose_name_plural = "File Download Logs"

    def __str__(self):
        return f"{self.user.email} downloaded {self.file.original_filename} on {self.downloaded_at.strftime('%Y-%m-%d %H:%M:%S')}"


# Feedback Model
class Feedback(AccessibleModel):
    user = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="feedbacks")
    feedback_text = models.TextField()
    usefulness_rating = models.FloatField(null=True, blank=True)
    time_spent_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Automatically measured active time spent on the assay page, in seconds."
        ),
    )
    submission_date = models.DateTimeField(auto_now_add=True)
    assay = models.OneToOneField(Assay, on_delete=models.CASCADE, related_name="feedback")

    def __str__(self):
        """Represent as String."""
        return f"Feedback from {self.user} on {self.submission_date}"

    def get_parent(self) -> Assay:
        """Return the parent Assay object."""
        return self.assay


class WorkspaceRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class Workspace(AccessibleModel):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="owned_workspaces"
    )
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def get_parent(self):
        return None
    
    def save(self, *args, **kwargs):
        """Override save to ensure owner is always a member with OWNER role."""
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            WorkspaceMember.objects.create(workspace=self, user=self.owner, role=WorkspaceRole.OWNER)


class WorkspaceMember(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="workspace_memberships"
    )
    role = models.CharField(
        max_length=20, choices=WorkspaceRole.choices, default=WorkspaceRole.MEMBER
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("workspace", "user")


class WorkspaceInvestigation(models.Model):
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="shared_investigations"
    )
    investigation = models.ForeignKey(
        Investigation, on_delete=models.CASCADE, related_name="shared_in_workspaces"
    )
    added_by = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("workspace", "investigation")


class LLMConfig(models.Model):
    """Singleton admin-managed configuration for Azure AI Foundry LLM endpoints.

    Only one row should ever exist (enforced by the ``save`` override).
    Stores which deployment is the default and which deployments users may choose.
    """

    default_model = models.CharField(
        max_length=128,
        default="",
        blank=True,
        help_text=(
            'Default deployment as "endpoint_index:tag" (e.g. "1:GPT4O"). '
            "Endpoint is derived from the selection. Empty = use first discovered model."
        ),
    )
    allowed_models = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'List of "endpoint_index:tag" strings users may choose from. '
            "Empty list = all discovered models are allowed."
        ),
    )
    last_health_check = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Per-deployment smoke-test results keyed by "index:tag". Populated by '
            "the 'Run health check now' admin action."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "LLM Configuration"
        verbose_name_plural = "LLM Configuration"

    def save(self, *args, **kwargs):
        """Persist the singleton row (always pk=1)."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "LLMConfig":
        """Return the singleton row, creating it with defaults if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def default_endpoint_index(self) -> int | None:
        """Parse the endpoint index from ``default_model`` (``"idx:tag"``)."""
        if ":" in self.default_model:
            try:
                return int(self.default_model.split(":", 1)[0])
            except ValueError:
                return None
        return None

    @property
    def default_model_tag(self) -> str:
        """Parse the model tag from ``default_model`` (``"idx:tag"``)."""
        if ":" in self.default_model:
            return self.default_model.split(":", 1)[1]
        return ""

    def __str__(self):
        return f"LLM Config (default={self.default_model or 'auto'})"




class AssayCost(models.Model):
    """Records the token usage and estimated cost for one LLM generation run on an assay.

    One row is created (or updated) per (assay, model_key) combination each time
    ``process_llm_async`` completes.  Cost fields are derived from the
    ``cost-input-1mtoken`` / ``cost-output-1mtoken`` tags on the model at the time
    the run executes; they stay ``None`` when those tags are absent.
    """

    assay = models.ForeignKey(
        "Assay",
        on_delete=models.CASCADE,
        related_name="costs",
        help_text="The assay this cost record belongs to.",
    )
    model_key = models.CharField(
        max_length=64,
        help_text='Deployment key used, e.g. "1:GPT4O".',
    )
    model_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text='Underlying model id at run time, e.g. "gpt-4o".',
    )
    input_tokens = models.PositiveBigIntegerField(
        default=0,
        help_text="Total prompt tokens consumed across all questions in this run.",
    )
    output_tokens = models.PositiveBigIntegerField(
        default=0,
        help_text="Total completion tokens produced across all questions in this run.",
    )
    cost_input_per_1m = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Snapshot of input price (EUR / 1 M tokens) at run time.",
    )
    cost_output_per_1m = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Snapshot of output price (EUR / 1 M tokens) at run time.",
    )
    cost_input = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Calculated input cost in EUR for this run.",
    )
    cost_output = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Calculated output cost in EUR for this run.",
    )
    cost_unit = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text='Currency unit from the cost-unit tag at run time, e.g. "Eur".',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Assay LLM Cost"
        verbose_name_plural = "Assay LLM Costs"
        unique_together = ("assay", "model_key")
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        total = self.total_cost
        sym = self.cost_unit_symbol
        if total is not None:
            return f"AssayCost assay={self.assay_id} model={self.model_key} total={sym}{total:.6f}"
        return f"AssayCost assay={self.assay_id} model={self.model_key}"

    @property
    def cost_unit_symbol(self) -> str:
        """Return a display symbol for the stored cost unit (e.g. ``€`` for ``Eur``)."""
        from toxtempass.azure_registry import cost_unit_symbol as _sym
        return _sym(self.cost_unit)

    @property
    def total_cost(self):
        """Return combined input + output cost, or ``None`` if cost data is absent."""
        if self.cost_input is None and self.cost_output is None:
            return None
        return (self.cost_input or 0) + (self.cost_output or 0)
