"""Tests: export_assay_to_file error handling.

Verifies that when Pandoc (subprocess.run) raises CalledProcessError or an
unexpected Exception:
1. The response has HTTP status 500.
2. assay.processing_log is updated with the correlation id (internal log
   only — never surfaced to the user).
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from toxtempass import Config

PANDOC_EXPORT_TYPES = Config.PANDOC_EXPORT_TYPES


def _mock_assay() -> MagicMock:
    """Return a mock Assay with the minimal attributes used by export_assay_to_file."""
    assay = MagicMock()
    assay.processing_log = ""
    assay.title = "error test assay"
    return assay


def _run_export_with_pandoc_error(assay, side_effect):
    """Run export_assay_to_file for the first Pandoc type with a given subprocess error."""
    from toxtempass.export import export_assay_to_file

    request = MagicMock()
    export_type = next(iter(PANDOC_EXPORT_TYPES))

    with tempfile.TemporaryDirectory() as tmp_dir:
        yaml_stub = Path(tmp_dir) / "meta.yaml"
        yaml_stub.write_text("title: test")

        with (
            patch(
                "toxtempass.export.generate_markdown_from_assay",
                return_value="# test",
            ),
            patch(
                "toxtempass.export.get_create_meta_data_yaml",
                return_value=yaml_stub,
            ),
            patch(
                "toxtempass.export.subprocess.run",
                side_effect=side_effect,
            ),
        ):
            response = export_assay_to_file(request, assay, export_type)

    return response


class ExportCalledProcessErrorTests(SimpleTestCase):
    """subprocess.CalledProcessError → HTTP 500 + correlation id in processing_log."""

    def test_returns_500(self):
        assay = _mock_assay()
        error = subprocess.CalledProcessError(returncode=1, cmd=["pandoc"])
        response = _run_export_with_pandoc_error(assay, error)
        self.assertEqual(response.status_code, 500)

    def test_processing_log_contains_corr_id(self):
        assay = _mock_assay()
        error = subprocess.CalledProcessError(returncode=1, cmd=["pandoc"])
        _run_export_with_pandoc_error(assay, error)
        self.assertRegex(
            assay.processing_log,
            r"\[[0-9a-f]{8}\]",
            msg="processing_log should contain a correlation id like [abcd1234]",
        )


class ExportUnexpectedExceptionTests(SimpleTestCase):
    """Generic Exception → HTTP 500 + correlation id in processing_log."""

    def test_returns_500(self):
        assay = _mock_assay()
        error = RuntimeError("unexpected pandoc failure")
        response = _run_export_with_pandoc_error(assay, error)
        self.assertEqual(response.status_code, 500)

    def test_processing_log_contains_corr_id(self):
        assay = _mock_assay()
        error = RuntimeError("unexpected pandoc failure")
        _run_export_with_pandoc_error(assay, error)
        self.assertRegex(
            assay.processing_log,
            r"\[[0-9a-f]{8}\]",
            msg="processing_log should contain a correlation id like [abcd1234]",
        )


class ExportTimeoutTests(SimpleTestCase):
    """A hung pandoc is killed after the timeout and reported like any failure."""

    def test_timeout_gives_500_and_is_logged(self):
        assay = _mock_assay()
        error = subprocess.TimeoutExpired(cmd="pandoc", timeout=1)

        response = _run_export_with_pandoc_error(assay, error)

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Export failed", response.content)
        self.assertIn("TimeoutExpired", assay.processing_log)

    def test_pandoc_is_started_with_a_timeout(self):
        seen = {}

        def record(*args, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd="pandoc", timeout=1)

        _run_export_with_pandoc_error(_mock_assay(), record)

        self.assertEqual(seen["timeout"], Config._pandoc_timeout_seconds)
