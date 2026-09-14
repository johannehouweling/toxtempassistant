"""Tests for the Account and Privacy tabs: profile, email, password, ORCID,
shared documents, export and account deletion."""

import io
import re
import zipfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from toxtempass import privacy, utilities
from toxtempass.filehandling import download_assay_files_as_zip
from toxtempass.models import (
    Answer,
    AnswerFile,
    FileAsset,
    FileWithdrawal,
    Investigation,
    Person,
)
from toxtempass.tests.fixtures.factories import (
    AdminFactory,
    AssayFactory,
    FileAssetFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionFactory,
    QuestionSetFactory,
    StudyFactory,
    WorkspaceFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "Pass-word-12345"


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = "https://toxtemp.example"
    settings.ADMINS = []


def _with_password(**kwargs):
    user = PersonFactory(**kwargs)
    user.set_password(PASSWORD)
    user.save()
    return user


def _question():
    # An explicit label (max 10 chars): the factory's "v<n>" can hit an existing "v1".
    question_set = QuestionSetFactory(label="privacy")
    return QuestionFactory(subsection__section__question_set=question_set)


@pytest.fixture
def shared_upload():
    """A document a user shared for an assay, linked to one of its answers."""
    user = PersonFactory()
    question = _question()
    assay = AssayFactory(
        title="Neurite outgrowth", question_set=question.subsection.section.question_set
    )
    answer = Answer.objects.create(
        assay=assay, question=question, answer_text="From the SOP"
    )
    file = FileAssetFactory(uploaded_by=user, original_filename="sop.pdf")
    AnswerFile.objects.create(answer=answer, file=file)
    return user, assay, answer, file


# ── Profile, email, password, ORCID ───────────────────────────────────────────


def test_profile_update_saves_name_and_organization(client):
    user = PersonFactory()
    client.force_login(user)

    response = client.post(
        reverse("account_update_profile"),
        {"first_name": "Ada", "last_name": "Lovelace", "organization": "RIVM"},
    )

    assert response.json()["success"] is True
    user.refresh_from_db()
    assert (user.first_name, user.last_name, user.organization) == (
        "Ada",
        "Lovelace",
        "RIVM",
    )


def test_profile_update_requires_an_organization(client):
    client.force_login(PersonFactory())
    response = client.post(
        reverse("account_update_profile"),
        {"first_name": "Ada", "last_name": "Lovelace", "organization": "  "},
    )
    assert response.status_code == 400
    assert "organization" in response.json()["errors"]


def test_email_change_waits_for_the_new_address_to_confirm(
    client, django_capture_on_commit_callbacks
):
    user = _with_password(email="old@example.org")
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("account_request_email_change"),
            {"new_email": "New@Example.org", "password": PASSWORD},
        )

    assert response.json()["success"] is True
    user.refresh_from_db()
    assert user.email == "old@example.org"
    assert user.pending_email == "new@example.org"
    assert sorted(message.to[0] for message in mail.outbox) == [
        "new@example.org",
        "old@example.org",
    ]
    confirmation = next(m for m in mail.outbox if m.to == ["new@example.org"])
    path = re.search(r"/account/confirm-email-change/[^/\s]+/", confirmation.body)[0]

    assert client.get(path).status_code == 200
    user.refresh_from_db()
    assert user.email == "new@example.org"
    assert user.pending_email == ""
    assert user.email_confirmed_at is not None
    assert client.get(path).status_code == 400


def test_email_change_needs_the_password_and_a_free_address(client):
    PersonFactory(email="taken@example.org")
    user = _with_password()
    client.force_login(user)
    url = reverse("account_request_email_change")

    wrong_password = client.post(url, {"new_email": "fresh@example.org", "password": "x"})
    assert wrong_password.status_code == 400
    assert "password" in wrong_password.json()["errors"]

    taken = client.post(url, {"new_email": "Taken@example.org", "password": PASSWORD})
    assert taken.status_code == 400
    assert "new_email" in taken.json()["errors"]

    user.refresh_from_db()
    assert user.pending_email == ""


def test_cancelled_email_change_link_stops_working(client):
    user = _with_password(email="old@example.org", pending_email="new@example.org")
    token = utilities.generate_email_change_token(user)
    client.force_login(user)

    client.post(reverse("account_cancel_email_change"))

    response = client.get(reverse("account_confirm_email_change", args=[token]))
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email == "old@example.org"


def test_password_change_keeps_the_session_and_sends_a_notice(
    client, django_capture_on_commit_callbacks
):
    user = _with_password()
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("account_change_password"),
            {
                "old_password": PASSWORD,
                "new_password1": "New-Secure-Pass-42",
                "new_password2": "New-Secure-Pass-42",
            },
        )

    assert response.json()["success"] is True
    user.refresh_from_db()
    assert user.check_password("New-Secure-Pass-42")
    assert client.get(reverse("account_shared_files")).status_code == 200
    assert [message.subject for message in mail.outbox] == [
        "[ToxTempAssistant] Your password was changed"
    ]


def test_unlink_orcid(client):
    user = _with_password(orcid_id="0000-0002-1825-0097")
    client.force_login(user)

    assert client.post(reverse("account_unlink_orcid")).json()["success"] is True
    user.refresh_from_db()
    assert user.orcid_id is None


# ── Shared documents ──────────────────────────────────────────────────────────


def test_privacy_tab_lists_only_the_users_own_documents(client, shared_upload):
    user, _assay, _answer, _file = shared_upload
    FileAssetFactory(original_filename="someone-else.pdf")
    client.force_login(user)

    html = client.get(reverse("account_shared_files")).content.decode()

    assert "Neurite outgrowth" in html
    assert "sop.pdf" in html
    assert "someone-else.pdf" not in html
    assert "Stop sharing" in html


def test_stop_sharing_takes_effect_now_and_can_be_undone(client, shared_upload):
    user, _assay, _answer, file = shared_upload
    client.force_login(user)

    response = client.post(reverse("account_stop_sharing"), {"file_ids": str(file.pk)})

    file.refresh_from_db()
    assert file.status == FileAsset.Status.WITHDRAWN
    remaining = file.delete_after - timezone.now()
    assert timedelta(hours=23) < remaining <= timedelta(hours=24)
    assert "Undo" in response.json()["html"]

    client.post(reverse("account_undo_stop_sharing"), {"file_ids": str(file.pk)})
    file.refresh_from_db()
    assert file.status == FileAsset.Status.AVAILABLE
    assert file.delete_after is None


@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="the ZIP download uses DISTINCT ON"
)
def test_withdrawn_documents_are_left_out_of_admin_downloads(shared_upload):
    user, assay, _answer, file = shared_upload
    privacy.withdraw_files(user, [str(file.pk)])

    with patch("toxtempass.filehandling.default_storage.open") as storage_open:
        assert download_assay_files_as_zip(assay, AdminFactory()) == (b"", "empty.zip")
    storage_open.assert_not_called()


def test_only_the_uploader_can_stop_sharing(client, shared_upload):
    _user, _assay, _answer, file = shared_upload
    client.force_login(PersonFactory())

    client.post(reverse("account_stop_sharing"), {"file_ids": f"{file.pk},not-a-uuid"})

    file.refresh_from_db()
    assert file.status == FileAsset.Status.AVAILABLE


def test_withdrawn_documents_are_deleted_after_the_waiting_period(shared_upload):
    user, assay, answer, file = shared_upload
    privacy.withdraw_files(user, [str(file.pk)])

    with patch("toxtempass.signals.default_storage") as storage:
        storage.exists.return_value = True
        privacy.delete_withdrawn_files(now=timezone.now() + timedelta(hours=1))
        assert FileAsset.objects.filter(pk=file.pk).exists()
        assert privacy.restore_files(
            user, [str(file.pk)], now=timezone.now() + timedelta(hours=25)
        ) == 0

        privacy.delete_withdrawn_files(now=timezone.now() + timedelta(hours=25))

    assert not FileAsset.objects.filter(pk=file.pk).exists()
    storage.delete.assert_called_once_with(file.object_key)
    answer.refresh_from_db()
    assert answer.answer_text == "From the SOP"
    record = FileWithdrawal.objects.get()
    assert (record.user, record.assay, record.file_count) == (user, assay, 1)


def test_files_left_behind_by_a_failed_draft_are_removed():
    leftover = FileAssetFactory(status=FileAsset.Status.DELETED)
    with patch("toxtempass.signals.default_storage") as storage:
        storage.exists.return_value = True
        privacy.delete_withdrawn_files()
    assert not FileAsset.objects.filter(pk=leftover.pk).exists()


def test_setup_schedules_replaces_the_old_email_schedule():
    Schedule.objects.create(
        name="toxtempass email jobs",
        func="toxtempass.notifications.run_email_jobs",
        schedule_type=Schedule.MINUTES,
        minutes=2,
    )

    call_command("setup_schedules", stdout=StringIO())
    call_command("setup_schedules", stdout=StringIO())

    assert list(Schedule.objects.values_list("name", "func")) == [
        ("toxtempass periodic jobs", "toxtempass.jobs.run_periodic_jobs")
    ]


# ── Export and account deletion ───────────────────────────────────────────────


def test_export_contains_every_toxtemp_the_user_can_open_and_nothing_else(client):
    user = PersonFactory()
    question = _question()
    question_set = question.subsection.section.question_set
    own = AssayFactory(
        title="Own assay",
        study=StudyFactory(investigation=InvestigationFactory(owner=user)),
        question_set=question_set,
    )
    Answer.objects.create(assay=own, question=question, answer_text="An answer")
    AssayFactory(
        title="Demo assay",
        study=own.study,
        question_set=question_set,
        demo_lock=True,
    )
    AssayFactory(title="Someone else's assay", question_set=question_set)
    client.force_login(user)

    response = client.get(reverse("account_export"))

    assert response["Content-Type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(name.endswith("-own-assay/toxtemp.md") for name in names)
    assert not any("someone-elses-assay" in name for name in names)
    # The read-only demo is seeded for everyone; it is not the user's own work.
    assert not any("demo-assay" in name for name in names)


def test_account_deletion_is_blocked_while_owning_a_workspace(client):
    user = _with_password()
    WorkspaceFactory(owner=user, name="Liver models")
    client.force_login(user)

    panel = client.get(reverse("account_delete_panel")).content.decode()
    assert "Liver models" in panel
    assert "Delete my account" not in panel
    assert "Download all your ToxTemps" in panel

    response = client.post(
        reverse("account_delete"), {"password": PASSWORD, "confirm": "1"}
    )
    assert response.status_code == 400
    assert Person.objects.filter(pk=user.pk).exists()


def test_deleting_an_account_removes_its_data_and_sends_a_receipt(
    client, django_capture_on_commit_callbacks
):
    user = _with_password(email="leaving@example.org", first_name="Lea")
    investigation = InvestigationFactory(owner=user)
    AssayFactory(study=StudyFactory(investigation=investigation))
    client.force_login(user)

    panel = client.get(reverse("account_delete_panel")).content.decode()
    assert "Delete my account" in panel
    unconfirmed = client.post(reverse("account_delete"), {"password": PASSWORD})
    assert unconfirmed.status_code == 400
    assert "confirm" in unconfirmed.json()["errors"]

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("account_delete"), {"password": PASSWORD, "confirm": "1"}
        )

    assert response.json()["redirect_url"] == reverse("account_deleted")
    assert not Person.objects.filter(email="leaving@example.org").exists()
    assert not Investigation.objects.filter(pk=investigation.pk).exists()
    assert [message.to for message in mail.outbox] == [["leaving@example.org"]]
    assert mail.outbox[0].subject == "[ToxTempAssistant] Your account was deleted"
    assert client.get(reverse("account_shared_files")).status_code == 302
    assert client.get(reverse("account_deleted")).status_code == 200
