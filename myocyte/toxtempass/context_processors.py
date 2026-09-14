import logging
from typing import Dict

from .workspace import get_workspace_list
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject
from toxtempass import config

logger = logging.getLogger(__name__)


def toxtempass_config(request) -> dict:
    """Expose the app-level config object to templates as 'config'."""
    return {"config": config}


def current_url_name(request) -> dict:
    """Expose the current resolved URL name as 'current_url_name'."""
    name = ""
    try:
        name = request.resolver_match.url_name or ""
    except Exception:
        name = ""
    return {"current_url_name": name}


def workspaces(request) -> Dict[str, object]:
    """Add workspace lists to every template so the offcanvas can render on any page.

    This calls the existing get_workspace_list view helper which returns a dict with
    owned_workspaces, member_workspaces and accessible_investigations. Using a
    context processor avoids having to call this from every view that renders the
    offcanvas.
    """
    # Provide the three expected context keys but keep evaluation lazy so
    # DB access only happens when the template actually uses them.
    def _owned_workspaces():
        try:
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return []
            return get_workspace_list(request).get("owned_workspaces", [])
        except Exception:
            return []

    def _member_workspaces():
        try:
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return []
            return get_workspace_list(request).get("member_workspaces", [])
        except Exception:
            return []

    def _accessible_investigations():
        try:
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return []
            return get_workspace_list(request).get("accessible_investigations", [])
        except Exception:
            return []

    return {
        "owned_workspaces": SimpleLazyObject(_owned_workspaces),
        "member_workspaces": SimpleLazyObject(_member_workspaces),
        "accessible_investigations": SimpleLazyObject(_accessible_investigations),
    }


def llm_info(request) -> dict:
    """Expose LLM selector + signature context for the offcanvas.

    * ``llm_signature`` — dict with icon/model_id/provider/version/privacy for the
      currently-resolved deployment, or ``None`` if nothing is configured.
    * ``llm_choices``   — ``(value, label)`` list the user may pick from.
    * ``llm_current``   — the user's persisted preference (empty if none).
    """
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {"llm_signature": None, "llm_choices": [], "llm_current": ""}

    try:
        from toxtempass.azure_registry import (
            badge_color,
            badge_icon,
            badge_short,
            find_by_model_id,
            get_model,
            get_registry,
        )
        from toxtempass.models import LLMConfig
    except Exception as exc:
        logger.debug("llm_info context unavailable: %s", exc)
        return {"llm_signature": None, "llm_choices": [], "llm_current": ""}

    try:
        cfg = LLMConfig.load()
    except Exception:
        cfg = None

    # Users may only pick models the admin ticked; with none ticked there is no choice
    # and everyone gets the default. Superusers also see the other models, marked
    # "(admin-only)". Labels are just the model_id (no privacy/version cruft).
    allowed = set(cfg.allowed_models or []) if cfg else set()
    is_superuser = bool(getattr(user, "is_superuser", False))
    choices = []
    for ep in get_registry():
        for m in ep.models:
            if m.retirement_status == "retired":
                continue
            key = f"{ep.index}:{m.tag}"
            in_allowed = key in allowed
            if not in_allowed and not is_superuser:
                continue
            label = m.model_id if in_allowed else f"{m.model_id} (admin-only)"
            choices.append((key, label))

    current = ""
    prefs = getattr(user, "preferences", None) or {}
    if isinstance(prefs, dict):
        current = prefs.get("llm_model", "") or ""

    # Resolve the effective deployment (user pref > admin default).
    effective_key = current or (cfg.default_model if cfg else "")
    resolved = None
    if effective_key and ":" in effective_key:
        try:
            idx_s, tag = effective_key.split(":", 1)
            resolved = get_model(int(idx_s), tag)
        except (ValueError, TypeError):
            resolved = None
    if resolved is None:
        # Legacy path (OpenAI env creds, no Azure registry)
        try:
            resolved = find_by_model_id(config.model)
        except Exception:
            resolved = None

    signature = None
    if resolved is not None:
        ep, m = resolved
        direct = (m.tags.get("direct-from-azure") or "").lower() == "true"
        # Who built the model vs. who hosts/serves it.
        model_by = (m.tags.get("provider") or "").title()
        if direct:
            hosted_on = "Azure (direct)"
        else:
            # Azure hosts the infra, but the model is served through a
            # third-party MaaS passthrough (e.g. Anthropic on Foundry).
            hosted_on = f"Azure (MaaS via {model_by or 'third-party'})"
        signature = {
            "icon": badge_icon(m.badge),
            "color": badge_color(m.badge),
            "privacy_short": badge_short(m.badge),
            "model_id": m.model_id,
            "deployment_name": m.deployment_name,
            "model_by": model_by,
            "hosted_on": hosted_on,
            "version": m.tags.get("version", ""),
            "api": m.api,
            "endpoint_index": ep.index,
            "tag": m.tag,
            "retirement_date": (
                m.retirement_date.isoformat() if m.retirement_date else ""
            ),
            "retirement_status": m.retirement_status,
            "context_window": m.context_window,
            "source": "user" if current else "admin_default",
        }

    return {
        "llm_signature": signature,
        "llm_choices": choices,
        "llm_current": current,
    }


def email_preferences(request: HttpRequest) -> dict:
    """Expose the email notifications the user can switch off, for the offcanvas."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {"email_settings": []}
    from toxtempass.notifications import email_settings_for

    return {
        "email_settings": email_settings_for(user),
        # Shown in the banner asking unconfirmed users to confirm their address.
        "unconfirmed_account_delete_days": config._unconfirmed_account_delete_days,
        # Shown in the Privacy tab's explanation of "Stop sharing".
        "file_withdrawal_grace_hours": config._file_withdrawal_grace_hours,
        "backup_retention_days": config._backup_retention_days,
    }
