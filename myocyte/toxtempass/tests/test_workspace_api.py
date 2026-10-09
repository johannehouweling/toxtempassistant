"""Tests for workspace API tokens: who may issue them, and what they can read."""

import io
import os
import secrets
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.http import FileResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone

from toxtempass import api, versions
from toxtempass.export import ANONYMOUS_AUTHOR
from toxtempass.models import (
    ApiPdfJob,
    Person,
    WorkspaceApiToken,
    WorkspaceInvestigation,
    WorkspaceRole,
)
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionFactory,
    QuestionSetFactory,
    SectionFactory,
    StudyFactory,
    SubsectionFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)
from toxtempass.tests.history_helpers import age_history

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


def _issue(client, user, workspace, **data):
    client.force_login(user)
    return client.post(
        reverse("workspace_token_create", args=[workspace.pk]),
        {"name": "reporting", **data},
    )


def _bearer(secret):
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


@pytest.fixture
def shared():
    """A workspace with one shared and one unshared ToxTemp."""
    workspace = WorkspaceFactory()
    inv = InvestigationFactory(owner=workspace.owner)
    in_ws = AssayFactory(study=StudyFactory(investigation=inv))
    outside = AssayFactory(study=StudyFactory(investigation=inv))
    other_inv = InvestigationFactory(owner=workspace.owner)
    unshared = AssayFactory(study=StudyFactory(investigation=other_inv))
    WorkspaceInvestigation.objects.create(workspace=workspace, investigation=inv)
    return workspace, in_ws, outside, unshared


class TestIssuing:
    def test_owner_and_admin_can_issue(self, client):
        workspace = WorkspaceFactory()
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        for user in (workspace.owner, admin):
            response = _issue(client, user, workspace)
            assert response.status_code == 200
            assert response.json()["token"].startswith("ttw_")

    def test_member_and_outsider_cannot_issue(self, client):
        workspace = WorkspaceFactory()
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)
        for user in (member, PersonFactory()):
            assert _issue(client, user, workspace).status_code == 404
        assert not WorkspaceApiToken.objects.exists()

    def test_only_hash_is_stored(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        token = WorkspaceApiToken.objects.get()
        assert secret not in (token.token_hash, token.prefix, token.name)
        assert secret.startswith(token.prefix)

    def test_lifetime_is_bounded(self, client, settings):
        workspace = WorkspaceFactory()
        assert (
            _issue(client, workspace.owner, workspace, expires_days="9999").status_code
            == 400
        )
        assert (
            _issue(client, workspace.owner, workspace, expires_days="abc").status_code
            == 400
        )

    def test_list_never_shows_secret(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        body = client.get(
            reverse("workspace_tokens", args=[workspace.pk])
        ).content.decode()
        assert secret not in body

    def test_a_workspace_holds_a_limited_number_of_active_tokens(self, client, shared):
        workspace, *_ = shared
        with patch("toxtempass.api.config._api_tokens_max_active", 2):
            assert _issue(client, workspace.owner, workspace).status_code == 200
            assert _issue(client, workspace.owner, workspace).status_code == 200
            blocked = _issue(client, workspace.owner, workspace)
            assert blocked.status_code == 400
            assert "revoke one first" in blocked.json()["error"]
            # Revoking one makes room again.
            token = workspace.api_tokens.first()
            client.post(
                reverse("workspace_token_revoke", args=[workspace.pk, token.pk])
            )
            assert _issue(client, workspace.owner, workspace).status_code == 200

    def test_admin_can_revoke_owners_token(self, client, shared):
        workspace, *_ = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        client.force_login(admin)
        token = WorkspaceApiToken.objects.get()
        client.post(reverse("workspace_token_revoke", args=[workspace.pk, token.pk]))
        client.logout()
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 401


class TestReading:
    def test_lists_only_shared_investigations(self, client, shared):
        workspace, in_ws, outside, unshared = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        response = client.get(reverse("api_assay_list"), **_bearer(secret))
        ids = {a["id"] for a in response.json()["results"]}
        assert ids == {in_ws.pk, outside.pk}
        assert unshared.pk not in ids

    def test_detail_of_unshared_assay_is_404(self, client, shared):
        workspace, in_ws, _, unshared = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        ok = client.get(reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret))
        assert ok.status_code == 200
        gone = client.get(
            reverse("api_assay_detail", args=[unshared.pk]), **_bearer(secret)
        )
        assert gone.status_code == 404

    def test_token_of_another_workspace_sees_nothing_here(self, client, shared):
        _, in_ws, _, _ = shared
        other = WorkspaceFactory()
        secret = _issue(client, other.owner, other).json()["token"]
        client.logout()
        response = client.get(
            reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret)
        )
        assert response.status_code == 404

    def test_removing_investigation_stops_access(self, client, shared):
        workspace, in_ws, *_ = shared
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        WorkspaceInvestigation.objects.filter(workspace=workspace).delete()
        response = client.get(
            reverse("api_assay_detail", args=[in_ws.pk]), **_bearer(secret)
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("header", [None, "Bearer nope", "Basic abc", "Bearer "])
    def test_bad_credentials_are_401(self, client, header):
        extra = {} if header is None else {"HTTP_AUTHORIZATION": header}
        response = client.get(reverse("api_assay_list"), **extra)
        assert response.status_code == 401

    def test_expired_token_is_401(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        WorkspaceApiToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 401

    def test_session_login_is_not_enough(self, client, shared):
        workspace, *_ = shared
        client.force_login(workspace.owner)
        assert client.get(reverse("api_assay_list")).status_code == 401

    def test_token_survives_issuer_leaving(self, client):
        workspace = WorkspaceFactory()
        admin = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        secret = _issue(client, admin, workspace).json()["token"]
        client.logout()
        admin.delete()
        assert client.get(reverse("api_assay_list"), **_bearer(secret)).status_code == 200

    def test_use_is_recorded(self, client):
        workspace = WorkspaceFactory()
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        assert WorkspaceApiToken.objects.get().last_used_at is None
        client.get(reverse("api_assay_list"), **_bearer(secret))
        assert WorkspaceApiToken.objects.get().last_used_at is not None


class TestWorkspaceUi:
    def test_settings_cog_only_for_owner_and_admin(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        from toxtempass import workspace as ws_views

        workspace = WorkspaceFactory()
        admin, member = PersonFactory(), PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=admin, role=WorkspaceRole.ADMIN)
        WorkspaceMemberFactory(workspace=workspace, user=member)

        seen = {}
        for label, user in (
            ("owner", workspace.owner),
            ("admin", admin),
            ("member", member),
        ):
            request = RequestFactory().get("/")
            request.user = user
            html = render_to_string(
                "toxtempass/base_extras/workspaces/workspace_list_partial.html",
                ws_views.get_workspace_list(request),
                request=request,
            )
            # Only the cards the server rendered: the script, which everyone gets,
            # holds the cogwheel's markup too, for a workspace just created.
            cards = html.split("<style>")[0]
            seen[label] = 'aria-label="Workspace settings"' in cards
        assert seen == {"owner": True, "admin": True, "member": False}

    def test_every_member_sees_each_token_by_name(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        from toxtempass import workspace as ws_views

        workspace = WorkspaceFactory()
        member = PersonFactory()
        WorkspaceMemberFactory(workspace=workspace, user=member)

        def chips(user) -> int:
            request = RequestFactory().get("/")
            request.user = user
            html = render_to_string(
                "toxtempass/base_extras/workspaces/workspace_list_partial.html",
                ws_views.get_workspace_list(request),
                request=request,
            )
            return html.count("api-access-chip\" data-token-name="), html

        def make(name: str) -> WorkspaceApiToken:
            return WorkspaceApiToken.objects.create(
                workspace=workspace,
                name=name,
                token_hash=name,
                prefix="ttw_x",
                expires_at=timezone.now() + timedelta(days=1),
            )

        assert chips(member)[0] == 0
        first, _ = make("reporting server"), make("dashboard")
        count, html = chips(member)
        assert count == 2
        assert "reporting server" in html and "dashboard" in html
        assert "ttw_x" not in html  # prefixes are for managers only
        assert chips(workspace.owner)[0] == 2
        first.revoked_at = timezone.now()
        first.save()
        assert chips(member)[0] == 1


class TestDataApi:
    def _token(self, client, workspace):
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        return _bearer(secret)

    def test_every_response_names_the_preview_version(self, client, shared):
        workspace, in_ws, *_ = shared
        auth = self._token(client, workspace)
        for name, args in (
            ("api_root", []),
            ("api_assay_list", []),
            ("api_assay_detail", [in_ws.pk]),
            ("api_assay_detail", [999999]),
        ):
            response = client.get(reverse(name, args=args), **auth)
            assert response["X-API-Version"] == "preview"
            assert "no-store" in response["Cache-Control"]
        assert client.get(reverse("api_root"))["X-API-Version"] == "preview"  # 401 too

    def test_root_identifies_the_workspace(self, client, shared):
        workspace, *_ = shared
        body = client.get(reverse("api_root"), **self._token(client, workspace)).json()
        assert body["api_version"] == "preview" and body["stable"] is False
        assert body["workspace"] == {"id": workspace.pk, "name": workspace.name}

    def test_detail_is_json_404_for_unshared(self, client, shared):
        workspace, _, _, unshared = shared
        response = client.get(
            reverse("api_assay_detail", args=[unshared.pk]),
            **self._token(client, workspace),
        )
        assert response.status_code == 404
        assert response.json() == {"error": "Not found"}

    def test_detail_exposes_answers_but_no_internal_fields(self, client, shared):
        workspace, in_ws, *_ = shared
        qset = QuestionSetFactory(label="vdetail")
        question = QuestionFactory(
            subsection=SubsectionFactory(section=SectionFactory(question_set=qset))
        )
        in_ws.question_set = qset
        in_ws.processing_log = "TRACEBACK secret-internal-detail"
        in_ws.user_alerts = [{"message": "internal alert"}]
        in_ws.save()
        AnswerFactory(
            assay=in_ws,
            question=question,
            answer_text="The cells are HepG2.",
            accepted=True,
            answer_documents=["protocol.pdf"],
        )
        response = client.get(
            reverse("api_assay_detail", args=[in_ws.pk]),
            **self._token(client, workspace),
        )
        body = response.json()
        answer = body["sections"][0]["subsections"][0]["questions"][0]["answer"]
        assert answer == {
            "text": "The cells are HepG2.",
            "accepted": True,
            "llm_abstained": None,
            "source_documents": ["protocol.pdf"],
        }
        raw = response.content.decode()
        assert "secret-internal-detail" not in raw
        assert "internal alert" not in raw
        assert workspace.owner.email not in raw

    def test_list_paginates(self, client, shared):
        workspace, *_ = shared
        study = StudyFactory(investigation=shared[1].study.investigation)
        for _ in range(3):
            AssayFactory(study=study)  # 5 shared in total with the fixture
        auth = self._token(client, workspace)
        url = reverse("api_assay_list")
        first = client.get(url, {"limit": 2}, **auth).json()
        assert first["count"] == 5 and len(first["results"]) == 2
        assert first["next_offset"] == 2
        last = client.get(url, {"limit": 2, "offset": 4}, **auth).json()
        assert len(last["results"]) == 1 and last["next_offset"] is None
        seen = {
            r["id"]
            for off in (0, 2, 4)
            for r in client.get(url, {"limit": 2, "offset": off}, **auth).json()[
                "results"
            ]
        }
        assert len(seen) == 5

    @pytest.mark.parametrize("params", [{"limit": "x"}, {"updated_since": "yesterday"}])
    def test_list_rejects_bad_parameters(self, client, shared, params):
        workspace, *_ = shared
        response = client.get(
            reverse("api_assay_list"), params, **self._token(client, workspace)
        )
        assert response.status_code == 400

    def test_updated_since_follows_answer_edits(self, client, shared):
        workspace, in_ws, outside, _ = shared
        auth = self._token(client, workspace)
        url = reverse("api_assay_list")
        marker = timezone.now().isoformat()
        assert client.get(url, {"updated_since": marker}, **auth).json()["count"] == 0
        question = QuestionFactory(
            subsection=SubsectionFactory(
                section=SectionFactory(question_set=QuestionSetFactory(label="vedit"))
            )
        )
        AnswerFactory(assay=in_ws, question=question, answer_text="edited")
        ids = [
            r["id"]
            for r in client.get(url, {"updated_since": marker}, **auth).json()["results"]
        ]
        assert ids == [in_ws.pk]


@pytest.fixture
def pdf_dir(tmp_path):
    with patch.object(ApiPdfJob, "directory", staticmethod(lambda: tmp_path)):
        yield tmp_path


class TestPdf:
    @pytest.fixture(autouse=True)
    def _dir(self, pdf_dir):
        self.dir = pdf_dir

    @pytest.fixture
    def export(self):
        def fake(request, assay, export_type, credited_ids=None):
            return FileResponse(
                io.BytesIO(b"%PDF-fake"),
                as_attachment=True,
                filename="toxtemp.pdf",
                content_type="application/pdf",
            )

        with patch("toxtempass.api.export_assay_to_file", side_effect=fake) as mocked:
            yield mocked

    def _auth(self, client, workspace, name="reporting"):
        secret = _issue(client, workspace.owner, workspace, name=name).json()["token"]
        client.logout()
        return _bearer(secret)

    def _request(self, client, assay, auth):
        return client.post(reverse("api_assay_pdf", args=[assay.pk]), **auth)

    def test_the_pdf_is_built_by_the_queue_and_downloaded_from_the_job(
        self, client, shared, export
    ):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        queued = self._request(client, in_ws, auth)
        assert queued.status_code == 202
        assert queued["X-API-Version"] == "preview"
        job = client.get(queued["Location"], **auth).json()
        assert job["status"] == "done" and job["assay_id"] == in_ws.pk
        assert job["error"] is None
        download = client.get(job["file_url"], **auth)
        assert download.status_code == 200
        assert download["Content-Type"] == "application/pdf"
        assert b"".join(download.streaming_content) == b"%PDF-fake"
        # The file can be fetched again until it expires.
        again = client.get(job["file_url"], **auth)
        assert b"".join(again.streaming_content) == b"%PDF-fake"

    def test_the_build_gets_no_request_and_names_only_agreed_authors(
        self, client, shared, export
    ):
        workspace, in_ws, *_ = shared
        self._request(client, in_ws, self._auth(client, workspace))
        args, kwargs = export.call_args
        assert args[0] is None and args[2] == "pdf"
        assert isinstance(kwargs["credited_ids"], frozenset)

    def test_a_get_is_not_allowed(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        response = client.get(reverse("api_assay_pdf", args=[in_ws.pk]), **auth)
        assert response.status_code == 405
        assert export.call_count == 0

    def test_second_request_within_cooldown_is_429(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        assert self._request(client, in_ws, auth).status_code == 202
        again = self._request(client, in_ws, auth)
        assert again.status_code == 429
        assert 1 <= int(again["Retry-After"]) <= 60
        assert export.call_count == 1  # the second request built nothing

    def test_cooldown_ends(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        self._request(client, in_ws, auth)
        WorkspaceApiToken.objects.update(
            last_pdf_at=timezone.now() - timedelta(seconds=61)
        )
        assert self._request(client, in_ws, auth).status_code == 202

    def test_cooldown_is_per_token(self, client, shared, export):
        workspace, in_ws, *_ = shared
        one = self._auth(client, workspace, "one")
        two = self._auth(client, workspace, "two")
        assert self._request(client, in_ws, one).status_code == 202
        assert self._request(client, in_ws, two).status_code == 202

    def test_json_reads_are_not_limited_by_the_pdf_cooldown(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        self._request(client, in_ws, auth)
        for _ in range(3):
            response = client.get(reverse("api_assay_detail", args=[in_ws.pk]), **auth)
            assert response.status_code == 200

    def test_unknown_assay_does_not_use_the_cooldown(self, client, shared, export):
        workspace, in_ws, _, unshared = shared
        auth = self._auth(client, workspace)
        assert self._request(client, unshared, auth).status_code == 404
        assert export.call_count == 0
        assert self._request(client, in_ws, auth).status_code == 202

    def test_failed_build_is_reported_and_gives_the_cooldown_back(self, client, shared):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        broken = JsonResponse({"error": "Export failed (ref abc)"}, status=500)
        with patch("toxtempass.api.export_assay_to_file", return_value=broken):
            queued = self._request(client, in_ws, auth)
        job = client.get(queued["Location"], **auth).json()
        assert job["status"] == "failed" and job["file_url"] is None
        assert "request it again" in job["error"]
        assert "abc" not in job["error"]
        assert WorkspaceApiToken.objects.get().last_pdf_at is None
        file_response = client.get(
            reverse("api_pdf_job_file", args=[job["id"]]), **auth
        )
        assert file_response.status_code == 410
        ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
        with patch("toxtempass.api.export_assay_to_file", return_value=ok):
            assert self._request(client, in_ws, auth).status_code == 202

    def test_a_crashing_build_is_reported_and_does_not_raise(self, client, shared):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        with patch("toxtempass.api.export_assay_to_file", side_effect=RuntimeError("x")):
            queued = self._request(client, in_ws, auth)
        assert queued.status_code == 202
        assert client.get(queued["Location"], **auth).json()["status"] == "failed"

    def test_pdf_needs_a_token(self, client, shared, export):
        _, in_ws, *_ = shared
        url = reverse("api_assay_pdf", args=[in_ws.pk])
        assert client.post(url).status_code == 401
        assert export.call_count == 0

    def test_root_advertises_the_pdf_endpoints_and_limits(self, client, shared):
        workspace, in_ws, *_ = shared
        body = client.get(reverse("api_root"), **self._auth(client, workspace)).json()
        assert body["endpoints"]["assay_pdf"].endswith("/{id}/pdf/")
        assert body["endpoints"]["pdf_job"].endswith("/pdf-jobs/{job_id}/")
        assert body["limits"] == {"pdf_cooldown_seconds": 60, "pdf_retention_minutes": 60}

    def _job(self, workspace, assay, **fields):
        return ApiPdfJob.objects.create(
            workspace=workspace,
            token=workspace.api_tokens.first(),
            assay=assay,
            **fields,
        )

    def test_asking_again_for_an_unfinished_pdf_returns_the_same_job(
        self, client, shared, export
    ):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        job = self._job(workspace, in_ws)
        again = self._request(client, in_ws, auth)
        assert again.status_code == 202
        assert again.json()["id"] == str(job.pk)
        assert export.call_count == 0

    def test_one_unfinished_job_per_token(self, client, shared, export):
        workspace, in_ws, other_in_ws, _ = shared
        auth = self._auth(client, workspace)
        self._job(workspace, in_ws)
        response = self._request(client, other_in_ws, auth)
        assert response.status_code == 429 and "Retry-After" in response
        assert export.call_count == 0

    def test_too_many_unfinished_jobs_overall_is_503(self, client, shared, export):
        workspace, in_ws, other_in_ws, _ = shared
        auth = self._auth(client, workspace)
        other = WorkspaceFactory()
        token = WorkspaceApiToken.objects.create(
            workspace=other, name="x", token_hash="h" * 64, prefix="ttw_x",
            created_by=other.owner,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ApiPdfJob.objects.create(workspace=other, token=token, assay=other_in_ws)
        with patch("toxtempass.api.config._api_pdf_max_active_jobs", 1):
            response = self._request(client, in_ws, auth)
        assert response.status_code == 503 and "Retry-After" in response
        assert export.call_count == 0
        assert WorkspaceApiToken.objects.get(workspace=workspace).last_pdf_at is None

    def test_a_job_is_private_to_its_workspace(self, client, shared, export):
        workspace, in_ws, *_ = shared
        job_url = self._request(client, in_ws, self._auth(client, workspace))["Location"]
        other = WorkspaceFactory()
        theirs = _bearer(_issue(client, other.owner, other).json()["token"])
        client.logout()
        assert client.get(job_url, **theirs).status_code == 404
        assert client.get(job_url + "file/", **theirs).status_code == 404

    def test_the_file_is_not_served_before_it_is_ready(self, client, shared):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        job = self._job(workspace, in_ws)
        response = client.get(reverse("api_pdf_job_file", args=[job.pk]), **auth)
        assert response.status_code == 409

    def test_an_expired_pdf_reads_expired_and_is_gone(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        queued = self._request(client, in_ws, auth)
        job = ApiPdfJob.objects.get()
        job.expires_at = timezone.now() - timedelta(seconds=1)
        job.save()
        body = client.get(queued["Location"], **auth).json()
        assert body["status"] == "expired" and body["file_url"] is None
        assert client.get(
            reverse("api_pdf_job_file", args=[job.pk]), **auth
        ).status_code == 410


class TestPdfCleanup:
    NOW = timezone.now()

    def _job(self, **fields):
        workspace = WorkspaceFactory()
        token = WorkspaceApiToken.objects.create(
            workspace=workspace, name="t", token_hash=secrets.token_hex(32),
            prefix="ttw_t", created_by=workspace.owner,
            expires_at=timezone.now() + timedelta(days=1),
        )
        return ApiPdfJob.objects.create(
            workspace=workspace, token=token, assay=AssayFactory(), **fields
        )

    def _file(self, job, age_seconds=0):
        job.file_path.write_bytes(b"%PDF")
        stamp = timezone.now().timestamp() - age_seconds
        os.utime(job.file_path, (stamp, stamp))

    def test_expired_files_go_and_live_ones_stay(self, pdf_dir):
        gone = self._job(
            status="done", expires_at=timezone.now() - timedelta(minutes=1)
        )
        kept = self._job(status="done", expires_at=timezone.now() + timedelta(minutes=30))
        self._file(gone, 7200)
        self._file(kept, 7200)

        api.cleanup_pdf_jobs()

        assert not gone.file_path.exists()
        assert kept.file_path.exists()

    def test_a_job_that_lost_its_worker_is_failed_and_frees_the_cooldown(self, pdf_dir):
        job = self._job(created_at=timezone.now() - timedelta(minutes=45))
        WorkspaceApiToken.objects.filter(pk=job.token_id).update(
            last_pdf_at=job.created_at
        )

        api.cleanup_pdf_jobs()

        job.refresh_from_db()
        assert job.status == "failed" and "interrupted" in job.error
        assert WorkspaceApiToken.objects.get(pk=job.token_id).last_pdf_at is None

    def test_a_fresh_queued_job_is_left_alone(self, pdf_dir):
        job = self._job()
        api.cleanup_pdf_jobs()
        job.refresh_from_db()
        assert job.status == "queued"

    def test_old_records_and_their_files_are_deleted(self, pdf_dir):
        old = self._job(
            status="done",
            created_at=timezone.now() - timedelta(hours=30),
            expires_at=timezone.now() - timedelta(hours=29),
        )
        self._file(old, 7200)
        api.cleanup_pdf_jobs()
        assert not ApiPdfJob.objects.filter(pk=old.pk).exists()
        assert not old.file_path.exists()

    def test_stray_files_go_but_one_being_written_stays(self, pdf_dir):
        stray = pdf_dir / "left-behind.pdf"
        stray.write_bytes(b"x")
        old = timezone.now().timestamp() - 7200
        os.utime(stray, (old, old))
        writing = pdf_dir / "being-written.pdf.part"
        writing.write_bytes(b"x")

        api.cleanup_pdf_jobs()

        assert not stray.exists()
        assert writing.exists()

    def test_it_runs_with_the_periodic_jobs(self):
        from toxtempass import jobs

        with patch("toxtempass.jobs.api.cleanup_pdf_jobs") as cleanup, patch.multiple(
            "toxtempass.jobs",
            notifications=MagicMock(),
            privacy=MagicMock(),
            model_metadata=MagicMock(),
            fx=MagicMock(),
        ):
            jobs.run_periodic_jobs()
        cleanup.assert_called_once()


class TestVersionsInTheApi:
    """Every saved change is a version; the newest is served, earlier ones by id."""

    @pytest.fixture
    def toxtemp(self, client, shared):
        workspace, in_ws, *_ = shared
        qset = QuestionSetFactory(label="vapi")
        sub = SubsectionFactory(section=SectionFactory(question_set=qset))
        question = QuestionFactory(subsection=sub)
        in_ws.question_set = qset
        in_ws.description = "asdf"
        in_ws.save()
        answer = AnswerFactory(assay=in_ws, question=question, answer_text="first")
        age_history(in_ws, 3600)  # so that what is saved later is another version
        secret = _issue(client, workspace.owner, workspace).json()["token"]
        client.logout()
        return workspace, in_ws, answer, _bearer(secret)

    def _get(self, client, name, auth, *args):
        return client.get(reverse(name, args=list(args)), **auth)

    def test_the_detail_lists_its_versions_and_the_list_names_the_newest(
        self, client, toxtemp
    ):
        _, assay, answer, auth = toxtemp
        answer.answer_text = "second"
        answer.save()
        body = self._get(client, "api_assay_detail", auth, assay.pk).json()
        ids = [h["id"] for h in body["history"]]
        assert len(ids) == 2 and len(set(ids)) == len(ids)
        assert body["assay"]["version"] == ids[0]
        listed = self._get(client, "api_assay_list", auth).json()["results"]
        versions_listed = [i["version"] for i in listed if i["id"] == assay.pk]
        assert versions_listed == [ids[0]]

    def test_an_earlier_version_shows_what_has_since_changed_or_gone(
        self, client, toxtemp
    ):
        _, assay, answer, auth = toxtemp
        old = self._get(client, "api_assay_detail", auth, assay.pk).json()["history"][0]
        answer.answer_text = "second"
        answer.save()
        assay.description = "a good description"
        assay.save()

        then = self._get(client, "api_assay_version", auth, assay.pk, old["id"]).json()
        now = self._get(client, "api_assay_detail", auth, assay.pk).json()

        def text(document):
            question = document["sections"][0]["subsections"][0]["questions"][0]
            return question["answer"]["text"]

        assert (text(then), then["assay"]["description"]) == ("first", "asdf")
        assert (text(now), now["assay"]["description"]) == (
            "second",
            "a good description",
        )
        assert then["assay"]["version"] == old["id"]
        assert now["assay"]["version"] != old["id"]

    def test_the_description_moves_last_modified_and_the_sync_filter(
        self, client, toxtemp
    ):
        _, assay, _, auth = toxtemp
        before = self._get(client, "api_assay_list", auth).json()["results"][0]
        assay.description = "a good description"
        assay.save()
        after = self._get(client, "api_assay_list", auth).json()["results"][0]
        assert after["last_modified"] > before["last_modified"]
        assert after["version"] != before["version"]
        synced = client.get(
            reverse("api_assay_list"), {"updated_since": before["last_modified"]}, **auth
        ).json()
        assert [a["id"] for a in synced["results"]] == [assay.pk]

    def test_an_earlier_version_names_authors_by_the_same_rule_as_the_current_one(
        self, client, toxtemp
    ):
        workspace, assay, answer, auth = toxtemp
        creator = assay.created_by or PersonFactory()
        assay.created_by = creator
        assay.save()
        Person.objects.filter(pk=creator.pk).update(credit_by_name=False)
        WorkspaceMemberFactory(workspace=workspace, user=creator)
        old = self._get(client, "api_assay_detail", auth, assay.pk).json()["history"][0]
        then = self._get(client, "api_assay_version", auth, assay.pk, old["id"])
        now = self._get(client, "api_assay_detail", auth, assay.pk)
        for response in (then, now):
            first = response.json()["metadata"]["authors"][0]  # the creator comes first
            assert first["credited"] is False and first["name"] == ANONYMOUS_AUTHOR
            raw = response.content.decode()
            assert creator.email not in raw and creator.get_full_name() not in raw

    def test_a_version_of_a_toxtemp_outside_the_workspace_is_not_found(
        self, client, shared, toxtemp
    ):
        workspace, assay, _, auth = toxtemp
        _, _, _, unshared = shared
        theirs = versions.versions(unshared)[0].id
        mine = versions.versions(assay)[0].id
        wrong = ((unshared.pk, theirs), (assay.pk, theirs), (unshared.pk, mine))
        for assay_id, version_id in wrong:
            response = self._get(client, "api_assay_version", auth, assay_id, version_id)
            assert response.status_code == 404
            assert response.json() == {"error": "Not found"}

    def test_versions_need_a_token(self, client, toxtemp):
        _, assay, *_ = toxtemp
        newest = versions.versions(assay)[0].id
        url = reverse("api_assay_version", args=[assay.pk, newest])
        assert client.get(url).status_code == 401


class TestAuthorsInTheApi:
    """Only members who agreed are named, and never with an email address."""

    SECRETS = ("grace@", "Navy Lab", "0000-0002-1825-0097")

    @pytest.fixture
    def assay(self):
        grace = PersonFactory(
            first_name="Grace",
            last_name="Hopper",
            organization="Navy Lab",
            orcid_id="0000-0002-1825-0097",
            email="grace@navy.example",
        )
        qset = QuestionSetFactory(label="vpeople")
        question = QuestionFactory(
            subsection=SubsectionFactory(section=SectionFactory(question_set=qset))
        )
        assay = AssayFactory(
            study=StudyFactory(investigation=InvestigationFactory(owner=grace)),
            created_by=grace,
            question_set=qset,
        )
        AnswerFactory(assay=assay, question=question, answer_text="HepG2 cells")
        return assay

    def _workspace(self, assay, credited: bool):
        workspace = WorkspaceFactory()
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=assay.study.investigation
        )
        WorkspaceMemberFactory(workspace=workspace, user=assay.created_by)
        Person.objects.filter(pk=assay.created_by_id).update(
            credit_by_name=credited
        )
        return workspace

    def _detail(self, client, workspace, assay):
        client.force_login(workspace.owner)
        secret = client.post(
            reverse("workspace_token_create", args=[workspace.pk]), {"name": "t"}
        ).json()["token"]
        client.logout()
        return client.get(reverse("api_assay_detail", args=[assay.pk]), **_bearer(secret))

    def test_the_in_app_exports_still_name_everyone(self, assay):
        from toxtempass.export import generate_markdown_from_assay

        markdown = generate_markdown_from_assay(assay)
        assert "Grace Hopper" in markdown and "grace@navy.example" in markdown

    def _authors(self, client, workspace, assay):
        return self._detail(client, workspace, assay).json()["metadata"]["authors"]

    def test_an_author_who_agreed_is_named_without_email(self, client, assay):
        response = self._detail(client, self._workspace(assay, credited=True), assay)
        assert response.json()["metadata"]["authors"] == [
            {
                "credited": True,
                "name": "Grace Hopper",
                "organization": "Navy Lab",
                "orcid_id": "0000-0002-1825-0097",
                "email": None,
            }
        ]
        assert response.json()["metadata"]["investigation_owner"] is None
        assert "grace@navy.example" not in response.content.decode()

    def test_an_author_who_has_not_agreed_keeps_their_place_unnamed(self, client, assay):
        response = self._detail(client, self._workspace(assay, credited=False), assay)
        assert response.json()["metadata"]["authors"] == [
            {
                "credited": False,
                "name": "Contributor (not named)",
                "organization": None,
                "orcid_id": None,
                "email": None,
            }
        ]
        raw = response.content.decode()
        for secret in ("Grace", "Hopper", *self.SECRETS):
            assert secret not in raw

    def test_an_author_outside_the_workspace_is_not_named(self, client, assay):
        # Agreed to be credited, but is not a member of this workspace.
        Person.objects.filter(pk=assay.created_by_id).update(credit_by_name=True)
        workspace = WorkspaceFactory()
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=assay.study.investigation
        )
        authors = self._authors(client, workspace, assay)
        assert authors[0]["credited"] is False

    def test_withdrawing_applies_on_the_next_request(self, client, assay):
        workspace = self._workspace(assay, credited=True)
        assert self._authors(client, workspace, assay)[0]["credited"]
        client.force_login(assay.created_by)
        client.post(reverse("account_set_credit"), {"credit": "off"})
        client.logout()
        assert not self._authors(client, workspace, assay)[0]["credited"]

    def test_an_account_without_a_name_is_not_named_by_its_email(self, client, assay):
        assay.created_by.first_name = assay.created_by.last_name = ""
        assay.created_by.save()
        workspace = self._workspace(assay, credited=True)
        response = self._detail(client, workspace, assay)
        assert response.json()["metadata"]["authors"][0]["credited"] is False
        assert "grace@navy.example" not in response.content.decode()

    def test_the_order_is_creator_then_editors_then_owner(self, client):
        owner = PersonFactory(first_name="Olga", last_name="Owner")
        creator = PersonFactory(first_name="Carl", last_name="Creator")
        editor = PersonFactory(first_name="Edith", last_name="Editor")
        qset = QuestionSetFactory(label="vorder")
        question = QuestionFactory(
            subsection=SubsectionFactory(section=SectionFactory(question_set=qset))
        )
        assay = AssayFactory(
            study=StudyFactory(investigation=InvestigationFactory(owner=owner)),
            created_by=creator,
            question_set=qset,
        )
        answer = AnswerFactory(assay=assay, question=question, answer_text="a")
        answer._history_user = editor
        answer.answer_text = "edited"
        answer.save()
        workspace = WorkspaceFactory()
        WorkspaceInvestigation.objects.create(
            workspace=workspace, investigation=assay.study.investigation
        )
        for person in (owner, creator, editor):
            WorkspaceMemberFactory(workspace=workspace, user=person)
        Person.objects.filter(pk__in=[owner.pk, creator.pk, editor.pk]).update(
            credit_by_name=True
        )
        names = [
            a["name"] for a in self._authors(client, workspace, assay)
        ]
        assert names == ["Carl Creator", "Edith Editor", "Olga Owner"]

    def test_the_pdf_is_built_with_exactly_the_agreed_members(
        self, client, assay, pdf_dir
    ):
        workspace = self._workspace(assay, credited=True)
        # The workspace owner is a member too, and has switched it off.
        Person.objects.filter(pk=workspace.owner_id).update(credit_by_name=False)
        client.force_login(workspace.owner)
        secret = client.post(
            reverse("workspace_token_create", args=[workspace.pk]), {"name": "t"}
        ).json()["token"]
        client.logout()
        ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
        with patch("toxtempass.api.export_assay_to_file", return_value=ok) as export:
            client.post(reverse("api_assay_pdf", args=[assay.pk]), **_bearer(secret))
        assert export.call_args.kwargs == {
            "credited_ids": frozenset({assay.created_by_id})
        }

    def test_the_api_exports_never_contain_an_email(self, assay, tmp_path):
        import json

        import yaml

        from toxtempass.export import (
            generate_json_from_assay,
            generate_markdown_from_assay,
            get_create_meta_data_yaml,
        )

        credited = {assay.created_by_id}
        markdown = generate_markdown_from_assay(assay, credited)
        as_json = json.dumps(generate_json_from_assay(assay, credited))
        yaml_path = get_create_meta_data_yaml(
            None, assay, tmp_path / "t.pdf", credited_ids=credited
        )
        metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert metadata["author"] == ["Grace Hopper"]
        for text in (markdown, as_json, yaml_path.read_text(encoding="utf-8")):
            assert "grace@navy.example" not in text
        assert "Grace Hopper" in markdown and "HepG2 cells" in markdown
        # And with nobody credited, the name is gone but the author is still counted.
        unnamed = generate_markdown_from_assay(assay, set())
        assert "Grace" not in unnamed and "Contributor (not named)" in unnamed
