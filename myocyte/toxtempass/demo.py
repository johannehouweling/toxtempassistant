"""Utilities for seeding demo assays for new users."""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q, QuerySet

from toxtempass.models import Answer, Assay, Investigation, LLMStatus, Person, Study

logger = logging.getLogger("demo")

# An assay belongs to the demo when it is the template, a copy of it, or locked.
DEMO_ASSAY = Q(demo_lock=True) | Q(demo_template=True) | Q(demo_source__isnull=False)


def without_demo_investigations(
    investigations: QuerySet[Investigation],
    user: Person | None = None,
    keep_pk: int | None = None,
) -> QuerySet[Investigation]:
    """Leave out investigations that hold a demo, so no new work is added to them.

    The demo is a read-only example; studies or assays added to it would be mixed
    up with it. Staff and superusers still see it, to look after the demo template.
    ``keep_pk`` stays in: the investigation an edited object is in.
    """
    if user is not None and (user.is_staff or user.is_superuser):
        return investigations
    demo = Assay.objects.filter(DEMO_ASSAY).values("study__investigation")
    return investigations.filter(~Q(pk__in=demo) | Q(pk=keep_pk))


def seed_demo_assay_for_user(user:Person) -> Assay|None:
    """Clone the demo template assay for the given user if not already present."""
    if not user or not getattr(user, "pk", None):
        return None

    # A usable template must have a questionnaire (otherwise the seeded copy is
    # filtered out of the overview and never shown) and must not itself be a demo
    # copy (a copy-of-a-copy is the messy state that breaks seeding). Newest wins.
    template = (
        Assay.objects.filter(
            demo_template=True,
            question_set__isnull=False,
            demo_source__isnull=True,
        )
        .select_related("study__investigation", "question_set")
        .prefetch_related("answers__question")
        .order_by("-pk")
        .first()
    )
    if not template:
        # Distinguish "nothing flagged" from "flagged but unusable" so an operator
        # can see *why* no demo was seeded instead of silently getting nothing.
        flagged = list(
            Assay.objects.filter(demo_template=True).values_list("pk", flat=True)
        )
        if flagged:
            logger.warning(
                "Assay(s) %s are flagged demo_template=True but none is usable as a "
                "template (a template needs a question_set and must not itself be a "
                "demo copy). Skipping demo seeding.",
                flagged,
            )
        else:
            logger.info("No demo template assay configured; skipping seeding.")
        return None

    already_exists = Assay.objects.filter(
        demo_source=template, study__investigation__owner=user
    ).exists()
    if already_exists:
        return None

    with transaction.atomic():
        template_inv = template.study.investigation
        inv = Investigation.objects.create(
            owner=user,
            title=f"{template_inv.title} (Demo)",
            description=template_inv.description,
            public_release_date=template_inv.public_release_date,
        )
        study = Study.objects.create(
            investigation=inv,
            title=f"{template.study.title} (Demo)",
            description=template.study.description,
        )
        assay = Assay.objects.create(
            study=study,
            title=f"{template.title} (Demo)",
            description=template.description,
            question_set=template.question_set,
            status=LLMStatus.DONE,
            processing_log=template.processing_log,
            user_alerts=list(template.user_alerts or []),
            demo_lock=True,
            demo_source=template,
        )

        answers_to_create = []
        for answer in template.answers.all():
            answers_to_create.append(
                Answer(
                    assay=assay,
                    question=answer.question,
                    answer_text=answer.answer_text,
                    accepted=answer.accepted,
                    answer_documents=answer.answer_documents,
                )
            )
        Answer.objects.bulk_create(answers_to_create)

    logger.info("Seeded demo assay %s for user %s", assay.id, user.id)
    return assay
