"""Every email the app sends goes through this module.

An email starts life as an ``EmailLog`` row, which is both the outbox and the
audit trail. Emails a user is waiting for (email confirmation, password changed,
beta approved) are sent as soon as the triggering transaction commits, from the
same process: the task queue has a single worker, and an LLM draft can occupy it
for a long time. Delayed emails wait for :func:`run_email_jobs`, which the task
queue runs every couple of minutes (via ``toxtempass.jobs``) and which also
queues the maintainer digest and alerts and deletes stale signups.

Spam controls, in the order they apply:

* ``dedup_key``: one key per logical email, so a double click, a retry or a job
  that runs twice cannot send it twice;
* opt-out: users can switch off the kinds marked ``optional`` (emails caused by
  someone else's action), and those emails carry a one-click unsubscribe link;
* a cap on emails to one user per rolling day, whatever triggered them;
* cool-off: workspace emails wait, so a change that is quickly undone sends
  nothing and several changes arrive as one email.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django_q.models import Failure

from toxtempass import config, privacy, utilities
from toxtempass.models import (
    EmailLog,
    LLMRun,
    Person,
    WorkspaceMember,
)

logger = logging.getLogger(__name__)

EMAIL_CONFIRMATION = "email_confirmation"
BETA_APPROVED = "beta_approved"
PASSWORD_CHANGED = "password_changed"  # noqa: S105 - an email kind, not a secret
WORKSPACE_ADDED = "workspace_added"
WORKSPACE_ACCESS_LOST = "workspace_access_lost"
MAINTAINER_BETA_DIGEST = "maintainer_beta_digest"
MAINTAINER_COST_ALERT = "maintainer_cost_alert"
MAINTAINER_FAILURE_ALERT = "maintainer_failure_alert"
EMAIL_CHANGE_CONFIRMATION = "email_change_confirmation"
EMAIL_CHANGE_REQUESTED = "email_change_requested"
ACCOUNT_DELETED = "account_deleted"

# Why a member lost access to a workspace, for WORKSPACE_ACCESS_LOST.
REASON_REMOVED = "removed"
REASON_DELETED = "deleted"

_OPT_OUT_PREF = "email_opt_out"
_CONFIRMATION_ATTEMPTS_PREF = "confirmation_email_attempts"


@dataclass(frozen=True)
class EmailKind:
    """How one kind of email is addressed and controlled."""

    key: str
    # Template name without extension; a .txt and an .html version must exist.
    template: str
    # Users may switch it off. Only for emails caused by someone else's action.
    optional: bool = False
    # Sent to DJANGO_ADMINS instead of a user.
    maintainer: bool = False
    # Pending emails of this kind for the same user are sent as one.
    grouped: bool = False
    # Label of the switch in the user menu (optional kinds only).
    label: str = ""
    # Payload key holding the address to send to, for emails that must not go to
    # the account's current address (a new address, or a deleted account).
    to_payload: str = ""


KINDS: dict[str, EmailKind] = {
    kind.key: kind
    for kind in (
        EmailKind(EMAIL_CONFIRMATION, "toxtempass/email/email_confirmation"),
        EmailKind(BETA_APPROVED, "toxtempass/email/beta_approved_email"),
        EmailKind(PASSWORD_CHANGED, "toxtempass/email/password_changed"),
        EmailKind(
            EMAIL_CHANGE_CONFIRMATION,
            "toxtempass/email/email_change_confirmation",
            to_payload="new_email",
        ),
        EmailKind(EMAIL_CHANGE_REQUESTED, "toxtempass/email/email_change_requested"),
        EmailKind(ACCOUNT_DELETED, "toxtempass/email/account_deleted", to_payload="to"),
        EmailKind(
            WORKSPACE_ADDED,
            "toxtempass/email/workspace_added",
            optional=True,
            grouped=True,
            label="Someone adds me to a workspace",
        ),
        EmailKind(
            WORKSPACE_ACCESS_LOST,
            "toxtempass/email/workspace_access_lost",
            optional=True,
            grouped=True,
            label="I am removed from a workspace, or it is deleted",
        ),
        EmailKind(
            MAINTAINER_BETA_DIGEST,
            "toxtempass/email/maintainer_beta_digest",
            maintainer=True,
        ),
        EmailKind(
            MAINTAINER_COST_ALERT,
            "toxtempass/email/maintainer_cost_alert",
            maintainer=True,
        ),
        EmailKind(
            MAINTAINER_FAILURE_ALERT,
            "toxtempass/email/maintainer_failure_alert",
            maintainer=True,
        ),
    )
}
OPTIONAL_KINDS: tuple[str, ...] = tuple(k.key for k in KINDS.values() if k.optional)


@dataclass
class _Built:
    """What a builder returns for an email that should go out."""

    subject: str
    context: dict
    # Rows of a group that were left out of the email, with the reason.
    skipped: dict[int, str] = field(default_factory=dict)
    # Runs once the mail server has accepted the email.
    on_sent: Callable[[], None] | None = None


# ── User preferences ──────────────────────────────────────────────────────────


def is_email_enabled(user: Person, kind: str) -> bool:
    """Return whether ``user`` wants emails of ``kind``; required kinds always do."""
    if not KINDS[kind].optional:
        return True
    return kind not in (user.preferences or {}).get(_OPT_OUT_PREF, [])


def set_email_enabled(user: Person, kind: str, enabled: bool) -> None:
    """Switch an optional kind of email on or off for ``user``."""
    if not KINDS[kind].optional:
        raise ValueError(f"{kind} emails cannot be switched off")

    def mutate(prefs: dict) -> bool:
        opted_out = set(prefs.get(_OPT_OUT_PREF, []))
        changed = (kind in opted_out) == enabled
        if enabled:
            opted_out.discard(kind)
        else:
            opted_out.add(kind)
        prefs[_OPT_OUT_PREF] = sorted(opted_out)
        return changed

    utilities.update_prefs_atomic(user, mutate)


def email_settings_for(user: Person) -> list[dict]:
    """Return the kinds ``user`` can switch off, with their current state."""
    return [
        {
            "kind": kind.key,
            "label": kind.label,
            "enabled": is_email_enabled(user, kind.key),
        }
        for kind in KINDS.values()
        if kind.optional
    ]


# ── Queueing and sending ──────────────────────────────────────────────────────


def queue_email(
    kind: str,
    *,
    user: Person | None = None,
    payload: dict | None = None,
    dedup_key: str = "",
    send_after: datetime | None = None,
) -> EmailLog | None:
    """Record an email and send it now, or once ``send_after`` has passed.

    Without ``send_after`` the email is sent right after the current transaction
    commits, in this process. Returns None when ``dedup_key`` was used before;
    otherwise the new row, which is already marked skipped if the user switched
    this kind off or reached the daily cap.
    """
    spec = KINDS[kind]
    payload = payload or {}
    if spec.maintainer:
        recipient = ", ".join(settings.ADMINS)
    elif spec.to_payload:
        recipient = payload[spec.to_payload]
    elif user is None:
        raise ValueError(f"{kind} emails need a user")
    else:
        recipient = user.email
    log = EmailLog(
        kind=kind,
        user=user,
        recipient=recipient,
        payload=payload,
        dedup_key=dedup_key,
        send_after=send_after,
    )
    skip_reason = _skip_reason(spec, user)
    if skip_reason:
        log.status = EmailLog.Status.SKIPPED
        log.error = skip_reason
    try:
        with transaction.atomic():
            log.save()
    except IntegrityError:
        if dedup_key and EmailLog.objects.filter(dedup_key=dedup_key).exists():
            # No kind or key in the message: CodeQL reads the "password_changed"
            # kind as a password. The earlier EmailLog row shows what was sent.
            logger.info("Not sending an email again: its dedup key was used before")
            return None
        raise
    if log.status == EmailLog.Status.PENDING and send_after is None:
        log_id = log.pk
        transaction.on_commit(lambda: deliver([log_id]))
    return log


def _skip_reason(spec: EmailKind, user: Person | None) -> str:
    """Return why an email should not be sent at all, or an empty string."""
    if spec.maintainer:
        return "" if settings.ADMINS else "DJANGO_ADMINS is not set"
    if user is None:  # only for kinds addressed through the payload
        return ""
    if not user.email:
        return "The account has no email address"
    if not is_email_enabled(user, spec.key):
        return "Switched off by the user"
    recent = (
        EmailLog.objects.filter(
            user=user, created_at__gte=timezone.now() - timedelta(days=1)
        )
        .exclude(status=EmailLog.Status.SKIPPED)
        .count()
    )
    if recent >= config._email_max_per_recipient_per_day:
        return "Daily limit of emails to this user reached"
    return ""


def deliver(log_ids: list[int]) -> bool:
    """Send one email built from the given pending rows. Returns whether it went out.

    Each row is claimed first, so a request and the scheduled job racing for the
    same row cannot both send it. Several rows only make sense for a grouped
    kind: the first included row becomes the sent email, the others are marked
    merged.
    """
    now = timezone.now()
    claimed = [
        pk
        for pk in log_ids
        if EmailLog.objects.filter(pk=pk, status=EmailLog.Status.PENDING).update(
            status=EmailLog.Status.SENDING, updated_at=now
        )
    ]
    if not claimed:
        return False
    logs = list(
        EmailLog.objects.filter(pk__in=claimed)
        .select_related("user")
        .order_by("created_at")
    )
    spec = KINDS.get(logs[0].kind)
    if spec is None:
        _mark(logs, EmailLog.Status.SKIPPED, f"Unknown email kind {logs[0].kind!r}")
        return False
    try:
        built = _BUILDERS[spec.key](logs)
    except Exception as exc:
        logger.exception("Building %s email %s failed", spec.key, logs[0].pk)
        _mark(logs, EmailLog.Status.FAILED, f"Could not build the email: {exc}")
        return False
    if isinstance(built, str):
        _mark(logs, EmailLog.Status.SKIPPED, built)
        return False
    for log in logs:
        if log.pk in built.skipped:
            _mark([log], EmailLog.Status.SKIPPED, built.skipped[log.pk])
    included = [log for log in logs if log.pk not in built.skipped]
    primary = included[0]
    recipients = _recipients(spec, primary)
    if not recipients:
        _mark(included, EmailLog.Status.SKIPPED, "No recipient address")
        return False

    subject = _one_line(f"{settings.EMAIL_SUBJECT_PREFIX}{built.subject}")[:255]
    try:
        _render(spec, primary.user, subject, built.context, recipients).send()
    except Exception as exc:
        logger.exception("Sending %s email %s failed", spec.key, primary.pk)
        _retry_or_fail(included, exc)
        return False

    sent_at = timezone.now()
    sent = {
        "subject": subject,
        "recipient": ", ".join(recipients)[:1000],
        "attempts": F("attempts") + 1,
        "sent_at": sent_at,
        "updated_at": sent_at,
    }
    EmailLog.objects.filter(pk=primary.pk).update(
        status=EmailLog.Status.SENT, error="", **sent
    )
    EmailLog.objects.filter(pk__in=[log.pk for log in included[1:]]).update(
        status=EmailLog.Status.MERGED, error=f"Sent as email {primary.pk}", **sent
    )
    if built.on_sent is not None:
        built.on_sent()
    return True


def _recipients(spec: EmailKind, log: EmailLog) -> list[str]:
    """Return the addresses to send to, as they are now."""
    if spec.maintainer:
        return list(settings.ADMINS)
    if spec.to_payload:
        address = log.payload.get(spec.to_payload, "")
        return [address] if address else []
    if log.user is None or not log.user.email:
        return []
    return [log.user.email]


def _render(
    spec: EmailKind,
    user: Person | None,
    subject: str,
    context: dict,
    recipients: list[str],
) -> EmailMultiAlternatives:
    """Render both versions of an email, with an unsubscribe link where allowed."""
    context = {**context, "user": user, "site_url": settings.SITE_URL}
    headers = {}
    if spec.optional and user is not None:
        token = utilities.generate_unsubscribe_token(user, spec.key)
        unsubscribe_url = utilities.absolute_url(reverse("unsubscribe", args=[token]))
        context["unsubscribe_url"] = unsubscribe_url
        context["kind_label"] = spec.label
        # RFC 8058 one-click unsubscribe: mail clients POST to this URL.
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"{spec.template}.txt", context),
        to=recipients,
        headers=headers,
    )
    message.attach_alternative(
        render_to_string(f"{spec.template}.html", context), "text/html"
    )
    return message


def _mark(logs: list[EmailLog], status: str, reason: str) -> None:
    """Set a final status on rows, with the reason."""
    EmailLog.objects.filter(pk__in=[log.pk for log in logs]).update(
        status=status, error=reason[:2000], updated_at=timezone.now()
    )


def _retry_or_fail(logs: list[EmailLog], exc: Exception) -> None:
    """Reschedule failed rows after the next retry delay, or mark them failed."""
    delays = config._email_retry_delays_minutes
    now = timezone.now()
    for log in logs:
        attempts = log.attempts + 1
        update = {"attempts": attempts, "error": str(exc)[:2000], "updated_at": now}
        if attempts <= len(delays):
            update["status"] = EmailLog.Status.PENDING
            update["send_after"] = now + timedelta(minutes=delays[attempts - 1])
        else:
            update["status"] = EmailLog.Status.FAILED
        EmailLog.objects.filter(pk=log.pk).update(**update)


def _one_line(text: str) -> str:
    """Collapse whitespace, so user-entered names cannot break a header."""
    return " ".join(str(text).split())


def _display_name(person: Person | None) -> str:
    """Return a person's full name, or their address when they have no name."""
    if person is None:
        return ""
    return _one_line(person.get_full_name()) or person.email


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from preferences; naive values are UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("UTC"))


# ── Builders: turn pending rows into subject and context ──────────────────────
# A builder returns a _Built, or a string saying why nothing should be sent.


def _build_email_confirmation(logs: list[EmailLog]) -> _Built | str:
    """Build the confirmation email, with a link valid for a few days."""
    user = logs[0].user
    if user is None:
        return "The account no longer exists"
    if user.email_confirmed_at is not None:
        return "The address is already confirmed"
    token = utilities.generate_email_confirmation_token(user)
    return _Built(
        subject="Confirm your email address",
        context={
            "confirm_url": utilities.absolute_url(reverse("confirm_email", args=[token])),
            "valid_days": config._email_confirmation_valid_days,
            "existing_account": bool(logs[0].payload.get("existing_account")),
            "delete_days": (
                config._unconfirmed_account_delete_days
                if user.delete_if_unconfirmed
                else None
            ),
        },
    )


def _build_beta_approved(logs: list[EmailLog]) -> _Built | str:
    """Build the email telling a user they were admitted to the beta."""
    user = logs[0].user
    if user is None:
        return "The account no longer exists"
    if not (user.preferences or {}).get("beta_admitted"):
        return "Beta access was revoked before the email went out"
    return _Built(
        subject="Your beta access is approved",
        context={
            "login_url": utilities.absolute_url(reverse("login")),
            "needs_confirmation": not user.has_confirmed_email,
        },
    )


def _build_password_changed(logs: list[EmailLog]) -> _Built | str:
    """Build the security notice sent after a password change."""
    if logs[0].user is None:
        return "The account no longer exists"
    return _Built(
        subject="Your password was changed",
        context={
            "changed_at": logs[0].created_at,
            "reset_url": utilities.absolute_url(reverse("password_reset")),
        },
    )


def _build_email_change_confirmation(logs: list[EmailLog]) -> _Built | str:
    """Build the link that confirms a new address, sent to that new address."""
    user = logs[0].user
    new_email = logs[0].payload.get("new_email", "")
    if user is None:
        return "The account no longer exists"
    if user.pending_email != new_email:
        return "The change was cancelled or replaced by a newer request"
    token = utilities.generate_email_change_token(user)
    return _Built(
        subject="Confirm your new email address",
        context={
            "new_email": new_email,
            "old_email": user.email,
            "confirm_url": utilities.absolute_url(
                reverse("account_confirm_email_change", args=[token])
            ),
            "valid_days": config._email_confirmation_valid_days,
        },
    )


def _build_email_change_requested(logs: list[EmailLog]) -> _Built | str:
    """Build the notice to the current address that a change was requested."""
    if logs[0].user is None:
        return "The account no longer exists"
    return _Built(
        subject="Your email address is about to change",
        context={
            "new_email": logs[0].payload.get("new_email", ""),
            "reset_url": utilities.absolute_url(reverse("password_reset")),
        },
    )


def _build_account_deleted(logs: list[EmailLog]) -> _Built:
    """Build the receipt for a deleted account, sent to its former address."""
    payload = logs[0].payload
    return _Built(
        subject="Your account was deleted",
        context={
            "name": payload.get("name", ""),
            "email": payload.get("to", ""),
            "backup_retention_days": config._backup_retention_days,
            "maintainer_email": config.maintainer_email,
        },
    )


def _build_workspace_added(logs: list[EmailLog]) -> _Built | str:
    """Build one email for every workspace the user was added to and is still in."""
    user = logs[0].user
    if user is None:
        return "The account no longer exists"
    if not is_email_enabled(user, WORKSPACE_ADDED):
        return "Switched off by the user"
    memberships = {
        member.workspace_id: member
        for member in WorkspaceMember.objects.filter(
            user=user, workspace_id__in=[log.payload.get("workspace_id") for log in logs]
        ).select_related("workspace", "added_by")
    }
    skipped = {
        log.pk: "No longer a member of the workspace"
        for log in logs
        if log.payload.get("workspace_id") not in memberships
    }
    if len(skipped) == len(logs):
        return "No longer a member of the workspace"

    workspaces = [
        {
            "name": _one_line(member.workspace.name),
            "added_by": _display_name(member.added_by),
            "role": member.get_role_display(),
        }
        for member in memberships.values()
    ]
    if len(workspaces) == 1:
        adder = workspaces[0]["added_by"] or "Someone"
        subject = f"{adder} added you to the workspace “{workspaces[0]['name']}”"
    else:
        subject = f"You were added to {len(workspaces)} workspaces"
    member_ids = [member.pk for member in memberships.values()]

    def mark_notified() -> None:
        WorkspaceMember.objects.filter(pk__in=member_ids).update(
            notified_at=timezone.now()
        )

    return _Built(
        subject=subject,
        context={
            "workspaces": workspaces,
            "overview_url": utilities.absolute_url(reverse("overview")),
        },
        skipped=skipped,
        on_sent=mark_notified,
    )


def _build_workspace_access_lost(logs: list[EmailLog]) -> _Built | str:
    """Build one email for every workspace the user lost and has not rejoined."""
    user = logs[0].user
    if user is None:
        return "The account no longer exists"
    if not is_email_enabled(user, WORKSPACE_ACCESS_LOST):
        return "Switched off by the user"
    rejoined = set(
        WorkspaceMember.objects.filter(
            user=user, workspace_id__in=[log.payload.get("workspace_id") for log in logs]
        ).values_list("workspace_id", flat=True)
    )
    skipped = {
        log.pk: "A member of the workspace again"
        for log in logs
        if log.payload.get("workspace_id") in rejoined
    }
    if len(skipped) == len(logs):
        return "A member of the workspace again"

    workspaces = [
        {
            "name": _one_line(log.payload.get("workspace_name", "")),
            "deleted": log.payload.get("reason") == REASON_DELETED,
            "actor": log.payload.get("actor_name", ""),
        }
        for log in logs
        if log.pk not in skipped
    ]
    if len(workspaces) > 1:
        subject = f"You no longer have access to {len(workspaces)} workspaces"
    elif workspaces[0]["deleted"]:
        subject = f"The workspace “{workspaces[0]['name']}” was deleted"
    else:
        subject = f"You were removed from the workspace “{workspaces[0]['name']}”"
    return _Built(
        subject=subject,
        context={
            "workspaces": workspaces,
            "overview_url": utilities.absolute_url(reverse("overview")),
        },
        skipped=skipped,
    )


def _build_beta_digest(logs: list[EmailLog]) -> _Built | str:
    """Build the maintainers' list of beta requests, each with an approve link."""
    people = Person.objects.filter(pk__in=logs[0].payload.get("person_ids", []))
    pending = [p for p in people.order_by("date_joined") if _awaits_beta_approval(p)]
    if not pending:
        return "No requests are pending any more"
    requests = [
        {
            "name": _display_name(person),
            "email": person.email,
            "organization": _one_line(person.organization),
            "requested_at": _parse_iso(
                (person.preferences or {}).get("beta_requested_at")
            ),
            "approve_url": utilities.absolute_url(
                reverse("approve_beta", args=[utilities.generate_beta_token(person.pk)])
            ),
        }
        for person in pending
    ]
    if len(requests) == 1:
        subject = "1 beta request is waiting for approval"
    else:
        subject = f"{len(requests)} beta requests are waiting for approval"
    return _Built(
        subject=subject,
        context={
            "requests": requests,
            "manage_url": utilities.absolute_url(reverse("admin_beta_user_list")),
        },
    )


def _build_cost_alert(logs: list[EmailLog]) -> _Built:
    """Build the alert that today's LLM spend passed the limit."""
    payload = logs[0].payload
    return _Built(
        subject=f"LLM spend on {payload['date']} passed €{payload['limit']}",
        context={
            **payload,
            "runs_url": utilities.absolute_url(
                reverse("admin:toxtempass_llmrun_changelist")
            ),
        },
    )


def _build_failure_alert(logs: list[EmailLog]) -> _Built:
    """Build the batched alert about failed emails, drafts and background tasks."""
    payload = logs[0].payload
    total = payload["email_count"] + payload["run_count"] + payload["task_count"]
    plural = "s" if total != 1 else ""
    return _Built(
        subject=f"{total} background failure{plural} since {payload['since']}",
        context=payload,
    )


_BUILDERS: dict[str, Callable[[list[EmailLog]], _Built | str]] = {
    EMAIL_CONFIRMATION: _build_email_confirmation,
    BETA_APPROVED: _build_beta_approved,
    PASSWORD_CHANGED: _build_password_changed,
    EMAIL_CHANGE_CONFIRMATION: _build_email_change_confirmation,
    EMAIL_CHANGE_REQUESTED: _build_email_change_requested,
    ACCOUNT_DELETED: _build_account_deleted,
    WORKSPACE_ADDED: _build_workspace_added,
    WORKSPACE_ACCESS_LOST: _build_workspace_access_lost,
    MAINTAINER_BETA_DIGEST: _build_beta_digest,
    MAINTAINER_COST_ALERT: _build_cost_alert,
    MAINTAINER_FAILURE_ALERT: _build_failure_alert,
}


# ── Entry points for views, admin and commands ────────────────────────────────


def send_email_confirmation(user: Person) -> EmailLog | None:
    """Email ``user`` a confirmation link, counting it against their resend limit."""
    utilities.record_attempt(
        user, _CONFIRMATION_ATTEMPTS_PREF, config._pw_reset_max_stored
    )
    return queue_email(EMAIL_CONFIRMATION, user=user)


def confirmation_resend_wait_seconds(user: Person) -> float:
    """Return how long ``user`` must wait before another confirmation email."""
    return utilities.get_attempt_wait_seconds(
        user, _CONFIRMATION_ATTEMPTS_PREF, config._pw_reset_wait_periods
    )


def request_email_confirmation(user: Person) -> EmailLog | None:
    """Ask an account from before email confirmation existed to confirm its address.

    Sent at most once per account; see ``manage.py send_confirmation_requests``.
    """
    return queue_email(
        EMAIL_CONFIRMATION,
        user=user,
        payload={"existing_account": True},
        dedup_key=f"{EMAIL_CONFIRMATION}:request:{user.pk}",
    )


def request_email_change(user: Person) -> None:
    """Send the confirmation link to ``user.pending_email``; warn the current address."""
    payload = {"new_email": user.pending_email}
    queue_email(EMAIL_CHANGE_CONFIRMATION, user=user, payload=payload)
    queue_email(EMAIL_CHANGE_REQUESTED, user=user, payload=payload)


def send_account_deleted_receipt(email: str, name: str) -> None:
    """Confirm to a deleted account's former address that the deletion happened.

    Queued without a user, because the account is gone by the time it is sent.
    """
    queue_email(ACCOUNT_DELETED, payload={"to": email, "name": name})


def notify_member_added(member: WorkspaceMember, added_by: Person | None) -> None:
    """Queue the 'added to a workspace' email, to go out after the cool-off.

    Call it once the membership row is saved. Adding yourself sends nothing, and
    re-adding someone whose 'lost access' email is still waiting cancels that
    email instead of sending both.
    """
    now = timezone.now()
    if added_by is not None and added_by.pk == member.user_id:
        _mark_notified(member, now)
        return
    cancelled = EmailLog.objects.filter(
        kind=WORKSPACE_ACCESS_LOST,
        user_id=member.user_id,
        status=EmailLog.Status.PENDING,
        payload__workspace_id=member.workspace_id,
    ).update(
        status=EmailLog.Status.SKIPPED,
        error="Added back to the workspace during the cool-off",
        updated_at=now,
    )
    if cancelled:
        _mark_notified(member, now)
        return
    queue_email(
        WORKSPACE_ADDED,
        user=member.user,
        payload={"workspace_id": member.workspace_id},
        send_after=now + timedelta(minutes=config._email_cooloff_minutes),
    )


def notify_access_lost(
    member: WorkspaceMember, *, actor: Person | None, reason: str
) -> None:
    """Queue the 'you lost access' email for a membership that is about to go.

    Call it before the row is deleted. Leaving or deleting a workspace yourself
    sends nothing, and removing someone who was never told they were added only
    cancels that pending email.
    """
    now = timezone.now()
    cancelled = EmailLog.objects.filter(
        kind=WORKSPACE_ADDED,
        user_id=member.user_id,
        status=EmailLog.Status.PENDING,
        payload__workspace_id=member.workspace_id,
    ).update(
        status=EmailLog.Status.SKIPPED,
        error="Removed from the workspace during the cool-off",
        updated_at=now,
    )
    if cancelled or member.notified_at is None:
        return
    if actor is not None and actor.pk == member.user_id:
        return
    queue_email(
        WORKSPACE_ACCESS_LOST,
        user=member.user,
        payload={
            "workspace_id": member.workspace_id,
            "workspace_name": member.workspace.name,
            "reason": reason,
            "actor_name": _display_name(actor),
        },
        send_after=now + timedelta(minutes=config._email_cooloff_minutes),
    )


def _mark_notified(member: WorkspaceMember, now: datetime) -> None:
    """Record that there is nothing (more) to tell this member about joining."""
    WorkspaceMember.objects.filter(pk=member.pk).update(notified_at=now)
    member.notified_at = now


# ── Scheduled job ─────────────────────────────────────────────────────────────


def run_email_jobs(now: datetime | None = None) -> None:
    """Send due emails and run the periodic checks. The task queue calls this.

    Called every ``_periodic_jobs_interval_minutes`` by ``jobs.run_periodic_jobs``.
    Every step is safe to repeat, and a failing step does not stop the others.
    """
    now = now or timezone.now()
    steps = (
        _release_stuck_sends,
        _send_due_emails,
        _queue_beta_digest,
        _queue_cost_alert,
        _queue_failure_alert,
        _delete_stale_unconfirmed_accounts,
    )
    for step in steps:
        try:
            step(now)
        except Exception:
            logger.exception("Email job step %s failed", step.__name__)


def _release_stuck_sends(now: datetime) -> None:
    """Put back rows whose send was claimed but never finished (a crashed worker)."""
    stuck_before = now - timedelta(minutes=config._email_stuck_sending_minutes)
    EmailLog.objects.filter(
        status=EmailLog.Status.SENDING, updated_at__lt=stuck_before
    ).update(status=EmailLog.Status.PENDING, updated_at=now)


def _send_due_emails(now: datetime) -> None:
    """Send every pending email that is due, grouping what should go out together."""
    rows = EmailLog.objects.filter(status=EmailLog.Status.PENDING).order_by("created_at")
    groups: dict[tuple, list[dict]] = {}
    for row in rows.values("pk", "kind", "user_id", "send_after", "created_at"):
        spec = KINDS.get(row["kind"])
        grouped = spec is not None and spec.grouped
        key = (row["kind"], row["user_id"] if grouped else row["pk"])
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if _is_due(group, now):
            deliver([row["pk"] for row in group])


def _is_due(group: list[dict], now: datetime) -> bool:
    """Return whether a group of pending rows should be sent now.

    Rows without ``send_after`` are sent by the request that created them, so
    they are only picked up here if that has not happened within a minute. A
    group waits until its newest row is due (each change restarts the cool-off),
    but no longer than ``_email_group_max_wait_minutes``.
    """
    oldest = min(row["created_at"] for row in group)
    if any(row["send_after"] is None for row in group):
        return oldest <= now - timedelta(minutes=1)
    if max(row["send_after"] for row in group) <= now:
        return True
    return oldest <= now - timedelta(minutes=config._email_group_max_wait_minutes)


def _awaits_beta_approval(person: Person) -> bool:
    """Return whether a confirmed, non-staff person's beta request is still open."""
    prefs = person.preferences or {}
    return (
        bool(prefs.get("beta_signup"))
        and not prefs.get("beta_admitted")
        and person.email_confirmed_at is not None
        and not (person.is_staff or person.is_superuser)
    )


def _queue_beta_digest(now: datetime) -> None:
    """Once a day, from the digest hour, list the beta requests awaiting approval.

    Only requests confirmed before today's digest hour are included; later ones
    wait until tomorrow, so the digest never turns into one email per signup.
    """
    if not settings.ADMINS:
        return
    local_now = now.astimezone(ZoneInfo(config._email_timezone))
    if local_now.hour < config._beta_digest_hour:
        return
    dedup_key = f"{MAINTAINER_BETA_DIGEST}:{local_now.date().isoformat()}"
    if EmailLog.objects.filter(dedup_key=dedup_key).exists():
        return
    cutoff = local_now.replace(
        hour=config._beta_digest_hour, minute=0, second=0, microsecond=0
    )
    person_ids = []
    for person in Person.objects.filter(email_confirmed_at__lt=cutoff).order_by("pk"):
        requested_at = _parse_iso((person.preferences or {}).get("beta_requested_at"))
        if _awaits_beta_approval(person) and (
            requested_at is None or requested_at < cutoff
        ):
            person_ids.append(person.pk)
    if person_ids:
        queue_email(
            MAINTAINER_BETA_DIGEST,
            payload={"person_ids": person_ids},
            dedup_key=dedup_key,
        )


def _queue_cost_alert(now: datetime) -> None:
    """Alert maintainers once a day when today's LLM spend passes the limit.

    Runs priced in another currency than EUR are not counted.
    """
    if not settings.ADMINS:
        return
    local_now = now.astimezone(ZoneInfo(config._email_timezone))
    dedup_key = f"{MAINTAINER_COST_ALERT}:{local_now.date().isoformat()}"
    if EmailLog.objects.filter(dedup_key=dedup_key).exists():
        return
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = LLMRun.objects.filter(
        Q(cost_unit__iexact="eur") | Q(cost_unit=""),
        created_at__gte=day_start,
        created_at__lte=now,
        cost__isnull=False,
    )
    total = runs.aggregate(total=Sum("cost"))["total"] or Decimal(0)
    limit = Decimal(config._llm_daily_cost_alert_limit)
    if total <= limit:
        return

    def top(field_name: str) -> list[dict]:
        rows = (
            runs.values(field_name)
            .annotate(spend=Sum("cost"), runs=Count("pk"))
            .order_by("-spend")[:5]
        )
        return [
            {
                "name": row[field_name] or "-",
                "spend": f"{row['spend']:.2f}",
                "runs": row["runs"],
            }
            for row in rows
        ]

    queue_email(
        MAINTAINER_COST_ALERT,
        payload={
            "date": local_now.date().isoformat(),
            "total": f"{total:.2f}",
            "limit": f"{limit:.2f}",
            "run_count": runs.count(),
            "top_users": top("user__email"),
            "top_models": top("model_id"),
        },
        dedup_key=dedup_key,
    )


def _queue_failure_alert(now: datetime) -> None:
    """Tell maintainers about failed emails, drafts and background tasks, at most hourly.

    Covers what failed since the previous alert (or the last interval, the first
    time). A failure alert that itself fails is not reported in the next one.
    """
    if not settings.ADMINS:
        return
    interval = timedelta(minutes=config._failure_alert_interval_minutes)
    last = (
        EmailLog.objects.filter(kind=MAINTAINER_FAILURE_ALERT)
        .order_by("-created_at")
        .first()
    )
    # Each alert records the end of the window it covered; the next one starts there.
    last_until = None
    if last is not None:
        last_until = _parse_iso(last.payload.get("until")) or last.created_at
    if last_until is not None and now - last_until < interval:
        return
    since = last_until or now - interval

    emails = EmailLog.objects.filter(
        status=EmailLog.Status.FAILED, updated_at__gt=since, updated_at__lte=now
    ).exclude(kind=MAINTAINER_FAILURE_ALERT)
    runs = LLMRun.objects.filter(
        status=LLMRun.Status.ERROR, created_at__gt=since, created_at__lte=now
    )
    tasks = Failure.objects.filter(stopped__gt=since, stopped__lte=now)
    counts = {
        "email_count": emails.count(),
        "run_count": runs.count(),
        "task_count": tasks.count(),
    }
    if not any(counts.values()):
        return

    shown = 20
    local_since = since.astimezone(ZoneInfo(config._email_timezone))
    queue_email(
        MAINTAINER_FAILURE_ALERT,
        payload={
            **counts,
            "until": now.isoformat(),
            "since": local_since.strftime("%d %b %Y %H:%M"),
            "emails": [
                {"kind": log.kind, "recipient": log.recipient, "error": log.error[:300]}
                for log in emails.order_by("-updated_at")[:shown]
            ],
            "runs": [
                {
                    "assay_id": run.assay_id,
                    "user": run.user.email if run.user else "",
                    "model": run.model_key,
                    "error": run.error[:300],
                }
                for run in runs.select_related("user").order_by("-created_at")[:shown]
            ],
            "tasks": [
                {
                    "name": task.name,
                    "func": task.func,
                    "error": str(task.result or "")[-300:],
                }
                for task in tasks.order_by("-stopped")[:shown]
            ],
        },
        dedup_key=f"{MAINTAINER_FAILURE_ALERT}:{now.isoformat(timespec='minutes')}",
    )


def _delete_stale_unconfirmed_accounts(now: datetime) -> None:
    """Delete signups whose address is still unconfirmed after the grace period.

    Accounts from before email confirmation (``delete_if_unconfirmed`` off) and
    staff accounts are never deleted.
    """
    cutoff = now - timedelta(days=config._unconfirmed_account_delete_days)
    stale = Person.objects.filter(
        email_confirmed_at__isnull=True,
        delete_if_unconfirmed=True,
        is_staff=False,
        is_superuser=False,
        date_joined__lt=cutoff,
    )
    for person in stale:
        person_id = person.pk
        try:
            privacy.delete_account(person)
        except Exception:
            logger.exception("Could not delete unconfirmed account %s", person_id)
            continue
        logger.info(
            "Deleted account %s: email address unconfirmed after %s days",
            person_id,
            config._unconfirmed_account_delete_days,
        )
