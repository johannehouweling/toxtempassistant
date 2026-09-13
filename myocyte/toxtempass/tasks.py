import logging
import os
from collections.abc import Sequence

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django_q.tasks import async_task

from toxtempass import config, utilities
from toxtempass.models import Person

_LOG = logging.getLogger(__name__)


def send_email_task(
    *,
    to: Sequence[str],
    subject: str,
    # Option A: plain text
    body: str | None = None,
    # Option B: templates
    template_html: str | None = None,
    template_text: str | None = None,
    context: dict | None = None,
    attachments: list | None = None,
) -> None:
    """Send either a plain-text email (if `body` is provided) or a templated email.

    (if `template_html` or `template_text` is provided). Raises on failure so
    django-q2 will retry according to Q_CLUSTER settings.
    """
    if not body and not template_html and not template_text:
        raise ValueError(
            "Provide `body` for plain text or a template_* for templated email."
        )

    # A) Plain text only
    if body:
        msg = EmailMessage(
            subject=subject,
            body=body,
            to=list(to),
        )
    else:
        # B) Template path(s)
        ctx = context or {}
        text_body = render_to_string(template_text, ctx) if template_text else ""
        html_body = render_to_string(template_html, ctx) if template_html else None

        # choose best base: have text? use EmailMultiAlternatives for alt HTML;
        # else use HTML as base
        if text_body:
            msg = EmailMultiAlternatives(subject=subject, body=text_body, to=list(to))
            if html_body is not None:
                msg.attach_alternative(html_body, "text/html")
        else:
            # HTML only: still send as HTML
            msg = EmailMultiAlternatives(
                subject=subject, body=html_body or "", to=list(to)
            )
            msg.attach_alternative(html_body or "", "text/html")

    # Attachments (both modes)
    for att in attachments or ():
        if att.path is not None:
            msg.attach_file(str(att.path))
        elif att.filename and att.content is not None:
            msg.attach(
                att.filename, att.content, att.mimetype or "application/octet-stream"
            )

    msg.send()


def on_email_done(task: any) -> None:
    """Hooks to execute after email is sent, e.g., for logging."""
    # task.success, task.result, task.attempt_count, task.func, task.args/kwargs
    # Log/metrics here (e.g., Sentry, DB row, print)
    pass


def queue_email(
    *,
    to: Sequence[str],
    subject: str,
    body: str | None = None,
    template_html: str | None = None,
    template_text: str | None = None,
    context: dict | None = None,
    attachments: list | None = None,
    group: str = "emails",
) -> str:
    """Keyword-only wrapper. Returns django-q2 task id."""
    task_id = async_task(
        "toxtempass.tasks.send_email_task",
        to=to,
        subject=subject,
        body=body,
        template_html=template_html,
        template_text=template_text,
        context=context,
        attachments=attachments,
        hook=on_email_done,
        group=group,
    )
    return str(task_id)


def queue_ror_lookup(person_pk: int) -> str:
    """Queue matching a Person's organization against ROR. Returns django-q2 task id."""
    return str(async_task("toxtempass.ror.resolve_person", person_pk, group="ror"))


# --- Beta signup notification -------------------------------------------------




def send_beta_signup_notification(person_id: int) -> str:
    """Queue an email notifying the maintainer that `person_id` signed up for beta.

    The email contains a one-click approval link (signed token). This function
    schedules the email using the existing queue_email helper and returns the
    django-q task id.

    Notes:
    - It will attempt to build an absolute URL using the SITE_URL environment
      variable (or settings.SITE_URL if present). If not found, a relative URL
      is used and a warning is logged.
    - The template paths used are:
        toxtempass/email/beta_signup_email.txt
        toxtempass/email/beta_signup_email.html  (optional)

    """
    try:
        person = Person.objects.get(pk=person_id)
    except Person.DoesNotExist:
        _LOG.error("send_beta_signup_notification: Person %s does not exist", person_id)
        raise

    # Generate token and approval path
    token = utilities.generate_beta_token(person_id)
    approve_path = reverse("approve_beta", args=[token])

    # Prefer explicit SITE_URL from environment or settings; fall back to relative path
    site_root = os.getenv("SITE_URL") or getattr(settings, "SITE_URL", "")
    if site_root:
        # Ensure trailing slash handling
        if site_root.endswith("/"):
            site_root = site_root[:-1]
        approve_url = f"{site_root}{approve_path}"
    else:
        approve_url = approve_path
        _LOG.warning(
            "SITE_URL not set; sending relative approve link for person %s: %s",
            person_id,
            approve_path,
        )

    # Recipient: maintainer defined in toxtempass.config
    recipient_email = getattr(config, "maintainer_email", None)
    if not recipient_email:
        _LOG.error("No maintainer email configured; cannot send beta notification.")

    context = {
        "person": person,
        "approve_url": approve_url,
    }

    subject = (
        f"[ToxTempAssistant Beta signup] {person.email or person.username} "
        "requested beta access"
    )
    task_id = queue_email(
        to=[recipient_email],
        subject=subject,
        template_text="toxtempass/email/beta_signup_email.txt",
        template_html="toxtempass/email/beta_signup_email.html",
        context=context,
        group="emails",
    )
    _LOG.info(
        "Queued beta signup notification for person %s to %s (task %s)",
        person_id,
        recipient_email,
        task_id,
    )
    return task_id

