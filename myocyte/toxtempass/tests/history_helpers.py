"""Helpers for tests that need saves to have happened at different times."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

from django.db.models import F
from simple_history.models import HistoricalRecords

from toxtempass.models import Answer, Assay, Person


def age_history(assay: Assay, seconds: float) -> None:
    """Move everything saved so far for ``assay`` ``seconds`` into the past.

    Whatever is saved afterwards is then that much later than all of it, which is
    how a test makes two saves belong to different versions (or to the same one).
    """
    shift = timedelta(seconds=seconds)
    Answer.history.model.objects.filter(assay_id=assay.pk).update(
        history_date=F("history_date") - shift
    )
    Assay.history.model.objects.filter(id=assay.pk).update(
        history_date=F("history_date") - shift
    )


@contextmanager
def as_user(person: Person):
    """Make every save inside the block count as ``person``'s, as in a request.

    A request saves a whole page of answers as one user, creating them as well as
    changing them; this is how ``HistoryRequestMiddleware`` attributes them.
    """
    context = HistoricalRecords.context
    before = getattr(context, "request", None)
    context.request = SimpleNamespace(user=person)
    try:
        yield
    finally:
        if before is None:
            del context.request
        else:
            context.request = before
