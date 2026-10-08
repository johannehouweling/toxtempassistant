"""The OpenAPI file is the API contract; these tests hold the code to it.

``toxtempass/openapi/preview.yaml`` is written by hand and is what partners read.
Here every route must be in it, every real response must validate against it
(including the status code, headers and the absence of undocumented fields), and
the file itself must be a valid OpenAPI document. Breaking changes to a frozen
version are caught separately in CI (``oasdiff``, .github/workflows/api-contract.yml).
"""

import io
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from django.http import FileResponse, JsonResponse
from django.urls import get_resolver, resolve, reverse
from django.utils import timezone
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from toxtempass.models import WorkspaceApiToken, WorkspaceInvestigation
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    InvestigationFactory,
    QuestionFactory,
    QuestionSetFactory,
    SectionFactory,
    StudyFactory,
    SubsectionFactory,
    WorkspaceFactory,
)

pytestmark = pytest.mark.django_db

SPEC_FILE = Path(__file__).parents[1] / "openapi" / "preview.yaml"
SPEC = yaml.safe_load(SPEC_FILE.read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(
    "urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012)
)
# Served for humans and tools, not part of the data API itself.
NOT_IN_CONTRACT = {"api_docs", "api_openapi"}


def _resolve_ref(node: dict) -> dict:
    """Follow a ``#/components/...`` reference to the object it points at."""
    while "$ref" in node:
        target = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _spec_path(url_path: str) -> str:
    """Return the spec's path template for a real URL, e.g. ``.../{assay_id}/``."""
    route = "/" + str(resolve(url_path).route).lstrip("^").rstrip("$")
    out = route
    for kwarg in resolve(url_path).kwargs:
        out = out.replace(f"<int:{kwarg}>", f"{{{kwarg}}}")
    return out


def check(response, url_path: str) -> None:
    """Assert a response is one the contract describes, in status, headers and body."""
    operation = SPEC["paths"][_spec_path(url_path)]["get"]
    declared = operation["responses"]
    assert str(response.status_code) in declared, (
        f"{url_path} returned {response.status_code}, which the spec does not declare"
    )
    spec_response = _resolve_ref(declared[str(response.status_code)])
    for header in spec_response.get("headers", {}):
        assert header in response, f"{url_path}: header {header} is documented but absent"
    content = spec_response.get("content", {})
    media = response["Content-Type"].split(";")[0]
    assert media in content, f"{url_path}: {media} is not a documented content type"
    schema = content[media]["schema"]
    if media == "application/json":
        body = response.json()
        validator = Draft202012Validator(_inline(schema), registry=REGISTRY)
        errors = sorted(validator.iter_errors(body), key=lambda e: list(e.path))
        assert not errors, f"{url_path}: {errors[0].message} at {list(errors[0].path)}"


def _inline(schema: dict) -> dict:
    """Turn a local ``#/components/...`` ref into one the registry can resolve."""
    if "$ref" in schema:
        return {"$ref": "urn:spec" + schema["$ref"]}
    return schema


def _bearer(secret: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def populated(client):
    """A workspace sharing one fully answered ToxTemp and one legacy, empty one."""
    workspace = WorkspaceFactory()
    investigation = InvestigationFactory(owner=workspace.owner)
    qset = QuestionSetFactory(label="vcontract")
    section = SectionFactory(question_set=qset)
    sub = SubsectionFactory(section=section)
    parent = QuestionFactory(subsection=sub)
    child = QuestionFactory(subsection=sub, parent_question=parent)
    full = AssayFactory(
        study=StudyFactory(investigation=investigation), question_set=qset
    )
    AnswerFactory(
        assay=full,
        question=parent,
        answer_text="HepG2",
        accepted=True,
        llm_abstained=False,
        answer_documents=["protocol.pdf"],
    )
    AnswerFactory(assay=full, question=child, answer_text="", accepted=None)
    legacy = AssayFactory(study=StudyFactory(investigation=investigation))
    WorkspaceInvestigation.objects.create(
        workspace=workspace, investigation=investigation
    )
    client.force_login(workspace.owner)
    secret = client.post(
        reverse("workspace_token_create", args=[workspace.pk]), {"name": "contract"}
    ).json()["token"]
    client.logout()
    return {
        "workspace": workspace,
        "full": full,
        "legacy": legacy,
        "auth": _bearer(secret),
    }


def test_spec_is_a_valid_openapi_document():
    validate_openapi(SPEC)


def test_every_route_is_in_the_spec_and_the_other_way_round():
    routes = set()
    for pattern in get_resolver().url_patterns:
        route = str(pattern.pattern)
        if route.startswith("api/preview/") and pattern.name not in NOT_IN_CONTRACT:
            routes.add("/" + route.replace("<int:assay_id>", "{assay_id}"))
    assert routes == set(SPEC["paths"])


def test_every_documented_response_is_defined():
    """Every status a path declares has a description (no empty promises)."""
    for path, item in SPEC["paths"].items():
        for status, response in item["get"]["responses"].items():
            assert _resolve_ref(response).get("description"), f"{path} {status}"


def test_root(client, populated):
    url = reverse("api_root")
    check(client.get(url, **populated["auth"]), url)


def test_list(client, populated):
    url = reverse("api_assay_list")
    response = client.get(url, **populated["auth"])
    check(response, url)
    assert response.json()["count"] == 2
    paged = client.get(url, {"limit": 1}, **populated["auth"])
    check(paged, url)
    assert paged.json()["next_offset"] == 1


@pytest.mark.parametrize("which", ["full", "legacy"])
def test_detail(client, populated, which):
    url = reverse("api_assay_detail", args=[populated[which].pk])
    response = client.get(url, **populated["auth"])
    check(response, url)
    if which == "full":
        question = response.json()["sections"][0]["subsections"][0]["questions"]
        assert {q["parent_question_id"] for q in question} == {None, question[0]["id"]}


def test_pdf(client, populated):
    url = reverse("api_assay_pdf", args=[populated["full"].pk])
    ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
    ok["X-API-Version"] = "preview"
    with patch("toxtempass.api.export_assay_to_file", return_value=ok):
        check(client.get(url, **populated["auth"]), url)


def test_pdf_cooldown_and_failure_are_documented(client, populated):
    url = reverse("api_assay_pdf", args=[populated["full"].pk])
    ok = FileResponse(io.BytesIO(b"%PDF"), content_type="application/pdf")
    with patch("toxtempass.api.export_assay_to_file", return_value=ok):
        client.get(url, **populated["auth"])
        limited = client.get(url, **populated["auth"])
    assert limited.status_code == 429
    check(limited, url)
    WorkspaceApiToken.objects.update(last_pdf_at=timezone.now() - timedelta(minutes=5))
    broken = JsonResponse({"error": "Export failed (ref abc)"}, status=500)
    with patch("toxtempass.api.export_assay_to_file", return_value=broken):
        failed = client.get(url, **populated["auth"])
    assert failed.status_code == 500
    check(failed, url)


@pytest.mark.parametrize(
    "name", ["api_root", "api_assay_list", "api_assay_detail", "api_assay_pdf"]
)
def test_unauthorized(client, populated, name):
    args = [populated["full"].pk] if name in {"api_assay_detail", "api_assay_pdf"} else []
    url = reverse(name, args=args)
    response = client.get(url)
    assert response.status_code == 401
    check(response, url)


@pytest.mark.parametrize("name", ["api_assay_detail", "api_assay_pdf"])
def test_not_found(client, populated, name):
    url = reverse(name, args=[999999])
    response = client.get(url, **populated["auth"])
    assert response.status_code == 404
    check(response, url)


def test_bad_query_is_documented(client, populated):
    url = reverse("api_assay_list")
    response = client.get(url, {"limit": "x"}, **populated["auth"])
    assert response.status_code == 400
    check(response, url)


def test_an_undocumented_field_would_fail_the_contract(client, populated):
    """The check has teeth: an extra field in a response is a contract violation."""
    url = reverse("api_root")
    body = client.get(url, **populated["auth"]).json()
    body["workspace"]["owner_email"] = "leak@example.org"
    validator = Draft202012Validator(
        {"$ref": "urn:spec#/components/schemas/Root"}, registry=REGISTRY
    )
    assert list(validator.iter_errors(body))


def test_docs_are_public_and_serve_the_same_file(client):
    assert client.get(reverse("api_docs")).status_code == 200
    served = client.get(reverse("api_openapi"))
    assert served.status_code == 200
    assert served.json() == SPEC
