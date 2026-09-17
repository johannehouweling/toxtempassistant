from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict
from unittest.mock import patch

from toxtempass.tests.fixtures.factories import (
    PersonFactory,
    InvestigationFactory,
    StudyFactory,
    AssayFactory,
)
from toxtempass.forms import AssayAnswerForm
from toxtempass.models import QuestionSet, Section, Subsection, Question, Answer


class FileUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = PersonFactory()
        # Ensure the investigation/assay are owned by the user so permission checks pass
        self.investigation = InvestigationFactory(owner=self.user)
        self.study = StudyFactory(investigation=self.investigation)
        self.assay = AssayFactory(study=self.study)

    def test_answer_view_accepts_multiple_files(self):
        """
        Form-level test: instantiate AssayAnswerForm with two uploaded files and
        assert the processing functions are invoked. This avoids test-client
        upload mechanics which can be brittle in tests.
        """
        qs = QuestionSet.objects.create(
            display_name="test-qs", created_by=self.assay.study.investigation.owner
        )
        section = Section.objects.create(question_set=qs, title="Sec")
        subsection = Subsection.objects.create(section=section, title="Subsec")
        question = Question.objects.create(subsection=subsection, question_text="Q1?")
        Answer.objects.get_or_create(assay=self.assay, question=question)
        self.assay.question_set = qs
        self.assay.save()

        dummy_doc = {
            "a.txt": {"text": "alpha", "source_document": "a.txt", "origin": "document"}
        }

        f1 = SimpleUploadedFile("a.txt", b"alpha", content_type="text/plain")
        f2 = SimpleUploadedFile("b.txt", b"beta", content_type="text/plain")

        files = MultiValueDict({"file_upload": [f1, f2]})

        with patch(
            "toxtempass.forms.get_text_or_imagebytes_from_django_uploaded_file",
            return_value=(dummy_doc, []),
        ) as mock_get_text, patch("toxtempass.forms.async_task") as mock_async:
            form = AssayAnswerForm(
                data={f"earmarked_{question.id}": True},
                files=files,
                assay=self.assay,
                user=self.user,
            )
            self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
            queued = form.save()

            mock_get_text.assert_called_once()
            mock_async.assert_called_once()
            self.assertTrue(queued)
            self.assertTrue(getattr(form, "async_enqueued", False))

    def test_rerun_passes_user_model_and_image_flag(self):
        """A re-run of selected questions uses the user's model and image choice."""
        qs = QuestionSet.objects.create(
            display_name="rerun-qs", created_by=self.assay.study.investigation.owner
        )
        section = Section.objects.create(question_set=qs, title="Sec")
        subsection = Subsection.objects.create(section=section, title="Subsec")
        question = Question.objects.create(subsection=subsection, question_text="Q1?")
        Answer.objects.get_or_create(assay=self.assay, question=question)
        self.assay.question_set = qs
        self.assay.save()

        doc = {
            "a.txt": {"text": "alpha", "source_document": "a.txt", "origin": "document"}
        }
        upload = SimpleUploadedFile("a.txt", b"alpha", content_type="text/plain")
        files = MultiValueDict({"file_upload": [upload]})

        with patch(
            "toxtempass.forms.get_text_or_imagebytes_from_django_uploaded_file",
            return_value=(doc, []),
        ) as mock_get_text, patch("toxtempass.forms.async_task") as mock_async, patch(
            "toxtempass.llm.current_llm_key", return_value="1:TESTMODEL"
        ):
            form = AssayAnswerForm(
                data={f"earmarked_{question.id}": True, "extract_images": True},
                files=files,
                assay=self.assay,
                user=self.user,
            )
            self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
            self.assertTrue(form.save())

        # The user ticked "extract images", so the upload path must honour it.
        self.assertTrue(mock_get_text.call_args.kwargs["extract_images"])
        kwargs = mock_async.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.pk)
        self.assertEqual(kwargs["llm_model"], "1:TESTMODEL")

    def test_assayanswerform_clean_accepts_multiple_files(self):
        """
        Direct form-level test: instantiate AssayAnswerForm with files mapping containing
        two SimpleUploadedFile objects and assert validation passes.
        """
        f1 = SimpleUploadedFile("a.txt", b"alpha", content_type="text/plain")
        f2 = SimpleUploadedFile("b.txt", b"beta", content_type="text/plain")

        files = MultiValueDict({"file_upload": [f1, f2]})

        form = AssayAnswerForm(data={}, files=files, assay=self.assay, user=self.user)
        # The form should validate (no required question fields enforced here)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
        cleaned = form.cleaned_data.get("file_upload", [])
        self.assertEqual(len(cleaned), 2)

    @patch("toxtempass.forms.config.max_size_mb", new=0.000001)
    def test_assayanswerform_rejects_oversize_file(self):
        """
        Force the configured max_size_mb to a very small value so that a small
        uploaded file becomes oversized and triggers validation error.
        """
        large_content = b"too large"
        f = SimpleUploadedFile("big.txt", large_content, content_type="text/plain")
        files = MultiValueDict({"file_upload": [f]})

        form = AssayAnswerForm(data={}, files=files, assay=self.assay, user=self.user)
        # Cleaning/validation should fail due to oversize
        self.assertFalse(form.is_valid())
        # Ensure the error mentions exceeds
        errors = (
            form.errors.get("__all__") or form.errors.get("file_upload") or form.errors
        )
        # Flatten errors to a string for a robust assertion
        error_str = str(errors)
        self.assertIn("exceeds", error_str.lower())

    def test_assayanswerform_rejects_legacy_doc_mime_type(self):
        """
        application/msword (.doc) is no longer in ALLOWED_MIME_TYPES because
        the old Word format cannot be parsed.  Uploading such a file must
        trigger a validation error at the form-cleaning stage.
        """
        f = SimpleUploadedFile(
            "report.doc", b"PK\x03\x04dummy", content_type="application/msword"
        )
        files = MultiValueDict({"file_upload": [f]})

        form = AssayAnswerForm(data={}, files=files, assay=self.assay, user=self.user)
        self.assertFalse(form.is_valid())
        error_str = str(form.errors)
        self.assertIn("unsupported file type", error_str.lower())

    def test_assayanswerform_save_warns_on_unreadable_file(self):
        """
        When get_text_or_imagebytes_from_django_uploaded_file reports that a
        file could not be parsed, save() must add a user-visible alert on the
        assay and still proceed if at least one other file was readable.
        """
        qs = QuestionSet.objects.create(
            display_name="test-qs", created_by=self.assay.study.investigation.owner
        )
        section = Section.objects.create(question_set=qs, title="Sec")
        subsection = Subsection.objects.create(section=section, title="Subsec")
        question = Question.objects.create(subsection=subsection, question_text="Q1?")
        Answer.objects.get_or_create(assay=self.assay, question=question)
        self.assay.question_set = qs
        self.assay.save()

        dummy_doc = {
            "good.txt": {
                "text": "readable content",
                "source_document": "good.txt",
                "origin": "document",
            }
        }

        f1 = SimpleUploadedFile("good.txt", b"readable", content_type="text/plain")
        f2 = SimpleUploadedFile("bad.pdf", b"corrupt", content_type="application/pdf")
        files = MultiValueDict({"file_upload": [f1, f2]})

        with patch(
            "toxtempass.forms.get_text_or_imagebytes_from_django_uploaded_file",
            return_value=(dummy_doc, ["bad.pdf"]),
        ), patch("toxtempass.forms.async_task"):
            form = AssayAnswerForm(
                data={f"earmarked_{question.id}": True},
                files=files,
                assay=self.assay,
                user=self.user,
            )
            self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
            result = form.save()

        self.assertTrue(result)
        self.assay.refresh_from_db()
        alerts = self.assay.user_alerts or []
        alert_messages = [a["message"] for a in alerts]
        self.assertTrue(
            any("bad.pdf" in msg for msg in alert_messages),
            msg=f"Expected 'bad.pdf' warning in user_alerts, got: {alert_messages}",
        )

    def test_assayanswerform_save_blocks_when_all_files_unreadable(self):
        """
        When every uploaded file fails to parse (all unreadable), save() must
        add a form error and return False instead of queuing an async task.
        """
        qs = QuestionSet.objects.create(
            display_name="test-qs2", created_by=self.assay.study.investigation.owner
        )
        section = Section.objects.create(question_set=qs, title="Sec2")
        subsection = Subsection.objects.create(section=section, title="Subsec2")
        question = Question.objects.create(subsection=subsection, question_text="Q2?")
        Answer.objects.get_or_create(assay=self.assay, question=question)
        self.assay.question_set = qs
        self.assay.save()

        f = SimpleUploadedFile("broken.pdf", b"notapdf", content_type="application/pdf")
        files = MultiValueDict({"file_upload": [f]})

        with patch(
            "toxtempass.forms.get_text_or_imagebytes_from_django_uploaded_file",
            return_value=({}, ["broken.pdf"]),
        ), patch("toxtempass.forms.async_task") as mock_async:
            form = AssayAnswerForm(
                data={f"earmarked_{question.id}": True},
                files=files,
                assay=self.assay,
                user=self.user,
            )
            self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")
            result = form.save()

        self.assertFalse(result)
        mock_async.assert_not_called()
        error_str = str(form.errors)
        self.assertIn("none of the uploaded files could be read", error_str.lower())
