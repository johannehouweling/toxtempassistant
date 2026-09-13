"""django-q2 task wrappers. Emails go through toxtempass/notifications.py instead."""

from django_q.tasks import async_task


def queue_ror_lookup(person_pk: int) -> str:
    """Queue matching a Person's organization against ROR. Returns django-q2 task id."""
    return str(async_task("toxtempass.ror.resolve_person", person_pk, group="ror"))
