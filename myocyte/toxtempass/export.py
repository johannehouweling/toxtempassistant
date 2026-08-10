import io
import json
import logging
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import yaml
from django.core.serializers import serialize
from django.db.models import Count, Min
from django.http import FileResponse, HttpRequest, JsonResponse
from django.utils import timezone  # Import timezone utilities
from django.utils.text import slugify

from toxtempass import Config
from toxtempass.models import Assay, Person, Section
from toxtempass.utilities import log_processing_event

logger = logging.getLogger(__name__)

# Export control surface lives on Config (see toxtempass/__init__.py). These
# module-level aliases keep call sites terse and let tests patch them.
EXPORT_MIME_SUFFIX = Config.EXPORT_MIME_SUFFIX
EXPORT_MAPPING = Config.EXPORT_MAPPING
PANDOC_EXPORT_TYPES = Config.PANDOC_EXPORT_TYPES

# simple regexes to catch display‐math delimiters

MATH_BLOCK_START = re.compile(r"^\s*(\$\$|\\\[)")
MATH_BLOCK_END = re.compile(r"(\$\$|\\\])\s*$")

ExportAuthor = dict[str, str | None]
ExportInvestigationOwner = dict[str, str | None]
ExportAuthorMetadata = dict[
    str,
    str | list[str] | list[ExportAuthor] | ExportInvestigationOwner | None,
]


def _escape_pandoc_inline_footnote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("]", "\\]").replace("^", "\\^")


def quote_answer(text: str) -> str:
    r"""Take a multi-line answer_text, and return a Markdown fragment.

    where only non-math lines are prefixed with '> '.
    Display math blocks ( $$…$$ or \[…\] ) are emitted raw.
    """
    out = []
    in_math = False

    for line in text.splitlines():
        # start of a display-math block?
        if not in_math and MATH_BLOCK_START.match(line):
            in_math = True
            out.append(line)
            continue

        # end of a display-math block?
        if in_math:
            out.append(line)
            if MATH_BLOCK_END.search(line):
                in_math = False
            continue

        # otherwise normal text → quote it
        out.append(f"> {line}" if line.strip() else ">")  # keep blank lines too

    return "\n".join(out) + "\n\n"


def _person_export_name(person: Person | None) -> str | None:
    """Return a display name suitable for export metadata."""
    if person is None:
        return None
    full_name = person.get_full_name().strip()
    return full_name or person.email


def _export_optional_value(value: str | None) -> str | None:
    """Normalize optional string metadata values for export."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _person_export_author_entry(person: Person | None) -> ExportAuthor | None:
    """Return an export author entry with name, organization, ORCID iD, and email."""
    author_name = _person_export_name(person)
    if author_name is None:
        return None
    return {
        "name": author_name,
        "organization": _export_optional_value(person.organization),
        "orcid_id": _export_optional_value(person.orcid_id),
        "email": _export_optional_value(person.email),
    }


def _person_export_owner_entry(
    person: Person | None,
) -> ExportInvestigationOwner | None:
    """Return owner metadata with author fields plus ``email`` and ``orcid_id``."""
    author_entry = _person_export_author_entry(person)
    if author_entry is None:
        return None
    return {
        **author_entry,
        "email": _export_optional_value(person.email),
        "orcid_id": _export_optional_value(person.orcid_id),
    }


def get_assay_export_authors(assay: Assay) -> list[ExportAuthor]:
    """Return ordered author entries with name, organization, ORCID iD, and email.

    Ordering rules:
    1. First author is the assay creator when available.
    2. Remaining contributors come from Answer.history, ranked by number of
       edits and then by first contribution date.
    3. Assay owner is listed last unless they are already first as the creator.
    """
    owner_id = assay.study.investigation.owner_id
    creator_id = assay.created_by_id

    historical_answer_model = assay.answers.model.history.model
    contributor_rows = list(
        historical_answer_model.objects.filter(
            assay_id=assay.id,
            history_user__isnull=False,
        )
        .values("history_user_id")
        .annotate(
            contribution_count=Count("history_id"),
            first_contribution=Min("history_date"),
        )
        .order_by("-contribution_count", "first_contribution", "history_user_id")
    )
    contributor_ids = [
        row["history_user_id"]
        for row in contributor_rows
    ]

    first_author_id = creator_id
    if first_author_id is None:
        first_author_id = next(
            (user_id for user_id in contributor_ids if user_id != owner_id),
            None,
        )

    ordered_ids: list[int] = []
    if first_author_id is not None:
        ordered_ids.append(first_author_id)
    ordered_ids.extend(
        user_id
        for user_id in contributor_ids
        if user_id not in {first_author_id, owner_id}
    )
    if owner_id is not None and owner_id != first_author_id:
        ordered_ids.append(owner_id)
    ordered_ids = list(dict.fromkeys(ordered_ids))

    people_by_id = Person.objects.only(
        "first_name",
        "last_name",
        "email",
        "organization",
        "orcid_id",
    ).in_bulk(ordered_ids)
    authors: list[ExportAuthor] = []
    for user_id in ordered_ids:
        author_entry = _person_export_author_entry(people_by_id.get(user_id))
        if author_entry is not None:
            authors.append(author_entry)
    return authors


def get_assay_export_author_metadata(assay: Assay) -> ExportAuthorMetadata:
    """Return export author metadata for an assay."""
    authors = get_assay_export_authors(assay)
    author_names = [author["name"] for author in authors]
    investigation_owner = _person_export_owner_entry(assay.study.investigation.owner)
    return {
        "author": author_names,
        "authors": authors,
        "main_author": author_names[0] if author_names else None,
        "co_authors": author_names[1:],
        "investigation_owner": investigation_owner,
    }


def generate_json_from_assay(assay: Assay) -> dict | None:
    """Generate Json from assay."""
    try:
        # Set the timezone to Amsterdam
        amsterdam_tz = timezone.get_fixed_timezone(
            1
        )  # UTC+1 for Amsterdam (standard time)
        current_time = timezone.now().astimezone(
            amsterdam_tz
        )  # Current time in Amsterdam timezone

        # Source the model identity from AssayCost (the source of truth recorded
        # by process_llm_async), not Config.model — Config.model is the import-time
        # registry default and does not reflect what actually ran on this assay.
        # An assay regenerated with multiple models has multiple AssayCost rows.
        # The info URL mirrors the link used by the off-canvas LLM signature
        # badge (templates/.../user_offcanvas.html), so the export and the UI
        # point at the same Azure catalog page.
        from urllib.parse import quote

        models_used: list[dict] = []
        for c in assay.costs.order_by("updated_at"):
            info_url = (
                f"https://ai.azure.com/catalog/models/{quote(c.model_id, safe='')}"
                if c.model_id
                else ""
            )
            models_used.append(
                {
                    "model_key": c.model_key,
                    "model_id": c.model_id,
                    "info_url": info_url,
                }
            )

        if models_used:
            model_summary = ", ".join(
                f"{m['model_id']} ({m['model_key']})"
                if m["model_id"]
                else m["model_key"]
                for m in models_used
            )
            # De-duplicate URLs while preserving order; drop empties.
            seen_urls: set[str] = set()
            ordered_urls = [
                u for m in models_used
                if (u := m["info_url"]) and not (u in seen_urls or seen_urls.add(u))
            ]
            model_info_url_value = ", ".join(ordered_urls) if ordered_urls else ""
        else:
            model_summary = "Not recorded"
            model_info_url_value = ""

        # Prepare the data structure
        author_metadata = get_assay_export_author_metadata(assay)
        export_data = {
            "metadata": {
                # Current date and time in ISO format
                "creation_date": current_time.isoformat(),
                # Filename for the export
                "filename": f"toxtemp_{slugify(assay.title)}",
                # Replace with your actual website name
                "reference_toxtemp": getattr(Config, "reference_toxtemp", None),
                "website": "toxtempassistant.vhp4safety.nl",
                # Structured per-run LLM identities (machine-readable companion to
                # the human-readable `config.model` string).
                "models_used": models_used,
                # Trimmed config for reproducibility
                # (PII and developer-only fields omitted)
                "config": {
                    "model": model_summary,
                    "model_info_url": model_info_url_value
                    or getattr(Config, "model_info_url", None),
                    # Same DOI link the off-canvas uses for the ToxTempAssistant
                    # paper badge. Config has no plain "reference_toxtempassistant"
                    # attribute — the previous getattr silently resolved to None.
                    "reference_toxtempassistant": getattr(
                        Config, "reference_toxtempassistant_paper", None
                    ),
                    "reference_toxtemp": getattr(Config, "reference_toxtemp", None),
                    "website": "toxtempassistant.vhp4safety.nl",
                    "version": getattr(Config, "version", None),
                    "github_repo_url": getattr(Config, "github_repo_url", None),
                    "git_hash": getattr(Config, "git_hash", None),
                    "license_url": getattr(Config, "license_url", None),
                },
                **author_metadata,
            },
            "investigation": json.loads(serialize("json", [assay.study.investigation]))[
                0
            ],
            "study": json.loads(serialize("json", [assay.study]))[0],
            "assay": json.loads(serialize("json", [assay]))[0],
            "answers": json.loads(serialize("json", assay.answers.all())),
        }

        # Add questions and their corresponding answers
        questions_with_answers = []
        for answer in assay.answers.all():
            question_data = json.loads(serialize("json", [answer.question]))[0]
            questions_with_answers.append(
                {
                    "question": question_data,
                    "answer": answer.answer_text,
                    "source": answer.answer_documents,
                }
            )
        export_data["questions_with_answers"] = questions_with_answers

        # Add sections and subsections with questions and answers.
        # Only walk the sections of this assay's questionnaire version —
        # Section.objects.all() would also include every other QuestionSet in
        # the DB, whose questions can never match this assay's answers and so
        # would all render as "Answer not found in documents."
        question_set_id = assay.question_set_id
        if question_set_id is None:
            # Legacy assays predate the question_set FK; derive it from the
            # questions their answers point to.
            question_set_id = assay.answers.values_list(
                "question__subsection__section__question_set_id", flat=True
            ).first()
        answers_by_question_id = {
            answer.question_id: answer for answer in assay.answers.all()
        }
        sections = []
        for section in Section.objects.filter(
            question_set_id=question_set_id
        ).prefetch_related("subsections__questions"):
            section_data = {
                "section": json.loads(serialize("json", [section]))[0],
                "subsections": [],
            }
            for subsection in section.subsections.all():
                subsection_data = {
                    "subsection": json.loads(serialize("json", [subsection]))[0],
                    "questions_with_answers": [],
                }
                # Add questions and answers for this subsection
                for question in subsection.questions.all():
                    # Find the corresponding answer, if any
                    answer = answers_by_question_id.get(question.id)
                    answer_text = answer.answer_text if answer else ""
                    subsection_data["questions_with_answers"].append(
                        {
                            "question": json.loads(serialize("json", [question]))[0],
                            "answer": answer_text,
                        }
                    )

                section_data["subsections"].append(subsection_data)
            sections.append(section_data)

        export_data["sections"] = sections
        return export_data

    except Assay.DoesNotExist:
        return None


def generate_markdown_from_assay(assay: Assay) -> str:
    """Generate markdown from assay."""
    export_data = generate_json_from_assay(assay)
    # Start with metadata
    markdown = []
    markdown.append("## Metadata\n")
    markdown.append(f"- **Creation Date:** {export_data['metadata']['creation_date']}\n")
    markdown.append(f"- **Filename:** {export_data['metadata']['filename']}\n")
    markdown.append(f"- **Website:** {export_data['metadata']['website']}\n")
    if export_data["metadata"].get("authors"):
        markdown.append("- **Authors:**\n")
        last_author_index = len(export_data["metadata"]["authors"]) - 1
        for index, author in enumerate(export_data["metadata"]["authors"]):
            author_line = author["name"]
            if author.get("organization"):
                author_line += f" ({author['organization']})"
            if author.get("orcid_id"):
                author_line += f" — ORCID iD: {author['orcid_id']}"
            if index == last_author_index and author.get("email"):
                author_line += f" — Email: {author['email']}"
            markdown.append(f"  - {author_line}\n")
    if export_data["metadata"].get("main_author"):
        markdown.append(f"- **Main Author:** {export_data['metadata']['main_author']}\n")
    if export_data["metadata"].get("co_authors"):
        markdown.append(
            "- **Co-authors:** "
            + ", ".join(export_data["metadata"]["co_authors"])
            + "\n"
        )
    markdown.append("\n## ToxTempAssistant configuration\n")
    for key, value in export_data["metadata"]["config"].items():
        markdown.append(f"- {key}: {value}\n")
    markdown.append("\n")

    # Include investigation details
    investigation_title = export_data["investigation"]["fields"]["title"]
    investigation_description = export_data["investigation"]["fields"]["description"]
    markdown.append("# Investigation\n")
    markdown.append(f"- **Title:** {investigation_title}\n")
    markdown.append(f"- **Description:** {investigation_description}\n")
    markdown.append("\n")

    # Include study details
    study_title = export_data["study"]["fields"]["title"]
    study_description = export_data["study"]["fields"]["description"]
    markdown.append("# Study\n")
    markdown.append(f"- **Title:** {study_title}\n")
    markdown.append(f"- **Description:** {study_description}\n")
    markdown.append("\n")

    # Include assay details
    assay_title = export_data["assay"]["fields"]["title"]
    markdown.append("# Assay\n")
    markdown.append(f"- **Title:** {assay_title}\n")
    markdown.append("\n")

    # Directly use the sections from export_data
    sections = export_data.get("sections", [])

    # Add sections and subsections to Markdown
    for section in sections:
        # Add section title
        section_title = section["section"]["fields"][
            "title"
        ]  # Adjust based on your model's field names
        markdown.append(f"# {section_title}\n")  # Section title

        for subsection in section["subsections"]:
            # Add subsection title
            subsection_title = subsection["subsection"]["fields"][
                "title"
            ]  # Adjust based on your model's field names
            markdown.append(f"## {subsection_title}\n")  # Subsection title

            for qa in subsection["questions_with_answers"]:
                question_text = qa["question"]["fields"][
                    "question_text"
                ]  # Adjust based on your model's field names
                answer_text = (
                    qa["answer"] if qa["answer"] else "Answer not found in documents."
                )

                # Add question and answer in a list format
                markdown.append(f"{question_text}\n\n")
                markdown.append(quote_answer(answer_text))

        markdown.append("\n")  # Add an empty line for spacing between sections

    return "".join(markdown)


def get_create_meta_data_yaml(
    request: HttpRequest, assay: Assay, file_path: Path, export_type: str = "pdf"
) -> Path:
    """Create meta data yaml file for pandoc.

    Args:
        request: The current HTTP request (used for author metadata).
        assay: The assay being exported.
        file_path: Destination file path; the YAML file is written alongside it.
        export_type: The export format (e.g. ``"pdf"``, ``"tex"``).  When
            ``"tex"``, fontspec and unicode-math are wrapped in an ``iftex``
            conditional so the generated ``.tex`` file also compiles with
            pdfLaTeX.

    """
    # get date:
    # Define the Amsterdam timezone (UTC+1)
    amsterdam_tz = timezone.get_fixed_timezone(1)  # 1 means UTC+1

    # Get the current time in UTC and convert it to Amsterdam time
    current_time = timezone.now().astimezone(amsterdam_tz)

    # Optionally, you can extract the date from the current_time if needed
    current_date = current_time.date()

    # For .tex output the user will compile themselves, wrap fontspec/unicode-math
    # in \ifPDFTeX…\else…\fi so the file also compiles with pdfLaTeX (which does
    # not support fontspec).  All other export types are processed by LuaLaTeX
    # inside the app, so the unconditional fontspec/unicode-math packages are fine.
    if export_type == "tex":
        font_block = (
            "\\usepackage{iftex}\n"
            "\\ifPDFTeX\n"
            "  \\usepackage[T1]{fontenc}\n"
            "  \\usepackage[utf8]{inputenc}\n"
            "\\else\n"
            "  \\usepackage{fontspec}\n"
            "  \\usepackage{unicode-math}\n"
            "  \\setmainfont{TeX Gyre Termes}\n"
            "  \\setmathfont{TeX Gyre Termes Math}\n"
            "\\fi"
        )
        header_includes = [
            r"\usepackage{amsmath}",
            font_block,
            r"\usepackage[a4paper, margin=3cm]{geometry}",
        ]
    else:
        header_includes = [
            r"\usepackage{amsmath}",
            r"\usepackage{fontspec}",
            r"\usepackage{unicode-math}",
            r"\setmainfont{TeX Gyre Termes}",
            r"\setmathfont{TeX Gyre Termes Math}",
            r"\usepackage[a4paper, margin=3cm]{geometry}",
        ]

    author_metadata = get_assay_export_author_metadata(assay)
    metadata_dict = {
        "author": author_metadata["author"],
        "authors": author_metadata["authors"],
        "date": str(current_date),  # Current date;
        "keywords": (
            "metadata template, "
            "cell-based toxicological test methods, "
            "New Approach Methodologies"
        ),  # Example keywords; customize as required
        "header-includes": header_includes,
        "title": f"ToxTemp for Test Method: {assay.title}",
        "toc": "true",
        "toc-title": "Table of Contents",
    }
    yaml_file_path = file_path.with_name("yaml" + file_path.name).with_suffix(".yaml")
    with open(yaml_file_path, "w") as file:
        yaml.dump(metadata_dict, file, default_flow_style=False)
    return yaml_file_path


def export_assay_to_file(
    request: HttpRequest, assay: Assay, export_type: str
) -> FileResponse:
    """Export assay to file."""
    # EXPORT_MAPPING (defined in toxtempass/__init__.py) is the single security
    # gate: only types with both trusted Pandoc options and known MIME/suffix
    # metadata are permitted.
    if export_type not in EXPORT_MAPPING or export_type not in EXPORT_MIME_SUFFIX:
        return JsonResponse({"error": "Invalid export type"}, status=400)
    mapped_suffix = EXPORT_MIME_SUFFIX[export_type]["suffix"]
    file_name = f"toxtemp_{slugify(assay.title)}{mapped_suffix}"

    # All export artefacts are written to a short-lived temp directory; nothing
    # is stored permanently on the container filesystem.
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / file_name

        export_data = None
        if export_type == "json":
            export_data = generate_json_from_assay(assay)
            with file_path.open("w", encoding="utf-8") as json_file:
                json.dump(export_data, json_file, indent=4)

        elif export_type in PANDOC_EXPORT_TYPES:
            # Generate the markdown file
            export_data = generate_markdown_from_assay(assay)
            md_file_path = file_path.with_name(f"{file_path.stem}_md").with_suffix(
                ".md"
            )
            with md_file_path.open("w", encoding="utf-8") as md_file:
                md_file.write(export_data)

            yaml_metadata_file_path = get_create_meta_data_yaml(
                request, assay, file_path, export_type
            )

            # Convert the markdown file to the requested format using Pandoc
            pandoc_command = [
                "pandoc",
                str(md_file_path),
                "--from=markdown+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash",
                f"--metadata-file={str(yaml_metadata_file_path)}",
                "--toc",
            ]
            # Add ONLY safe mapped Pandoc options
            pandoc_command.extend(EXPORT_MAPPING[export_type])
            pandoc_command.extend(["-o", str(file_path)])

            try:
                subprocess.run(pandoc_command, check=True)  # noqa: S603
            except subprocess.CalledProcessError as e:
                corr_id = uuid.uuid4().hex[:8]
                logger.exception(
                    "Pandoc conversion failed [corr=%s] for assay %s", corr_id, assay.id
                )
                log_processing_event(assay, f"[{corr_id}] {type(e).__name__}: {e}")
                assay.save()
                return JsonResponse(
                    {
                        "error": f"Export failed (ref {corr_id}). "
                        "Please contact support if the issue persists."
                    },
                    status=500,
                )
            except Exception as e:
                corr_id = uuid.uuid4().hex[:8]
                logger.exception(
                    "Unexpected export error [corr=%s] for assay %s", corr_id, assay.id
                )
                log_processing_event(assay, f"[{corr_id}] {type(e).__name__}: {e}")
                assay.save()
                return JsonResponse(
                    {
                        "error": f"Export failed (ref {corr_id}). "
                        "Please contact support if the issue persists."
                    },
                    status=500,
                )

        # Read the output file into memory so it can be served after the temp
        # directory is cleaned up.
        file_content = file_path.read_bytes()

    # Prepare the response for the generated file
    try:
        return FileResponse(
            io.BytesIO(file_content),
            as_attachment=True,
            filename=file_name,
            content_type=EXPORT_MIME_SUFFIX[export_type]["mime_type"],
        )
    except Exception as e:
        corr_id = uuid.uuid4().hex[:8]
        logger.exception(
            "FileResponse construction failed [corr=%s] for assay %s",
            corr_id, assay.id,
        )
        log_processing_event(assay, f"[{corr_id}] {type(e).__name__}: {e}")
        assay.save()
        return JsonResponse(
            {
                "error": f"Export failed (ref {corr_id}). "
                "Please contact support if the issue persists."
            },
            status=500,
        )
