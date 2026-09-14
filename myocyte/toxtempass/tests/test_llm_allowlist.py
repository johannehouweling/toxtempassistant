"""Users may only choose models the admin ticked; none ticked means no choice."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse

from toxtempass.llm import current_llm_key
from toxtempass.models import LLMConfig
from toxtempass.tests.fixtures.factories import PersonFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def registry():
    """Two active fake deployments, 1:A (the admin default) and 1:B."""
    models = [
        SimpleNamespace(tag=tag, model_id=f"model-{tag}", retirement_status="active")
        for tag in ("A", "B")
    ]
    endpoint = SimpleNamespace(index=1, models=models)

    def get_model(index, tag):
        found = [m for m in models if m.tag == tag] if index == 1 else []
        return (endpoint, found[0]) if found else None

    with (
        patch("toxtempass.azure_registry.get_registry", return_value=[endpoint]),
        patch("toxtempass.azure_registry.get_model", side_effect=get_model),
    ):
        yield


def _allow(*keys):
    llm_config = LLMConfig.load()
    llm_config.default_model = "1:A"
    llm_config.allowed_models = list(keys)
    llm_config.save()


def test_preference_counts_only_when_the_admin_ticked_that_model():
    user = PersonFactory(preferences={"llm_model": "1:B"})

    _allow()
    assert current_llm_key(user) == "1:A"

    _allow("1:A")
    assert current_llm_key(user) == "1:A"

    _allow("1:B")
    assert current_llm_key(user) == "1:B"


def test_superusers_may_use_any_model():
    _allow()
    admin = PersonFactory(is_superuser=True, preferences={"llm_model": "1:B"})
    assert current_llm_key(admin) == "1:B"


def test_users_cannot_save_a_model_nobody_ticked(client):
    user = PersonFactory()
    client.force_login(user)
    _allow()

    response = client.post(reverse("set_llm_preference"), {"llm_model": "1:B"})

    assert response.status_code == 403
    user.refresh_from_db()
    assert "llm_model" not in (user.preferences or {})
