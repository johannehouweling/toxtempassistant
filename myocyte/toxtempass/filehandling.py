import base64
import csv
import hashlib
import json
import logging
import mimetypes
import tempfile
import warnings
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile
from zipfile import ZipFile as ZipFileLib

import tiktoken
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest
from langchain_community.document_loaders import (
    BSHTMLLoader,
    TextLoader,
    UnstructuredWordDocumentLoader,
)
from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image
from pypdf import PdfReader
from pypdf._page import PageObject

from toxtempass import config
from toxtempass.llm import get_llm
from toxtempass.models import AnswerFile, Assay, FileAsset, FileDownloadLog, Person

try:
    import openpyxl
except ImportError:  # pragma: no cover - optional dependency
    openpyxl = None

try:
    import xlrd  # legacy .xls (openpyxl cannot read these)
except ImportError:  # pragma: no cover - optional dependency
    xlrd = None

try:
    from pptx import Presentation  # .pptx slide text
except ImportError:  # pragma: no cover - optional dependency
    Presentation = None

# Suppress PyPDF warnings about image and mask size mismatches
# These are benign warnings that don't affect functionality
warnings.filterwarnings(
    "ignore",
    message=".*image and mask size not matching.*",
    category=UserWarning,
)

logger = logging.getLogger("llm")


IMAGE_SUFFIX_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".bmp": "BMP",
    ".tiff": "TIFF",
    ".webp": "WEBP",
}

# Target format for all image conversions - WebP is most efficient for base64/tokens
# This reduces API costs by minimizing the base64 payload size
TARGET_IMAGE_FORMAT = "WEBP"
TARGET_IMAGE_MIME = "image/webp"
TARGET_IMAGE_QUALITY = 85  # WebP quality (0-100), 85 is good balance of quality/size

DEFAULT_IMAGE_MIME = "image/png"
MAX_TABLE_ROWS = 50


def _truncate_context(text: str | None, limit: int = 1500) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


def _describe_image(
    encoded_image: str,
    filename: str,
    mime_type: str | None,
    page_context: str | None = None,
) -> str:
    """Generate a textual description for an image using the configured LLM."""
    mime = mime_type or DEFAULT_IMAGE_MIME
    try:
        llm = get_llm()
        system_message = SystemMessage(content=config.image_description_prompt)
        human_content: list[dict[str, object]] = []
        context = _truncate_context(page_context)
        if context:
            human_content.append({"type": "text", "text": f"PAGE CONTEXT:\n{context}"})
        human_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{encoded_image}",
                    "detail": "high",
                },
            }
        )
        response = llm.invoke([system_message, HumanMessage(content=human_content)])
        description = (getattr(response, "content", "") or "").strip()
        if description:
            normalized = description.strip()
            upper = normalized.upper()
            if upper == "IGNORE_IMAGE":
                logger.info(
                    "Descriptor returned IGNORE_IMAGE for %s; skipping.", filename
                )
                return ""
            if upper.startswith("I'M UNABLE") or upper.startswith("IT SEEMS"):
                return ""
            return normalized
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Image description failed for %s: %s", filename, exc)

    return ""


def _format_image_description(
    description: str,
    source_document: str,
    page_number: int | None = None,
) -> str:
    doc_name = Path(source_document).name
    if page_number is not None:
        prefix = f"Image summary from {doc_name} (page {page_number})"
    else:
        prefix = f"Image summary from {doc_name}"
    return f"{prefix}:\n{description}"


def summarize_image_entries(doc_dict: dict[str, dict[str, str]]) -> None:
    """Convert encoded image entries in-place to textual summaries."""
    for key in list(doc_dict.keys()):
        meta = doc_dict[key]
        if "encodedbytes" not in meta:
            continue

        description = _describe_image(
            meta.get("encodedbytes", ""),
            Path(key).name,
            meta.get("mime_type"),
            meta.get("page_context"),
        )
        if not description:
            logger.info("Removing image %s due to empty or ignored description.", key)
            doc_dict.pop(key)
            continue

        doc_dict[key] = {
            "text": _format_image_description(
                description,
                meta.get("source_document", key),
                meta.get("page_number"),
            ),
            "source_document": meta.get("source_document", key),
            "origin": "image_description",
        }


def _convert_image_to_webp(
    image_bytes: bytes, source_format: str | None = None
) -> tuple[bytes | None, str | None]:
    """Convert image bytes to WebP format for optimal token efficiency.

    WebP provides the best compression ratio, reducing base64 size and API token usage.
    Filters out images smaller than configured minimum dimensions.

    Args:
        image_bytes: Raw image bytes
        source_format: Original format name (for logging)

    Returns:
        Tuple of (converted_bytes, mime_type) or (None, None) if image is too small

    Raises:
        Exception: If conversion fails
    """
    img = Image.open(BytesIO(image_bytes))
    
    # Filter out small images (icons, bullets, decorative elements)
    if img.width < config.min_image_width or img.height < config.min_image_height:
        logger.debug(
            "Skipping small image (%dx%d, min: %dx%d) from %s",
            img.width,
            img.height,
            config.min_image_width,
            config.min_image_height,
            source_format or "unknown"
        )
        return None, None
    
    output = BytesIO()
    # Convert to RGB if necessary (WebP supports RGBA but not all modes)
    if img.mode == "RGBA":
        pass  # WebP supports RGBA
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(output, format=TARGET_IMAGE_FORMAT, quality=TARGET_IMAGE_QUALITY)
    logger.debug(
        "Converted %s image (%dx%d) to %s",
        source_format or "unknown",
        img.width,
        img.height,
        TARGET_IMAGE_FORMAT
    )
    return output.getvalue(), TARGET_IMAGE_MIME


def _extract_images_from_pdf_page(
    page: PageObject, source_path: Path, page_number: int
) -> dict[str, dict[str, str]]:
    """Extract images from a single PDF page into the document dictionary format.

    All images are converted to WebP format for optimal token efficiency when
    sending to OpenAI's Vision API.
    """
    images: dict[str, dict[str, str]] = {}
    pdf_images = getattr(page, "images", []) or []

    for idx, image_obj in enumerate(pdf_images, start=1):
        image_bytes = getattr(image_obj, "data", None)
        if image_bytes is None and hasattr(image_obj, "get_data"):
            try:
                image_bytes = image_obj.get_data()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to read PDF image bytes (%s page %s idx %s): %s",
                    source_path,
                    page_number,
                    idx,
                    exc,
                )
                continue
        if not image_bytes:
            continue

        image_name = getattr(image_obj, "name", "") or f"image{idx}"
        image_format = (
            getattr(image_obj, "image_format", None)
            or Path(image_name).suffix.lstrip(".")
            or None
        )

        # Always convert to WebP for optimal token efficiency
        try:
            image_bytes, mime_type = _convert_image_to_webp(image_bytes, image_format)
            # Skip if image was too small (filtered out)
            if image_bytes is None or mime_type is None:
                continue
        except Exception as exc:
            logger.warning(
                "Failed to convert %s image to WebP (%s page %s): %s. Skipping image.",
                image_format or "unknown",
                source_path,
                page_number,
                exc,
            )
            continue

        encoded = base64.b64encode(image_bytes).decode("utf-8")
        key = (
            f"{str(source_path)}#page{page_number}_"
            f"{image_name if image_name else f'image{idx}'}"
        )
        images[key] = {
            "encodedbytes": encoded,
            "mime_type": mime_type,
            "source_document": str(source_path),
            "origin": "embedded",
            "page_number": page_number,
        }

    return images


def _extract_images_from_docx(path: Path) -> dict[str, dict[str, str]]:
    """Extract embedded images from a DOCX file.

    All images are converted to WebP format for optimal token efficiency.
    """
    images: dict[str, dict[str, str]] = {}
    try:
        with ZipFile(path) as docx_zip:
            for info in docx_zip.infolist():
                if not info.filename.startswith("word/media/"):
                    continue
                filename = Path(info.filename).name
                data = docx_zip.read(info)
                if not data:
                    continue

                # Convert to WebP for optimal token efficiency
                original_format = Path(filename).suffix.lstrip(".")
                try:
                    data, mime_type = _convert_image_to_webp(data, original_format)
                    # Skip if image was too small (filtered out)
                    if data is None or mime_type is None:
                        continue
                except Exception as exc:
                    logger.warning(
                        "Failed to convert DOCX image %s to WebP: %s. Skipping.",
                        filename,
                        exc,
                    )
                    continue

                encoded = base64.b64encode(data).decode("utf-8")
                key = f"{str(path)}#{filename}"
                images[key] = {
                    "encodedbytes": encoded,
                    "mime_type": mime_type,
                    "source_document": str(path),
                    "origin": "embedded",
                    "page_number": None,
                }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to extract images from DOCX %s: %s", path, exc)
    return images


def _read_json_file(path: Path) -> str:
    """Read a JSON file and return a pretty-printed string representation."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse JSON %s: %s. Returning raw text.", path, exc)
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Unexpected error reading JSON %s: %s", path, exc)
        return path.read_text(encoding="utf-8", errors="replace")


def _read_csv_file(path: Path, max_rows: int = MAX_TABLE_ROWS) -> str:
    """Read a CSV file and return a truncated textual representation."""
    lines: list[str] = []
    truncated = False
    try:
        with path.open("r", encoding="utf-8", newline="", errors="replace") as f:
            reader = csv.reader(f)
            for idx, row in enumerate(reader):
                if idx >= max_rows:
                    truncated = True
                    break
                lines.append(", ".join(row))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read CSV %s: %s", path, exc)
        return path.read_text(encoding="utf-8", errors="replace")

    if not lines:
        lines.append("(empty csv)")
    if truncated:
        lines.append(f"... (truncated after {max_rows} rows)")
    return "\n".join(lines)


def _read_xlsx_file(path: Path, max_rows: int = MAX_TABLE_ROWS) -> str:
    """Read an XLSX workbook and return a textual summary per sheet."""
    if openpyxl is None:
        logger.warning("openpyxl not installed; cannot process %s", path)
        return "Excel parsing unavailable (openpyxl not installed)."

    try:
        workbook = openpyxl.load_workbook(
            path, read_only=True, data_only=True, keep_links=False
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to open XLSX %s: %s", path, exc)
        return "Unable to read Excel file."

    sheet_texts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            lines: list[str] = []
            truncated = False
            for idx, row in enumerate(sheet.iter_rows(values_only=True)):
                if idx >= max_rows:
                    truncated = True
                    break
                formatted = ", ".join("" if cell is None else str(cell) for cell in row)
                lines.append(formatted)
            if not lines:
                lines.append("(empty sheet)")
            if truncated:
                lines.append(f"... (truncated after {max_rows} rows)")
            sheet_texts.append(f"Sheet: {sheet.title}\n" + "\n".join(lines))
    finally:
        workbook.close()

    return "\n\n".join(sheet_texts) if sheet_texts else "(no sheets found)"


def _read_delimited_text(path: Path, max_rows: int = MAX_TABLE_ROWS) -> str:
    """Read a delimited TEXT file (detecting BOM/encoding), preserving rows.

    Many instruments export tab-delimited text with a misleading ``.xls``
    extension (often UTF-16). This keeps the rows as-is and truncates.
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16"
    elif raw[:3] == b"\xef\xbb\xbf":
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except (LookupError, UnicodeError):  # pragma: no cover - defensive
        text = raw.decode("latin-1", errors="replace")

    lines = text.splitlines()
    truncated = len(lines) > max_rows
    body = "\n".join(lines[:max_rows]) if lines else "(empty file)"
    if truncated:
        body += f"\n... (truncated after {max_rows} rows)"
    return body


def _read_xls_file(path: Path, max_rows: int = MAX_TABLE_ROWS) -> str:
    """Read a legacy ``.xls`` workbook and return a textual summary per sheet.

    Uses ``xlrd`` for genuine binary ``.xls`` (``openpyxl`` only handles OOXML
    ``.xlsx``). Falls back to delimited-text reading when the file is actually
    text mislabeled ``.xls`` (e.g. UTF-16 TSV instrument exports), which ``xlrd``
    rejects with a "BOF record" error.
    """
    if xlrd is not None:
        try:
            workbook = xlrd.open_workbook(str(path))
        except Exception as exc:
            logger.info(
                "xlrd could not parse %s as binary .xls (%s); trying text fallback",
                path, exc,
            )
        else:
            sheet_texts: list[str] = []
            for sheet in workbook.sheets():
                lines: list[str] = []
                truncated = False
                for idx in range(sheet.nrows):
                    if idx >= max_rows:
                        truncated = True
                        break
                    row = sheet.row_values(idx)
                    lines.append(
                        ", ".join("" if cell is None else str(cell) for cell in row)
                    )
                if not lines:
                    lines.append("(empty sheet)")
                if truncated:
                    lines.append(f"... (truncated after {max_rows} rows)")
                sheet_texts.append(f"Sheet: {sheet.name}\n" + "\n".join(lines))
            return "\n\n".join(sheet_texts) if sheet_texts else "(no sheets found)"

    # Either xlrd is unavailable or the file is text mislabeled as .xls.
    try:
        return _read_delimited_text(path, max_rows)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read %s as .xls or text: %s", path, exc)
        return "Unable to read legacy Excel file."


def _pptx_shape_texts(shapes: object) -> list[str]:
    """Recursively collect text from pptx shapes, incl. groups and tables.

    Plain text frames, table cells, and shapes nested inside group shapes are all
    extracted (the naive ``slide.shapes`` loop misses grouped/tabular content).
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    out: list[str] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            out.extend(_pptx_shape_texts(shape.shapes))
            continue
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    out.append(line)
            continue
        if shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text:
                out.append(text)
    return out


def _read_pptx_file(path: Path) -> str:
    """Read a ``.pptx`` deck and return the text of each slide."""
    if Presentation is None:
        logger.warning("python-pptx not installed; cannot process %s", path)
        return "PowerPoint parsing unavailable (python-pptx not installed)."

    try:
        prs = Presentation(str(path))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to open PPTX %s: %s", path, exc)
        return "Unable to read PowerPoint file."

    slide_texts: list[str] = []
    for n, slide in enumerate(prs.slides, start=1):
        fragments = _pptx_shape_texts(slide.shapes)
        body = "\n".join(fragments) if fragments else "(no text)"
        slide_texts.append(f"Slide {n}:\n{body}")

    return "\n\n".join(slide_texts) if slide_texts else "(no slides found)"


def stringyfy_text_dict(text_dict: dict[str, dict[str, str]]) -> str:
    """Convert text dictionary to a single string."""
    return "\n\n".join(
        f"--- {Path(fp).name} ---\n{meta['text']}"
        for fp, meta in text_dict.items()
        if "text" in meta
    )


def estimate_token_count(text: str) -> int:
    """Estimate the number of tokens in *text* using tiktoken (cl100k_base)."""
    if not text:
        return 0
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        logger.debug(
            "tiktoken encoding failed; using char-based token estimate.",
            exc_info=True,
        )
        return max(1, len(text) // 4)


_TRUNCATION_MARKER = (
    "\n\n[... context truncated: uploaded documents exceeded the "
    "configured context-window limit ...]"
)


def truncate_context_to_token_limit(
    text: str,
    max_tokens: int,
) -> tuple[str, bool]:
    """Truncate *text* so it fits within *max_tokens*.

    Returns a ``(possibly_truncated_text, was_truncated)`` tuple. When
    truncation is needed the returned string ends with a visible marker so
    downstream readers know that content was dropped. The marker's own token
    cost is budgeted, so the final result is guaranteed to fit under
    ``max_tokens``.

    ``max_tokens <= 0`` returns ``("", True)`` — there is no budget for any
    payload, and silently passing a non-positive limit through the slicing
    arithmetic would otherwise yield a result that exceeds the limit.
    """
    if not text:
        return text, False
    if max_tokens <= 0:
        return "", True

    token_count = estimate_token_count(text)
    if token_count <= max_tokens:
        return text, False

    marker_tokens = estimate_token_count(_TRUNCATION_MARKER)
    body_budget = max_tokens - marker_tokens
    if body_budget <= 0:
        # Marker alone wouldn't fit under the budget — drop everything.
        return "", True

    ratio = body_budget / token_count
    approx_chars = int(len(text) * ratio * config.truncation_safety_margin)
    truncated = text[:approx_chars].rstrip()
    result = truncated + _TRUNCATION_MARKER

    # The proportional cut is heuristic (chars-per-token varies). Verify and
    # shave the body until the final result actually fits. Each iteration
    # drops ~10% of the body so this terminates in O(log(len)).
    while estimate_token_count(result) > max_tokens and truncated:
        truncated = truncated[: int(len(truncated) * 0.9)].rstrip()
        result = truncated + _TRUNCATION_MARKER

    return result, True


def get_text_or_bytes_perfile_dict(
    document_filenames: list[str | Path], unlink: bool = True, extract_images: bool = True
) -> dict[str, dict[str, str]]:
    """Load content from a list of documents.

    Args:
    document_filenames (list of str): List of file paths to the documents.
    unlink (bool): if files shall be deleted afterwards
    extract_images (bool): whether to extract images from PDFs and DOCX files

    Returns:
    dict: A dictionary where keys are filenames (or synthetic identifiers) and values
    are dictionaries containing extracted text. When ``extract_images`` is True,
    images are summarized into text entries instead of returning raw bytes.

    """
    # coherce paths of type str to Path elements:
    document_filenames: list[Path] = [Path(path) for path in document_filenames]
    document_contents = {}

    for context_filename in document_filenames:
        text = None
        suffix = context_filename.suffix.lower()

        try:
            if suffix == ".pdf":
                with open(context_filename, "rb") as file:
                    reader = PdfReader(file)
                    paragraphs: list[str] = []
                    for page_number, page in enumerate(reader.pages, start=1):
                        try:
                            page_text = page.extract_text() or ""
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.debug(
                                "Failed to extract text from %s page %s: %s",
                                context_filename,
                                page_number,
                                exc,
                            )
                            page_text = ""
                        if page_text.strip():
                            paragraphs.append(page_text.strip())

                        if extract_images:
                            images = _extract_images_from_pdf_page(
                                page, context_filename, page_number
                            )
                            if images:
                                page_context = _truncate_context(page_text)
                                for key in images:
                                    images[key]["page_context"] = page_context
                                document_contents.update(images)

                    text = "\n".join(paragraphs)

            elif suffix in [".txt", ".md"]:
                loader = TextLoader(file_path=context_filename, autodetect_encoding=True)
                text = loader.load()[0].page_content

            elif suffix == ".html":
                loader = BSHTMLLoader(context_filename, open_encoding="utf-8")
                docs = loader.load()  # returns a list of Documents
                text = docs[0].page_content.replace("\n", "") if docs else ""

            elif suffix == ".docx":
                loader = UnstructuredWordDocumentLoader(str(context_filename))
                text = loader.load()[0].page_content
                if extract_images:
                    images = _extract_images_from_docx(context_filename)
                    if images:
                        doc_context = _truncate_context(text)
                        for key in images:
                            images[key]["page_context"] = doc_context
                        document_contents.update(images)

            elif suffix == ".json":
                text = _read_json_file(context_filename)

            elif suffix == ".csv":
                text = _read_csv_file(context_filename)

            elif suffix == ".xlsx":
                text = _read_xlsx_file(context_filename)

            elif suffix == ".xls":
                text = _read_xls_file(context_filename)

            elif suffix == ".pptx":
                text = _read_pptx_file(context_filename)

            elif suffix in config.IMAGE_ACCEPT_FILES:
                # Convert all uploaded images to WebP for optimal token efficiency
                with open(context_filename, "rb") as img_file:
                    image_bytes = img_file.read()
                try:
                    image_bytes, mime_type = _convert_image_to_webp(image_bytes, suffix)
                    # Skip if image was too small (filtered out)
                    if image_bytes is None or mime_type is None:
                        logger.info("Skipping uploaded image %s (too small)", context_filename)
                        continue
                except Exception as exc:
                    logger.warning(
                        "Failed to convert uploaded image %s to WebP: %s. Skipping.",
                        context_filename,
                        exc,
                    )
                    continue
                encoded = base64.b64encode(image_bytes).decode("utf-8")
                document_contents[str(context_filename)] = {
                    "encodedbytes": encoded,
                    "mime_type": mime_type,
                    "source_document": str(context_filename),
                    "origin": "uploaded_image",
                    "page_number": None,
                    "page_context": None,
                }
                logger.info(
                    f"The image '{context_filename}' was read and converted to WebP."
                )
                continue

            if text:
                document_contents[str(context_filename)] = {
                    "text": text,
                    "source_document": str(context_filename),
                    "origin": "document",
                }
                logger.info(f"The file '{context_filename}' was read successfully.")

        except Exception as e:
            logger.error(f"Error reading '{context_filename}': {e}")
        finally:
            if unlink:
                try:
                    context_filename.unlink()
                except FileNotFoundError:
                    pass

    if extract_images:
        summarize_image_entries(document_contents)

    return document_contents


def get_text_or_imagebytes_from_django_uploaded_file(
    files: UploadedFile,
    extract_images: bool = False,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Get text dictionary from uploaded files.

    {Path(filename.pdf): {'text': 'lorem ipsum'} or {"encodedbytes": "dskhasdhak"}

    Returns:
        A tuple of ``(text_dict, unreadable_names)`` where ``unreadable_names``
        is a list of original display file names that could not be parsed
        (unsupported format, read error, or no extractable content).

    """
    # Map each temp file path back to the original display name so we can
    # report human-readable names when a file fails to produce any output.
    original_names: dict[str, str] = {}
    temp_files = []
    # The readers open files by path and pick the reader by extension, so each upload
    # is written under its own name, in a subfolder of its own so that uploads with
    # the same name don't collide. The folder is deleted when the block ends, also
    # after an error.
    with tempfile.TemporaryDirectory() as temp_dir:
        for number, file in enumerate(files):
            temp_path = Path(temp_dir) / str(number) / file.name
            temp_path.parent.mkdir()
            with temp_path.open("wb") as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            temp_files.append(str(temp_path))
            original_names[str(temp_path)] = file.name

        text_dict = get_text_or_bytes_perfile_dict(
            temp_files, extract_images=extract_images
        )

    # Determine which input files produced no output entry at all.
    # Every successfully processed file leaves at least one entry whose
    # "source_document" value equals the temp file path.
    processed_sources = {
        entry.get("source_document")
        for entry in text_dict.values()
        if entry.get("source_document") is not None
    }
    unreadable = [
        display_name
        for temp_path, display_name in original_names.items()
        if temp_path not in processed_sources
    ]

    return text_dict, unreadable


def split_doc_dict_by_type(
    dict: dict[str, dict[str, str]], decode: bool = True
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Split the dictionary into two dictionaries: one for text and one for bytes.

    Args:
    dict (dict): The input dictionary to split.
    decode (bool): if to base64decode the load

    Returns:
    tuple: A tuple containing two dictionaries: one for text and one for bytes.

    """
    text_dict = {}
    bytes_dict = {}
    for pathstr, sub_dict in dict.items():
        if "text" in sub_dict:
            text_dict[pathstr] = sub_dict
        elif "encodedbytes" in sub_dict:
            if decode:
                try:
                    bytes_dict[pathstr] = {
                        "bytes": base64.b64decode(sub_dict["encodedbytes"]),
                    }
                    if "mime_type" in sub_dict:
                        bytes_dict[pathstr]["mime_type"] = sub_dict["mime_type"]
                except Exception as e:
                    logging.error(f"Error decoding base64: {e}")
            else:
                bytes_dict[pathstr] = sub_dict
    return text_dict, bytes_dict


def collect_source_documents(doc_dict: dict[str, dict[str, str]]) -> list[str]:
    """Return unique source document names for a document dictionary."""
    seen: set[str] = set()
    ordered: list[str] = []
    for key, meta in doc_dict.items():
        origin = meta.get("origin")
        source = meta.get("source_document")
        if origin == "embedded" and source:
            name = Path(source).name
        else:
            name = Path(source or key).name
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _calculate_sha256(file_bytes: bytes) -> str:
    """Calculate SHA256 hash for file bytes."""
    return hashlib.sha256(file_bytes).hexdigest()


def store_files_to_storage(
    files: list[UploadedFile],
    user: "Person",
    assay: "Assay",
    consent: bool,
) -> list["FileAsset"]:
    """Store uploaded files to S3/MinIO if user consented.

    Args:
        files: List of uploaded files from the form
        user: Person object (uploader)
        assay: Assay object associated with the files
        consent: Whether user consented to file storage

    Returns:
        List of created FileAsset objects (empty if no consent)

    Raises:
        Exception: If file storage fails (propagates after logging)
    """
    from toxtempass.models import FileAsset
    import uuid

    if not consent:
        logger.debug("User did not consent to file storage; skipping storage.")
        return []

    if not files:
        logger.debug("No files provided for storage.")
        return []

    created_assets: list[FileAsset] = []

    for file in files:
        try:
            # Read file content
            file_content = b""
            for chunk in file.chunks():
                file_content += chunk

            # Calculate metadata
            file_size = len(file_content)
            sha256_hash = _calculate_sha256(file_content)
            content_type = (
                getattr(file, "content_type", "")
                or mimetypes.guess_type(file.name)[0]
                or ""
            )

            # Generate S3 object key using user/assay structure
            # Format: consent_user_documents/{user.email}/{assay.id}/{uuid}/{filename}
            object_key = (
                f"consent_user_documents/{user.email}/assay/{assay.id}/"
                f"{uuid.uuid4()}/"
                f"{Path(file.name).name}"
            )

            # Upload to storage using BytesIO from file_content
            # (file pointer is exhausted after chunks() call above)
            file_obj = BytesIO(file_content)
            default_storage.save(object_key, file_obj)
            logger.info(
                "Successfully uploaded file %s to storage: %s",
                file.name,
                object_key,
            )

            # Create FileAsset record
            asset = FileAsset.objects.create(
                object_key=object_key,
                original_filename=file.name,
                content_type=content_type,
                size_bytes=file_size,
                sha256=sha256_hash,
                status=FileAsset.Status.AVAILABLE,
                uploaded_by=user,
            )
            created_assets.append(asset)
            logger.debug("Created FileAsset record: id=%s, key=%s", asset.id, object_key)

        except Exception as exc:
            logger.exception(
                "Failed to store file %s for user %s on assay %s: %s",
                file.name,
                user.email,
                assay.id,
                exc,
            )
            raise

    logger.info(
        "Stored %d files for user %s on assay %s",
        len(created_assets),
        user.email,
        assay.id,
    )
    return created_assets

def download_assay_files_as_zip(
    assay: Assay,
    user: Person,
    request: HttpRequest | None = None,
) -> tuple[bytes, str]:
    """Download all files associated with an assay as a ZIP archive.

    Args:
        assay: Assay object whose files to download
        user: Person performing the download (staff/superuser only)
        request: Optional HttpRequest to extract IP address for audit log

    Returns:
        Tuple of (zip_bytes, filename)

    Raises:
        Exception: If file retrieval or ZIP creation fails
    """
    # Get all files associated with all answers in this assay. Only files that are
    # still shared: withdrawn ones must not be used (see toxtempass/privacy.py).
    answer_files = AnswerFile.objects.filter(
        answer__assay=assay, file__status="available"
    ).select_related("file").distinct("file")

    if not answer_files.exists():
        logger.warning("No files found for assay %s", assay.id)
        return b"", "empty.zip"

    file_assets = [af.file for af in answer_files]
    zip_buffer = BytesIO()

    try:
        with ZipFileLib(zip_buffer, "w") as zip_file:
            for file_asset in file_assets:
                try:
                    # Download file from S3/MinIO
                    file_content = default_storage.open(file_asset.object_key).read()

                    # Add to ZIP with original filename
                    zip_file.writestr(file_asset.original_filename, file_content)
                    logger.debug("Added %s to ZIP", file_asset.original_filename)

                except Exception as exc:
                    logger.exception(
                        "Failed to retrieve file %s from storage: %s",
                        file_asset.id,
                        exc,
                    )
                    raise

        zip_bytes = zip_buffer.getvalue()

        # Log the download for audit trail
        try:
            ip_address = None
            if request:
                x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
                if x_forwarded_for:
                    ip_address = x_forwarded_for.split(",")[0]
                else:
                    ip_address = request.META.get("REMOTE_ADDR")

            for file_asset in file_assets:
                FileDownloadLog.objects.create(
                    file=file_asset,
                    user=user,
                    ip_address=ip_address,
                )
            logger.info(
                "Logged %d file downloads for assay %s by user %s",
                len(file_assets),
                assay.id,
                user.email,
            )
        except Exception as exc:
            logger.exception("Failed to create download log entries: %s", exc)
            # Don't fail the download if logging fails
            pass

        return zip_bytes, f"assay_{assay.id}_files.zip"

    except Exception as exc:
        logger.exception("Failed to create ZIP archive for assay %s: %s", assay.id, exc)
        raise
