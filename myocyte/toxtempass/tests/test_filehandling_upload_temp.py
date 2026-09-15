"""Uploads are written to private temporary folders while their text is read."""

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile

from toxtempass.filehandling import (
    convert_to_temporary,
    get_text_or_imagebytes_from_django_uploaded_file,
)


@pytest.fixture(autouse=True)
def _own_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _in_memory(name, content):
    return InMemoryUploadedFile(
        BytesIO(content), "files", name, "text/plain", len(content), "utf-8"
    )


def _on_disk(name, content):
    upload = TemporaryUploadedFile(name, "text/plain", len(content), "utf-8")
    upload.write(content)
    upload.seek(0)
    return upload


def test_uploads_with_the_same_name_get_their_own_private_folders():
    first = Path(convert_to_temporary(_in_memory("notes.txt", b"first")))
    second = Path(convert_to_temporary(_in_memory("notes.txt", b"second")))

    assert first.name == second.name == "notes.txt"
    assert first.parent != second.parent
    assert (first.read_bytes(), second.read_bytes()) == (b"first", b"second")
    assert first.parent.stat().st_mode & 0o777 == 0o700


def test_reading_uploads_removes_their_files_and_folders():
    uploads = [
        _in_memory("protocol.txt", b"Cells were seeded at 10,000 per well."),
        _on_disk("protocol.txt", b"Viability was measured after 24 hours."),
    ]

    text_dict, unreadable = get_text_or_imagebytes_from_django_uploaded_file(
        uploads, extract_images=False
    )

    texts = sorted(entry["text"] for entry in text_dict.values())
    assert texts == [
        "Cells were seeded at 10,000 per well.",
        "Viability was measured after 24 hours.",
    ]
    assert unreadable == []
    for path in map(Path, text_dict):
        assert not path.exists()
        assert not path.parent.exists()
