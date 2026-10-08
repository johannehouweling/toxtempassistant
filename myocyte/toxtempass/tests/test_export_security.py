"""Regression tests: export_assay_to_file security hardening.

Verifies that:
1. Exported filenames use only the hardcoded suffix from EXPORT_MIME_SUFFIX,
   not raw export_type string concatenation.
2. The Pandoc subprocess command contains only trusted options from EXPORT_MAPPING
   and never includes raw export_type values as standalone arguments.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.http import FileResponse
from django.test import TestCase

from toxtempass import Config
from toxtempass.tests.fixtures.factories import AssayFactory, PersonFactory

EXPORT_MAPPING = Config.EXPORT_MAPPING
EXPORT_MIME_SUFFIX = Config.EXPORT_MIME_SUFFIX
PANDOC_EXPORT_TYPES = Config.PANDOC_EXPORT_TYPES


def _release_file_response(response: FileResponse) -> None:
    """Close the underlying file handle without firing request_finished.

    We must close the FD so TemporaryDirectory can clean up (Windows) and to
    avoid leaking across the loop, but calling response.close() fires
    django.core.signals.request_finished, whose close_old_connections
    listener closes the DB connection shared by this TestCase and poisons
    the next setUp. file_to_stream is FileResponse's public stream attribute.
    """
    response.file_to_stream.close()


def _make_pandoc_stub(captured_commands: list) -> object:
    """Return a side_effect callable that records the command and touches the output file."""

    def _run(cmd, check=False, timeout=None):
        captured_commands.append(list(cmd))
        try:
            o_idx = cmd.index("-o")
            Path(cmd[o_idx + 1]).touch()
        except ValueError:
            pass

    return _run


class ExportFilenameTests(TestCase):
    """Filename suffix is derived from EXPORT_MIME_SUFFIX, not raw export_type."""

    def setUp(self):
        self.user = PersonFactory()
        self.assay = AssayFactory(title="my test assay")
        self.request = MagicMock()

    def test_json_filename_suffix_from_mapping(self):
        """JSON export filename ends with the suffix from EXPORT_MIME_SUFFIX."""
        from toxtempass.export import export_assay_to_file

        with patch("toxtempass.export.generate_json_from_assay", return_value={}):
            response = export_assay_to_file(self.request, self.assay, "json")
        content_disp = response.headers.get("Content-Disposition", "")
        _release_file_response(response)

        expected_suffix = EXPORT_MIME_SUFFIX["json"]["suffix"]  # ".json"
        self.assertIn(expected_suffix, content_disp)
        # Guard against a double-dot regression (e.g. "toxtemp_title..json")
        self.assertNotIn("..", content_disp)

    def test_pandoc_filename_suffix_from_mapping(self):
        """Every Pandoc export type uses the suffix from EXPORT_MIME_SUFFIX."""
        from toxtempass.export import export_assay_to_file

        for export_type in PANDOC_EXPORT_TYPES:
            with (
                self.subTest(export_type=export_type),
                tempfile.TemporaryDirectory() as tmp_dir,
            ):
                yaml_stub = Path(tmp_dir) / "meta.yaml"
                yaml_stub.write_text("title: test")
                captured_commands: list = []

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
                        side_effect=_make_pandoc_stub(captured_commands),
                    ),
                ):
                    response = export_assay_to_file(
                        self.request, self.assay, export_type
                    )
                content_disp = response.headers.get("Content-Disposition", "")
                _release_file_response(response)

                expected_suffix = EXPORT_MIME_SUFFIX[export_type]["suffix"]
                self.assertIn(
                    expected_suffix,
                    content_disp,
                    msg=f"Expected suffix '{expected_suffix}' for type '{export_type}'",
                )
                self.assertNotIn(
                    "..",
                    content_disp,
                    msg=f"Double-dot in Content-Disposition for type '{export_type}'",
                )


class ExportPandocCommandTests(TestCase):
    """Pandoc command is built only from EXPORT_MAPPING trusted options."""

    def setUp(self):
        self.user = PersonFactory()
        self.assay = AssayFactory(title="pandoc test assay")
        self.request = MagicMock()

    def test_pandoc_command_contains_only_mapped_options(self):
        """subprocess.run receives exactly the trusted options listed in EXPORT_MAPPING."""
        from toxtempass.export import export_assay_to_file

        for export_type in PANDOC_EXPORT_TYPES:
            with (
                self.subTest(export_type=export_type),
                tempfile.TemporaryDirectory() as tmp_dir,
            ):
                yaml_stub = Path(tmp_dir) / "meta.yaml"
                yaml_stub.write_text("title: test")
                captured_commands: list = []

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
                        side_effect=_make_pandoc_stub(captured_commands),
                    ),
                ):
                    response = export_assay_to_file(self.request, self.assay, export_type)
                _release_file_response(response)

                self.assertEqual(
                    len(captured_commands),
                    1,
                    msg=f"Expected one subprocess.run call for type '{export_type}'",
                )
                cmd = captured_commands[0]

                # Every trusted option from EXPORT_MAPPING must appear in the command
                for opt in EXPORT_MAPPING[export_type]:
                    self.assertIn(
                        opt,
                        cmd,
                        msg=f"Trusted option '{opt}' missing from pandoc cmd for '{export_type}'",
                    )

                # The raw export_type string must NOT be a standalone argument
                self.assertNotIn(
                    export_type,
                    cmd,
                    msg=f"Raw export_type '{export_type}' must not appear as a standalone "
                    f"argument in the pandoc command",
                )
