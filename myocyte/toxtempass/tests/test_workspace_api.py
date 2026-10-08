"""Tests for workspace API tokens: who may issue them, and what they can read."""

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.http import FileResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone

from toxtempass.models import (
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
            seen[label] = (
                'aria-label="Workspace settings"'
                in html  # the button; the JS is shown to all
            )
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
            return html.count('class="badge text-bg-warning-subtle'), html

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


class TestPdf:
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

    def test_pdf_is_returned(self, client, shared, export):
        workspace, in_ws, *_ = shared
        response = client.get(
            reverse("api_assay_pdf", args=[in_ws.pk]), **self._auth(client, workspace)
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["X-API-Version"] == "preview"
        assert b"".join(response.streaming_content) == b"%PDF-fake"

    def test_second_request_within_cooldown_is_429(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        url = reverse("api_assay_pdf", args=[in_ws.pk])
        assert client.get(url, **auth).status_code == 200
        again = client.get(url, **auth)
        assert again.status_code == 429
        assert 1 <= int(again["Retry-After"]) <= 60
        assert export.call_count == 1  # the second request built nothing

    def test_cooldown_ends(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        url = reverse("api_assay_pdf", args=[in_ws.pk])
        client.get(url, **auth)
        WorkspaceApiToken.objects.update(
            last_pdf_at=timezone.now() - timedelta(seconds=61)
        )
        assert client.get(url, **auth).status_code == 200

    def test_cooldown_is_per_token(self, client, shared, export):
        workspace, in_ws, *_ = shared
        one = self._auth(client, workspace, "one")
        two = self._auth(client, workspace, "two")
        url = reverse("api_assay_pdf", args=[in_ws.pk])
        assert client.get(url, **one).status_code == 200
        assert client.get(url, **two).status_code == 200

    def test_json_reads_are_not_limited_by_the_pdf_cooldown(self, client, shared, export):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        client.get(reverse("api_assay_pdf", args=[in_ws.pk]), **auth)
        for _ in range(3):
            response = client.get(reverse("api_assay_detail", args=[in_ws.pk]), **auth)
            assert response.status_code == 200

    def test_unknown_assay_does_not_use_the_cooldown(self, client, shared, export):
        workspace, in_ws, _, unshared = shared
        auth = self._auth(client, workspace)
        missing = client.get(reverse("api_assay_pdf", args=[unshared.pk]), **auth)
        assert missing.status_code == 404
        assert export.call_count == 0
        assert (
            client.get(reverse("api_assay_pdf", args=[in_ws.pk]), **auth).status_code
            == 200
        )

    def test_failed_build_gives_the_cooldown_back(self, client, shared):
        workspace, in_ws, *_ = shared
        auth = self._auth(client, workspace)
        url = reverse("api_assay_pdf", args=[in_ws.pk])
        broken = JsonResponse({"error": "Export failed (ref abc)"}, status=500)
        with patch("toxtempass.api.export_assay_to_file", return_value=broken):
            assert client.get(url, **auth).status_code == 500
        assert WorkspaceApiToken.objects.get().last_pdf_at is None
        ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
        with patch("toxtempass.api.export_assay_to_file", return_value=ok):
            assert client.get(url, **auth).status_code == 200

    def test_pdf_needs_a_token(self, client, shared, export):
        _, in_ws, *_ = shared
        assert client.get(reverse("api_assay_pdf", args=[in_ws.pk])).status_code == 401
        assert export.call_count == 0

    def test_root_advertises_the_pdf_endpoint_and_limit(self, client, shared):
        workspace, in_ws, *_ = shared
        body = client.get(reverse("api_root"), **self._auth(client, workspace)).json()
        assert body["endpoints"]["assay_pdf"].endswith("/{id}/pdf/")
        assert body["limits"] == {"pdf_cooldown_seconds": 60}


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
        WorkspaceMemberFactory(
            workspace=workspace,
            user=assay.created_by,
            credit_consent_at=timezone.now() if credited else None,
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
        # Agreed in some other workspace, but not a member of this one.
        other = WorkspaceFactory()
        WorkspaceMemberFactory(
            workspace=other, user=assay.created_by, credit_consent_at=timezone.now()
        )
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
        client.post(
            reverse("set_workspace_credit", args=[workspace.pk]), {"credit": "off"}
        )
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
            WorkspaceMemberFactory(
                workspace=workspace, user=person, credit_consent_at=timezone.now()
            )
        names = [
            a["name"] for a in self._authors(client, workspace, assay)
        ]
        assert names == ["Carl Creator", "Edith Editor", "Olga Owner"]

    def test_the_pdf_is_built_with_exactly_the_agreed_members(self, client, assay):
        workspace = self._workspace(assay, credited=True)
        client.force_login(workspace.owner)
        secret = client.post(
            reverse("workspace_token_create", args=[workspace.pk]), {"name": "t"}
        ).json()["token"]
        client.logout()
        ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
        with patch("toxtempass.api.export_assay_to_file", return_value=ok) as export:
            client.get(reverse("api_assay_pdf", args=[assay.pk]), **_bearer(secret))
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
