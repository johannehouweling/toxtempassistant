import base64
import os
import uuid
from pathlib import Path

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from toxtempass.filehandling import get_text_or_bytes_perfile_dict
from toxtempass.models import (
    Answer,
    Assay,
    FileAsset,
    Investigation,
    LLMStatus,
    Person,
    Question,
    QuestionSet,
    Section,
    Study,
    Subsection,
    Workspace,
    WorkspaceInvestigation,
    WorkspaceMember,
    WorkspaceRole,
)


class PersonFactory(DjangoModelFactory):
    class Meta:
        model = Person

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.LazyAttributeSequence(
        lambda obj, n: f"{obj.first_name}.{obj.last_name}.{n}@test.com"
    )
    # A working account. Pass email_confirmed_at=None to test unconfirmed users.
    email_confirmed_at = factory.LazyFunction(timezone.now)


class InvestigationFactory(DjangoModelFactory):
    class Meta:
        model = Investigation

    owner = factory.SubFactory(PersonFactory)
    title = factory.Faker("sentence", locale="en_US", nb_words=6, variable_nb_words=True)
    description = factory.Faker(
        "sentence", locale="en_US", nb_words=20, variable_nb_words=True
    )


class StudyFactory(DjangoModelFactory):
    class Meta:
        model = Study

    investigation = factory.SubFactory(InvestigationFactory)
    title = factory.Faker("sentence", locale="en_US", nb_words=6, variable_nb_words=True)
    description = factory.Faker(
        "sentence", locale="en_US", nb_words=20, variable_nb_words=True
    )


class AssayFactory(DjangoModelFactory):
    class Meta:
        model = Assay

    study = factory.SubFactory(StudyFactory)
    title = factory.Faker("sentence", locale="en_US", nb_words=6, variable_nb_words=True)
    description = factory.Faker(
        "sentence", locale="en_US", nb_words=20, variable_nb_words=True
    )
    status = LLMStatus.NONE


class DocumentDictFactory(factory.Factory):
    class Meta:
        model = dict

    @classmethod
    def _create(
        cls,
        model_class,
        *args,
        num_text: int = 2,
        num_bytes: int = 1,
        document_filenames: list[Path | str] | None = None,
        unlink: bool = False,  # if by default we want to consume documents
        extract_images: bool = False,
        **kwargs,
    ) -> dict[str, dict[str, str]]:
        text_dict: dict[str, dict[str, str]] = {}

        # 1) If the user passed real files, load them first:
        if document_filenames:
            # Coerce to Path
            paths = [Path(p) for p in document_filenames]
            real_contents = get_text_or_bytes_perfile_dict(
                paths, unlink, extract_images=extract_images
            )
            text_dict.update(real_contents)
            # optionally re-raise or continue with dummy data

        # 2) Count how many real text vs. binary entries we already have:
        existing_text = sum(1 for v in text_dict.values() if "text" in v)
        existing_bytes = sum(1 for v in text_dict.values() if "encodedbytes" in v)

        # 3) Generate dummy text entries to hit num_text
        for _ in range(max(num_text - existing_text, 0)):
            entry = factory.build(
                dict,
                filename=f"{uuid.uuid4()}.txt",
                text=factory.Faker("text").evaluate(None, None, {"locale": "en_US"}),
            )
            text_dict[entry["filename"]] = {"text": entry["text"]}

        # 4) Generate dummy binary entries to hit num_bytes
        for _ in range(max(num_bytes - existing_bytes, 0)):
            entry = factory.build(
                dict,
                filename=f"{uuid.uuid4()}.bin",
                encodedbytes=base64.b64encode(os.urandom(10)).decode("utf-8"),
            )
            text_dict[entry["filename"]] = {"encodedbytes": entry["encodedbytes"]}

        return text_dict


class AdminFactory(PersonFactory):
    """Factory for admin users (superuser + staff)."""

    is_superuser = True
    is_staff = True


class QuestionSetFactory(DjangoModelFactory):
    """Factory for QuestionSet model."""

    class Meta:
        model = QuestionSet

    label = factory.Sequence(lambda n: f"v{n}")
    display_name = factory.Faker("sentence", locale="en_US", nb_words=3, variable_nb_words=True)
    hide_from_display = False
    created_by = factory.SubFactory(PersonFactory)
    is_visible = True


class SectionFactory(DjangoModelFactory):
    """Factory for Section model."""

    class Meta:
        model = Section

    question_set = factory.SubFactory(QuestionSetFactory)
    title = factory.Faker("sentence", locale="en_US", nb_words=5, variable_nb_words=True)


class SubsectionFactory(DjangoModelFactory):
    """Factory for Subsection model."""

    class Meta:
        model = Subsection

    section = factory.SubFactory(SectionFactory)
    title = factory.Faker("sentence", locale="en_US", nb_words=5, variable_nb_words=True)


class QuestionFactory(DjangoModelFactory):
    """Factory for Question model."""

    class Meta:
        model = Question

    subsection = factory.SubFactory(SubsectionFactory)
    question_text = factory.Faker("sentence", locale="en_US", nb_words=10, variable_nb_words=True)


class AnswerFactory(DjangoModelFactory):
    """Factory for Answer model."""

    class Meta:
        model = Answer

    assay = factory.SubFactory(AssayFactory)
    question = factory.SubFactory(QuestionFactory)
    answer_text = factory.Faker("text", locale="en_US")
    accepted = False


class FileAssetFactory(DjangoModelFactory):
    """Factory for FileAsset model."""

    class Meta:
        model = FileAsset

    object_key = factory.LazyFunction(lambda: f"test/{uuid.uuid4()}/file.pdf")
    original_filename = factory.Faker("file_name", extension="pdf")
    content_type = "application/pdf"
    size_bytes = factory.Faker("random_int", min=1000, max=1000000)
    sha256 = factory.LazyFunction(lambda: uuid.uuid4().hex)
    status = FileAsset.Status.AVAILABLE
    uploaded_by = factory.SubFactory(PersonFactory)


class WorkspaceFactory(DjangoModelFactory):
    """Factory for Workspace model.

    NOTE: Workspace.save() auto-creates a WorkspaceMember with OWNER role.
    Do NOT manually create an owner WorkspaceMember — use workspace.memberships.first().
    """

    class Meta:
        model = Workspace

    owner = factory.SubFactory(PersonFactory)
    name = factory.Faker("company")
    description = factory.Faker("sentence", locale="en_US", nb_words=10, variable_nb_words=True)


class WorkspaceMemberFactory(DjangoModelFactory):
    """Factory for WorkspaceMember (non-owner members only).

    Do not use for the workspace owner — Workspace.save() creates that automatically.
    Use this for ADMIN or MEMBER entries only.
    """

    class Meta:
        model = WorkspaceMember

    workspace = factory.SubFactory(WorkspaceFactory)
    user = factory.SubFactory(PersonFactory)
    role = WorkspaceRole.MEMBER


class WorkspaceInvestigationFactory(DjangoModelFactory):
    """Factory for WorkspaceInvestigation.

    Does NOT grant guardian permissions — permission propagation is view-layer logic.
    Use this factory only to set up pre-existing DB state.
    """

    class Meta:
        model = WorkspaceInvestigation

    workspace = factory.SubFactory(WorkspaceFactory)
    investigation = factory.SubFactory(InvestigationFactory)
    added_by = factory.LazyAttribute(lambda obj: obj.workspace.owner)
