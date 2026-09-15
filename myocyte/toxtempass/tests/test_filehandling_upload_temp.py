"""Uploads are read from a temporary folder that is deleted afterwards."""

import tempfile
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile

from toxtempass.filehandling import get_text_or_imagebytes_from_django_uploaded_file


@pytest.fixture(autouse=True)
def temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _in_memory(name, content):
    return InMemoryUploadedFile(
        BytesIO(content), "files", name, "text/plain", len(content), "utf-8"
    )


def _on_disk(name, content):
    upload = TemporaryUploadedFile(name, "text/plain", len(content), "utf-8")
    upload.write(content)
    upload.seek(0)
    return upload


def test_uploads_with_the_same_name_are_both_read_and_leave_nothing_behind(temp_dir):
    uploads = [
        _in_memory("protocol.txt", b"Cells were seeded at 10,000 per well."),
        _on_disk("protocol.txt", b"Viability was measured after 24 hours."),
    ]
    before = set(temp_dir.iterdir())  # holds the on-disk upload's own file

    text_dict, unreadable = get_text_or_imagebytes_from_django_uploaded_file(
        uploads, extract_images=False
    )

    texts = sorted(entry["text"] for entry in text_dict.values())
    assert texts == [
        "Cells were seeded at 10,000 per well.",
        "Viability was measured after 24 hours.",
    ]
    assert unreadable == []
    assert set(temp_dir.iterdir()) == before


def test_the_temporary_folder_is_deleted_when_reading_fails(temp_dir):
    before = set(temp_dir.iterdir())
    with (
        patch(
            "toxtempass.filehandling.get_text_or_bytes_perfile_dict",
            side_effect=RuntimeError("unreadable"),
        ),
        pytest.raises(RuntimeError),
    ):
        get_text_or_imagebytes_from_django_uploaded_file(
            [_in_memory("notes.txt", b"text")], extract_images=False
        )
    assert set(temp_dir.iterdir()) == before
