import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from toxtempass.filehandling import (
    _extract_images_from_docx,
    _extract_images_from_pdf_page,
    collect_source_documents,
    get_text_or_bytes_perfile_dict,
)


def _make_png_bytes(size=(10, 10), color="red") -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


class DummyPdfImage:
    def __init__(self, data: bytes, name: str = "img.png", image_format: str = "PNG"):
        self.data = data
        self.name = name
        self.image_format = image_format


def test_extract_images_from_pdf_page_creates_entries(tmp_path):
    png_bytes = _make_png_bytes(size=(60, 60))
    page = SimpleNamespace(images=[DummyPdfImage(png_bytes, name="foo.png")])
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7")  # minimal placeholder content

    result = _extract_images_from_pdf_page(page, source, page_number=2)

    assert len(result) == 1
    [(key, meta)] = list(result.items())
    assert "sample.pdf#page2_" in key
    assert meta["mime_type"] == "image/webp"
    decoded = base64.b64decode(meta["encodedbytes"])
    # WebP files start with RIFF....WEBP
    assert decoded[:4] == b"RIFF"
    assert decoded[8:12] == b"WEBP"
    assert meta["origin"] == "embedded"
    assert meta["source_document"].endswith("sample.pdf")


def test_extract_images_from_docx_reads_media(tmp_path):
    docx_path = tmp_path / "simple.docx"
    img_bytes = _make_png_bytes(size=(60, 60))
    with ZipFile(docx_path, "w") as zip_file:
        zip_file.writestr("word/media/image1.png", img_bytes)

    result = _extract_images_from_docx(docx_path)

    assert len(result) == 1
    [(key, meta)] = list(result.items())
    assert key.endswith("#image1.png")
    assert meta["mime_type"] == "image/webp"
    decoded = base64.b64decode(meta["encodedbytes"])
    assert decoded[:4] == b"RIFF"
    assert decoded[8:12] == b"WEBP"
    assert meta["origin"] == "embedded"
    assert meta["source_document"].endswith("simple.docx")


def test_collect_source_documents_handles_embedded_and_uploaded(tmp_path):
    doc_path = tmp_path / "report.pdf"
    img_path = tmp_path / "figure.png"
    doc_dict = {
        str(doc_path): {
            "text": "content",
            "source_document": str(doc_path),
            "origin": "document",
        },
        f"{doc_path}#page1_image1": {
            "encodedbytes": base64.b64encode(b"img1").decode("utf-8"),
            "mime_type": "image/png",
            "source_document": str(doc_path),
            "origin": "embedded",
        },
        str(img_path): {
            "encodedbytes": base64.b64encode(b"img2").decode("utf-8"),
            "mime_type": "image/png",
            "source_document": str(img_path),
            "origin": "uploaded_image",
        },
    }

    result = collect_source_documents(doc_dict)

    assert result == ["report.pdf", "figure.png"]


def test_image_descriptions_are_added_when_requested(tmp_path):
    image_path = tmp_path / "figure.png"
    img = Image.new("RGB", (60, 60), color="red")
    img.save(image_path)

    with patch("toxtempass.filehandling._describe_image", return_value="Stub description"):
        doc_dict = get_text_or_bytes_perfile_dict(
            [image_path], unlink=False, extract_images=True
        )

    entry = doc_dict[str(image_path)]
    assert "text" in entry
    assert "Stub description" in entry["text"]
    assert entry["origin"] == "image_description"


def test_image_descriptions_can_be_left_to_the_worker(tmp_path):
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (60, 60), color="red").save(image_path)

    with patch("toxtempass.filehandling._describe_image") as describe:
        doc_dict = get_text_or_bytes_perfile_dict(
            [image_path], unlink=False, extract_images=True, summarize_images=False
        )

    describe.assert_not_called()
    assert "encodedbytes" in doc_dict[str(image_path)]
