import logging
import os
from functools import lru_cache
from typing import Literal

from django.core.exceptions import ImproperlyConfigured
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import Field, model_validator

from toxtempass import config as app_config

# Get logger
logger = logging.getLogger("llm")


def _resolve_azure_config() -> tuple[str | None, str | None, str | None]:
    """Read the LLMConfig singleton and return (api_key, base_url, model).

    Returns (None, None, None) when no Azure endpoints are configured or
    the DB is not ready yet (e.g. during initial migrations).
    """
    try:
        from toxtempass.azure_registry import get_model, get_registry
        from toxtempass.models import LLMConfig

        registry = get_registry()
        if not registry:
            return None, None, None

        cfg = LLMConfig.load()
        idx = cfg.default_endpoint_index
        tag = cfg.default_model_tag
        if idx is not None and tag:
            result = get_model(idx, tag)
            if result:
                ep, model_entry = result
                return ep.api_key, ep.endpoint, model_entry.model_id

        # Env-tagged default (default:true) as bootstrap fallback.
        from toxtempass.azure_registry import env_default_key
        env_key = env_default_key()
        if env_key and ":" in env_key:
            try:
                eidx_s, etag = env_key.split(":", 1)
                result = get_model(int(eidx_s), etag)
            except (ValueError, TypeError):
                result = None
            if result:
                ep, model_entry = result
                return ep.api_key, ep.endpoint, model_entry.model_id

        # Last-ditch: first endpoint, first model
        ep = registry[0]
        if ep.models:
            return ep.api_key, ep.endpoint, ep.models[0].model_id
        return ep.api_key, ep.endpoint, None
    except Exception:
        # DB not ready, migrations pending, etc.
        return None, None, None


def get_llm():
    """Return the currently-configured default LLM client.

    Not cached at this layer — each call re-reads ``LLMConfig`` so admin changes
    propagate to all workers on their next request. The underlying per-deployment
    client *is* cached inside :func:`get_llm_for_endpoint`, so repeated calls
    with the same default are effectively free.
    """
    # Fast path: admin picked an Azure deployment → honour its api tag.
    try:
        from toxtempass.models import LLMConfig
        cfg = LLMConfig.load()
        if cfg.default_model and ":" in cfg.default_model:
            idx, tag = cfg.default_model.split(":", 1)
            return get_llm_for_endpoint(int(idx), tag, temperature=0)
    except Exception:
        pass  # DB not ready, no row yet, etc. — fall through to env-default.

    # Env-tagged bootstrap default (`default:true` in AZURE_E*_TAGS_*).
    try:
        from toxtempass.azure_registry import env_default_key
        env_key = env_default_key()
        if env_key and ":" in env_key:
            idx, tag = env_key.split(":", 1)
            return get_llm_for_endpoint(int(idx), tag, temperature=0)
    except Exception:
        pass

    # Import here to avoid early import-time races
    try:
        from toxtempass import config, LLM_API_KEY, LLM_ENDPOINT  # noqa: I001
    except Exception:
        config = None
        LLM_API_KEY = None
        LLM_ENDPOINT = None

    # Try Azure registry first (reads admin LLMConfig from DB)
    azure_key, azure_url, azure_model = _resolve_azure_config()

    api_key = (
        azure_key
        or LLM_API_KEY
        or (getattr(config, "api_key", None) if config else None)
        or os.getenv("OPENAI_API_KEY")
    )

    base_url = (
        azure_url
        or LLM_ENDPOINT
        or (getattr(config, "url", None) if config else None)
        or os.getenv("OPENAI_BASE_URL")
    )

    model = (
        azure_model
        or (getattr(config, "model", None) if config else None)
        or "gpt-4o-mini"
    )
    temperature = (getattr(config, "temperature", None) if config else None) or 0
    extra_headers = getattr(config, "extra_headers", None) if config else None

    if not api_key and not os.getenv("TESTING"):  # allow missing key in tests
        raise ImproperlyConfigured(
            "LLM API key missing. Set AZURE_E1_KEY or OPENAI_API_KEY."
        )

    logger.info("LLM configured")
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,  # fine if None
        model=model,
        temperature=temperature,
        default_headers=extra_headers,
        timeout=120,
        # langchain-openai renames this to max_completion_tokens for the models
        # that require it, so one name is safe across the family.
        max_tokens=app_config.llm_max_output_tokens,
    )


def current_llm_key(user) -> str | None:
    """Return the ``"idx:tag"`` deployment key the user would be served *right now*.

    Use this at queue time to snapshot a user's model choice so the worker uses
    the same deployment when the task actually runs, regardless of any preference
    changes the user makes in the meantime. Returns ``None`` when the Azure
    registry has no models — in that case the worker falls back to legacy env creds.
    """
    from toxtempass.azure_registry import get_model, get_registry
    from toxtempass.models import LLMConfig

    if not get_registry():
        return None

    # Honour a valid user preference first.
    prefs = (getattr(user, "preferences", None) or {}) if user else {}
    raw = prefs.get("llm_model") if isinstance(prefs, dict) else None
    if raw and ":" in raw:
        try:
            idx_s, tag = raw.split(":", 1)
            result = get_model(int(idx_s), tag)
        except (ValueError, TypeError):
            result = None
        if result is not None:
            ep, m = result
            if m.retirement_status != "retired":
                cfg = LLMConfig.load()
                allowed = cfg.allowed_models or []
                is_superuser = bool(getattr(user, "is_superuser", False))
                # An empty allowlist allows no user choice, only the default.
                if raw in allowed or is_superuser:
                    return f"{ep.index}:{m.tag}"

    # Fall back to the admin-selected default.
    try:
        cfg = LLMConfig.load()
    except Exception:
        cfg = None
    if cfg and cfg.default_model and ":" in cfg.default_model:
        try:
            idx_s, tag = cfg.default_model.split(":", 1)
            result = get_model(int(idx_s), tag)
        except (ValueError, TypeError):
            result = None
        if result is not None and result[1].retirement_status != "retired":
            return cfg.default_model

    # Final fallback: the deployment tagged ``default:true`` in .env.
    from toxtempass.azure_registry import env_default_key
    return env_default_key()


def model_with_room_for(user: object, required_tokens: int) -> str | None:
    """Return a model the user may pick whose input ceiling fits ``required_tokens``.

    Used when documents had to be truncated, so the alert can name a way out
    instead of only reporting the loss. Returns ``None`` when the user has no
    choice to make -- the admin ticked nothing, so everyone gets the default --
    or when nothing available is big enough, in which case suggesting a switch
    would waste their time.

    The smallest sufficient model is returned rather than the largest: it is
    the cheapest one that solves their problem.
    """
    from toxtempass import model_metadata
    from toxtempass.azure_registry import get_registry
    from toxtempass.models import LLMConfig

    try:
        cfg = LLMConfig.load()
    except Exception:
        return None
    allowed = set(cfg.allowed_models or [])
    is_superuser = bool(getattr(user, "is_superuser", False))
    if not allowed and not is_superuser:
        return None

    candidates: list[tuple[int, str]] = []
    for endpoint in get_registry():
        for entry in endpoint.models:
            if entry.retirement_status == "retired":
                continue
            if f"{endpoint.index}:{entry.tag}" not in allowed and not is_superuser:
                continue
            ceiling = model_metadata.lookup(
                entry.model_id,
                tier=entry.tags.get("tier"),
                residency=entry.tags.get("residency"),
            ).max_input_tokens
            if ceiling and ceiling >= required_tokens:
                candidates.append((ceiling, entry.model_id))
    if not candidates:
        return None
    return min(candidates)[1]


def resolve_user_llm(user, temperature: float | int = 0):
    """Return ``(llm, source, replaced)`` for a given user.

    Resolution order (silent fallback, option A):
        1. ``user.preferences["llm_model"]`` if set, still in allowed list, not retired.
        2. ``LLMConfig.default_model`` (admin-chosen default).
        3. Legacy ``get_llm()`` fallback (OpenAI env creds).

    ``source`` is one of ``"user" | "admin_default" | "legacy"``.
    ``replaced`` is ``True`` when the user had a preference that was invalid/retired/
    disallowed — the caller should surface a non-blocking toast in that case.

    When ``source == "user"``, the user's stored preference is also cleaned up
    (stripped of invalid entries) so the same toast doesn't trigger again next time.
    """
    from toxtempass.azure_registry import get_model, get_registry
    from toxtempass.models import LLMConfig

    replaced = False
    user_pref = None
    prefs = (getattr(user, "preferences", None) or {}) if user else {}
    raw_pref = prefs.get("llm_model") if isinstance(prefs, dict) else None

    if raw_pref and ":" in raw_pref:
        try:
            idx, tag = raw_pref.split(":", 1)
            result = get_model(int(idx), tag)
        except (ValueError, TypeError):
            result = None
        if result is not None:
            ep, m = result
            cfg = LLMConfig.load()
            allowed = cfg.allowed_models or []
            key = f"{ep.index}:{m.tag}"
            not_retired = m.retirement_status != "retired"
            is_superuser = bool(getattr(user, "is_superuser", False))
            # An empty allowlist allows no user choice, only the default.
            in_allowlist = key in allowed or is_superuser
            if not_retired and in_allowlist:
                user_pref = (ep.index, m.tag)
        if user_pref is None:
            # Had a preference but it's no longer valid — clean it up.
            replaced = True
            if user is not None:
                from toxtempass.utilities import update_prefs_atomic

                missing = object()
                update_prefs_atomic(
                    user,
                    lambda p: p.pop("llm_model", missing) is not missing,
                )

    if user_pref is not None:
        idx, tag = user_pref
        return get_llm_for_endpoint(idx, tag, temperature=temperature), "user", replaced

    # Fall back to admin default via get_llm()
    if get_registry():
        return get_llm(), "admin_default", replaced

    return get_llm(), "legacy", replaced


def run_health_check(prompt: str = "What is the capital of France? One word.") -> dict:
    """Ping every discovered Azure deployment with a trivial prompt.

    Returns a dict keyed by ``"endpoint_index:tag"`` with per-deployment results::

        {"1:KIMI": {"ok": True, "latency_ms": 1240, "error": None,
                    "response": "Paris", "checked_at": "2026-04-12T13:47:00+00:00"}}
    """
    import time
    from datetime import datetime, timezone

    from toxtempass.azure_registry import get_registry

    results: dict[str, dict] = {}
    for ep in get_registry():
        for m in ep.models:
            key = f"{ep.index}:{m.tag}"
            t0 = time.perf_counter()
            entry: dict = {
                "ok": False,
                "latency_ms": None,
                "error": None,
                "response": None,
                "model_id": m.model_id,
                "deployment": m.deployment_name,
                "api": m.api,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                llm = get_llm_for_endpoint(ep.index, m.tag, temperature=0)
                resp = llm.invoke(prompt)
                text = getattr(resp, "content", str(resp))
                if isinstance(text, list):
                    text = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in text
                    )
                entry["ok"] = True
                entry["response"] = text.strip()[:120]
                entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"[:400]
                entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            results[key] = entry
    return results


def _is_reasoning_model(model_id: str) -> bool:
    """Return True for reasoning models (o1/o3/o4/o5 and gpt-5 family).

    These models reject custom ``temperature`` values and only support the default (1).
    """
    m = (model_id or "").lower()
    return m.startswith(("o1", "o3", "o4", "o5")) or m.startswith("gpt-5")


@lru_cache(maxsize=32)
def get_llm_for_endpoint(endpoint_index: int, model_tag: str, temperature: float | int = 0):
    """Build a chat client for a specific Azure deployment.

    Dispatches by the ``api`` tag on the model:
      - ``openai``       → ``ChatOpenAI``  (generic OpenAI-compat passthrough)
      - ``azure-openai`` → ``AzureChatOpenAI``  (classic ``*.cognitiveservices.azure.com``)
      - ``foundry``      → ``AzureAIChatCompletionsModel``  (Foundry Models Inference API)
      - ``anthropic``    → ``ChatAnthropic``  (Foundry ``/anthropic/`` passthrough)
    """
    from toxtempass.azure_registry import get_model

    result = get_model(endpoint_index, model_tag)
    if result is None:
        raise ImproperlyConfigured(
            f"Azure model E{endpoint_index}:{model_tag} not found in registry."
        )
    ep, m = result

    # Resolve the temperature to send (if any). Precedence:
    #   1. An explicit per-model `temperature` tag wins — a number pins the value,
    #      and `temperature:none` means the model rejects the param (e.g. Claude
    #      Opus 4.x, which 400s on `temperature`) so we omit it entirely. This is
    #      per-model, not per-api: other Anthropic models (Haiku 4.5) keep it.
    #   2. Otherwise reasoning models (o1/o3/o4/gpt-5) are forced to the only
    #      value they accept (1).
    #   3. Otherwise the caller-supplied `temperature` argument is used.
    policy = m.temperature_policy
    if policy == "omit":
        temp_kwargs = {}
    elif isinstance(policy, (int, float)):
        temp_kwargs = {"temperature": policy}
    else:
        if _is_reasoning_model(m.model_id):
            temperature = 1
        temp_kwargs = {"temperature": temperature}

    logger.info(
        "LLM configured: model=%s, deployment=%s, endpoint=%s, api=%s, tags=%s",
        m.model_id, m.deployment_name, ep.endpoint, m.api, m.tags,
    )

    if m.api == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ImproperlyConfigured(
                "`langchain-anthropic` is not installed. "
                "Run `poetry add langchain-anthropic anthropic`."
            ) from exc
        # Anthropic SDK base_url must be the root that precedes /v1/messages.
        base = ep.endpoint.rstrip("/").removesuffix("/v1/messages").removesuffix("/v1")
        return ChatAnthropic(
            api_key=ep.api_key,
            base_url=base,
            model=m.deployment_name,
            timeout=120,
            # Set explicitly: langchain otherwise picks a default from the model
            # profile, and `model` here is our deployment name rather than a
            # canonical id, so an unrecognised one silently falls back to 4096.
            # Anthropic also counts max_tokens against the context window, so
            # this reserves input room as well as capping output.
            max_tokens=app_config.llm_max_output_tokens,
            **temp_kwargs,
        )

    if m.api == "azure-openai":
        from langchain_openai import AzureChatOpenAI
        if not ep.api_version:
            raise ImproperlyConfigured(
                f"E{endpoint_index} uses api=azure-openai but AZURE_E{endpoint_index}"
                "_API_VERSION is not set."
            )
        # AzureChatOpenAI wants the resource root (no /openai/... path).
        azure_endpoint = ep.endpoint.split("/openai/")[0].rstrip("/")
        # Pass `model` explicitly so the request BODY carries a model string.
        # Real Azure OpenAI ignores it (the deployment in the URL is
        # authoritative), but SGLang-backed Azure deployments (e.g. some
        # serverless models) validate body.model and reject `null`. Harmless for
        # genuine Azure deployments, required for the OpenAI-compatible backends.
        return AzureChatOpenAI(
            api_key=ep.api_key,
            azure_endpoint=azure_endpoint,
            api_version=ep.api_version,
            azure_deployment=m.deployment_name,
            model=m.model_id,
            timeout=120,
            max_tokens=app_config.llm_max_output_tokens,
            **temp_kwargs,
        )

    if m.api == "foundry":
        try:
            from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel
        except ImportError as exc:
            raise ImproperlyConfigured(
                "`langchain-azure-ai` is not installed. "
                "Run `poetry add langchain-azure-ai azure-ai-inference`."
            ) from exc
        # Foundry inference endpoint is the /models root (no /chat/completions path).
        endpoint = ep.endpoint.split("/chat/completions")[0]
        return AzureAIChatCompletionsModel(
            endpoint=endpoint,
            credential=ep.api_key,
            model=m.deployment_name,
            api_version=ep.api_version or None,
            max_tokens=app_config.llm_max_output_tokens,
            **temp_kwargs,
        )

    # Default: plain OpenAI-compat passthrough (api:openai).
    return ChatOpenAI(
        api_key=ep.api_key,
        base_url=ep.endpoint,
        model=m.deployment_name,
        timeout=120,
        max_tokens=app_config.llm_max_output_tokens,
        **temp_kwargs,
    )






class ImageMessage(BaseMessage):
    type: Literal["image"] = Field(default="image")
    content: str  # Base64-encoded image string
    filename: str
    mime_type: str | None = None

    @model_validator(mode="before")
    def validate_fields(cls, values: dict) -> dict:
        """Validate that content and filename are provided."""
        content = values.get("content")
        filename = values.get("filename")
        if not content:
            raise ValueError("Image content must be provided.")
        if not filename:
            raise ValueError("Filename must be provided.")
        return values

    def to_dict(self) -> dict:
        """Convert the message to a dictionary."""
        return {
            "type": self.type,
            "content": self.content,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageMessage":
        """Create an instance from a dictionary."""
        return cls(
            content=data["content"],
            filename=data["filename"],
            mime_type=data.get("mime_type"),
        )
