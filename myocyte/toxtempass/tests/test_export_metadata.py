"""Tests for export author metadata."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml
from django.test import TestCase

from toxtempass.export import (
    generate_json_from_assay,
    generate_markdown_from_assay,
    get_create_meta_data_yaml,
)
from toxtempass.tests.fixtures.factories import AnswerFactory, AssayFactory, PersonFactory


class ExportMetadataAuthorTests(TestCase):
    """Export YAML metadata includes a flat ordered author list."""

    def _save_answer_with_user(
        self,
        answer,
        *,
        user,
        answer_text: str | None = None,
        accepted: bool | None = None,
    ) -> None:
        if answer_text is not None:
            answer.answer_text = answer_text
        if accepted is not None:
            answer.accepted = accepted
        answer._history_user = user
        answer.save()

    def test_metadata_lists_creator_then_contributors_then_owner(self):
        owner = PersonFactory(
            first_name="Owner",
            last_name="Person",
            organization="Owner Institute",
            email="owner@test.com",
            orcid_id="0000-0000-0000-0001",
        )
        creator = PersonFactory(
            first_name="Creator",
            last_name="Author",
            organization="Creator Lab",
            orcid_id="0000-0000-0000-0002",
            email="creator@test.com",
        )
        contributor_one = PersonFactory(
            first_name="Alice",
            last_name="Editor",
            organization="Alice Org",
            orcid_id="0000-0000-0000-0003",
            email="alice@test.com",
        )
        contributor_two = PersonFactory(
            first_name="Bob",
            last_name="Reviewer",
            organization="Bob Center",
            orcid_id="0000-0000-0000-0004",
            email="bob@test.com",
        )
        exporter = PersonFactory(first_name="Exporter", last_name="Only")
        assay = AssayFactory(study__investigation__owner=owner, created_by=creator)

        answer_one = AnswerFactory(
            assay=assay,
            answer_text="draft",
            question__subsection__section__question_set__label="authtrk1",
        )

        self._save_answer_with_user(
            answer_one,
            user=contributor_one,
            answer_text="edited once",
        )
        self._save_answer_with_user(
            answer_one,
            user=contributor_one,
            accepted=True,
        )
        self._save_answer_with_user(
            answer_one,
            user=contributor_two,
            answer_text="edited twice",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = get_create_meta_data_yaml(
                SimpleNamespace(user=exporter),
                assay,
                file_path=Path(tmp_dir) / "toxtemp.pdf",
            )
            metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        self.assertEqual(
            metadata["author"],
            [
                "Creator Author",
                "Alice Editor",
                "Bob Reviewer",
                "Owner Person",
            ],
        )
        self.assertNotIn("main_author", metadata)
        self.assertNotIn("co_authors", metadata)
        self.assertEqual(
            metadata["authors"],
            [
                {
                    "credited": True,
                    "name": "Creator Author",
                    "organization": "Creator Lab",
                    "orcid_id": "0000-0000-0000-0002",
                    "email": "creator@test.com",
                },
                {
                    "credited": True,
                    "name": "Alice Editor",
                    "organization": "Alice Org",
                    "orcid_id": "0000-0000-0000-0003",
                    "email": "alice@test.com",
                },
                {
                    "credited": True,
                    "name": "Bob Reviewer",
                    "organization": "Bob Center",
                    "orcid_id": "0000-0000-0000-0004",
                    "email": "bob@test.com",
                },
                {
                    "credited": True,
                    "name": "Owner Person",
                    "organization": "Owner Institute",
                    "orcid_id": "0000-0000-0000-0001",
                    "email": "owner@test.com",
                },
            ],
        )
        self.assertNotIn("corresponding_author", metadata)
        self.assertNotIn("investigation_owner", metadata)
        self.assertNotIn("Exporter Only", metadata["author"])

    def test_owner_is_listed_once_and_first_when_creator_matches_owner(self):
        owner = PersonFactory(
            first_name="Owner",
            last_name="Creator",
            orcid_id="0000-0000-0000-0010",
            email="ownercreator@test.com",
        )
        contributor = PersonFactory(
            first_name="Middle",
            last_name="Editor",
            orcid_id="0000-0000-0000-0011",
            email="middle@test.com",
        )
        assay = AssayFactory(study__investigation__owner=owner, created_by=owner)

        answer = AnswerFactory(
            assay=assay,
            answer_text="draft",
            question__subsection__section__question_set__label="ownerdedup",
        )
        self._save_answer_with_user(answer, user=owner, answer_text="owner edit")
        self._save_answer_with_user(
            answer,
            user=contributor,
            answer_text="contributor edit",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = get_create_meta_data_yaml(
                SimpleNamespace(user=owner),
                assay,
                file_path=Path(tmp_dir) / "toxtemp.pdf",
            )
            metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["author"], ["Owner Creator", "Middle Editor"])
        self.assertNotIn("main_author", metadata)
        self.assertNotIn("co_authors", metadata)
        self.assertEqual(
            metadata["authors"],
            [
                {
                    "credited": True,
                    "name": "Owner Creator",
                    "organization": None,
                    "orcid_id": "0000-0000-0000-0010",
                    "email": "ownercreator@test.com",
                },
                {
                    "credited": True,
                    "name": "Middle Editor",
                    "organization": None,
                    "orcid_id": "0000-0000-0000-0011",
                    "email": "middle@test.com",
                },
            ],
        )

    def test_json_and_markdown_include_author_organizations_and_owner_contact(self):
        owner = PersonFactory(
            first_name="Owner",
            last_name="Person",
            organization="Owner Institute",
            email="owner@test.com",
            orcid_id="0000-0000-0000-0001",
        )
        creator = PersonFactory(
            first_name="Creator",
            last_name="Author",
            organization="Creator Lab",
            orcid_id="0000-0000-0000-0002",
            email="creator@test.com",
        )
        contributor = PersonFactory(
            first_name="Alice",
            last_name="Editor",
            organization="Alice Org",
            orcid_id="0000-0000-0000-0003",
            email="alice@test.com",
        )
        assay = AssayFactory(study__investigation__owner=owner, created_by=creator)

        answer = AnswerFactory(
            assay=assay,
            answer_text="draft",
            question__subsection__section__question_set__label="authjson1",
        )
        self._save_answer_with_user(answer, user=contributor, answer_text="edited once")

        export_data = generate_json_from_assay(assay)

        self.assertEqual(
            export_data["metadata"]["authors"],
            [
                {
                    "credited": True,
                    "name": "Creator Author",
                    "organization": "Creator Lab",
                    "orcid_id": "0000-0000-0000-0002",
                    "email": "creator@test.com",
                },
                {
                    "credited": True,
                    "name": "Alice Editor",
                    "organization": "Alice Org",
                    "orcid_id": "0000-0000-0000-0003",
                    "email": "alice@test.com",
                },
                {
                    "credited": True,
                    "name": "Owner Person",
                    "organization": "Owner Institute",
                    "orcid_id": "0000-0000-0000-0001",
                    "email": "owner@test.com",
                },
            ],
        )
        self.assertEqual(export_data["metadata"]["main_author"], "Creator Author")
        self.assertEqual(
            export_data["metadata"]["co_authors"],
            ["Alice Editor", "Owner Person"],
        )
        self.assertEqual(
            export_data["metadata"]["investigation_owner"],
            {
                "name": "Owner Person",
                "organization": "Owner Institute",
                "email": "owner@test.com",
                "orcid_id": "0000-0000-0000-0001",
            },
        )

        markdown = generate_markdown_from_assay(assay)
        self.assertIn("- **Authors:**", markdown)
        self.assertIn(
            "  - Creator Author (Creator Lab) — ORCID iD: 0000-0000-0000-0002",
            markdown,
        )
        self.assertIn(
            "  - Alice Editor (Alice Org) — ORCID iD: 0000-0000-0000-0003",
            markdown,
        )
        self.assertIn(
            "  - Owner Person (Owner Institute) — ORCID iD: 0000-0000-0000-0001",
            markdown,
        )
        self.assertIn(
            (
                "  - Owner Person (Owner Institute) — ORCID iD: "
                "0000-0000-0000-0001 — Email: owner@test.com"
            ),
            markdown,
        )
