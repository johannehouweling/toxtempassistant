"""Exports must only contain the assay's own question-set sections.

Regression tests for the bug where ``generate_json_from_assay`` walked
``Section.objects.all()`` — every QuestionSet in the DB — so any foreign
question set rendered a full block of questions whose answers can never
match the assay, each shown as "Answer not found in documents.".
"""

from django.test import TestCase

from toxtempass.export import generate_json_from_assay, generate_markdown_from_assay
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    QuestionFactory,
    QuestionSetFactory,
    SectionFactory,
    SubsectionFactory,
)


def _section_titles(export_data: dict) -> list[str]:
    return [
        section["section"]["fields"]["title"] for section in export_data["sections"]
    ]


class ExportQuestionSetFilterTests(TestCase):
    """The export walks only the assay's questionnaire version."""

    def test_export_excludes_sections_of_other_question_sets(self):
        foreign_qs = QuestionSetFactory(label="expA")
        foreign_section = SectionFactory(
            question_set=foreign_qs, title="Foreign Section"
        )
        foreign_subsection = SubsectionFactory(
            section=foreign_section, title="Foreign Subsection"
        )
        QuestionFactory(
            subsection=foreign_subsection, question_text="Foreign question?"
        )

        own_qs = QuestionSetFactory(label="expB")
        own_section = SectionFactory(question_set=own_qs, title="Own Section")
        own_subsection = SubsectionFactory(section=own_section, title="Own Subsection")
        own_question = QuestionFactory(
            subsection=own_subsection, question_text="Own question?"
        )
        # A second own-set section whose question has NO Answer row: it must
        # still be exported (with the not-found placeholder). Guards against
        # deriving the section list from the assay's answers instead of its
        # question set, which passes the answered-only assertions below but
        # silently drops every unanswered section from real exports.
        own_unanswered_section = SectionFactory(
            question_set=own_qs, title="Own Unanswered Section"
        )
        own_unanswered_subsection = SubsectionFactory(
            section=own_unanswered_section, title="Own Unanswered Subsection"
        )
        QuestionFactory(
            subsection=own_unanswered_subsection,
            question_text="Own unanswered question?",
        )

        assay = AssayFactory(question_set=own_qs)
        AnswerFactory(
            assay=assay,
            question=own_question,
            answer_text="The actual answer body.",
            accepted=True,
        )

        export_data = generate_json_from_assay(assay)
        self.assertCountEqual(
            _section_titles(export_data),
            ["Own Section", "Own Unanswered Section"],
        )
        unanswered_section = next(
            section
            for section in export_data["sections"]
            if section["section"]["fields"]["title"] == "Own Unanswered Section"
        )
        self.assertEqual(
            unanswered_section["subsections"][0]["questions_with_answers"][0][
                "answer"
            ],
            "",
        )

        markdown = generate_markdown_from_assay(assay)
        self.assertIn("The actual answer body.", markdown)
        self.assertNotIn("Foreign Section", markdown)
        # Exactly one placeholder: the unanswered own-set question. The foreign
        # set's questions must not add any.
        self.assertEqual(markdown.count("Answer not found in documents."), 1)

    def test_export_derives_question_set_from_answers_when_assay_has_none(self):
        # Legacy assays predate the Assay.question_set FK: the factory leaves
        # it None, mirroring rows created before that migration.
        SectionFactory(title="Foreign Section", question_set__label="expC")

        answer = AnswerFactory(
            answer_text="Legacy answer body.",
            question__subsection__section__question_set__label="expD",
            question__subsection__section__title="Legacy Answered Section",
        )
        assay = answer.assay
        self.assertIsNone(assay.question_set_id)

        # A sibling section in the same derived question set, with no answers:
        # the fallback must resolve the question SET, not just the sections the
        # answers happen to live in.
        legacy_qs = answer.question.subsection.section.question_set
        sibling_section = SectionFactory(
            question_set=legacy_qs, title="Legacy Sibling Section"
        )
        sibling_subsection = SubsectionFactory(
            section=sibling_section, title="Legacy Sibling Subsection"
        )
        QuestionFactory(
            subsection=sibling_subsection,
            question_text="Legacy sibling question?",
        )

        export_data = generate_json_from_assay(assay)
        self.assertCountEqual(
            _section_titles(export_data),
            ["Legacy Answered Section", "Legacy Sibling Section"],
        )

        markdown = generate_markdown_from_assay(assay)
        self.assertIn("Legacy answer body.", markdown)
        self.assertNotIn("Foreign Section", markdown)
        self.assertEqual(markdown.count("Answer not found in documents."), 1)

    def test_export_without_question_set_or_answers_has_no_sections(self):
        SectionFactory(title="Foreign Section", question_set__label="expE")
        assay = AssayFactory()

        export_data = generate_json_from_assay(assay)
        self.assertEqual(export_data["sections"], [])

        markdown = generate_markdown_from_assay(assay)
        self.assertNotIn("Foreign Section", markdown)
