"""Versions of a ToxTemp, read from the histories of its answers and of the assay.

The point of all of these: an earlier version must be what the ToxTemp really was at
that moment, and its id must be stable.
"""

import uuid

import pytest

from toxtempass import versions
from toxtempass.export import generate_json_from_assay
from toxtempass.models import AssayCost, LLMStatus
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    PersonFactory,
    QuestionFactory,
    QuestionSetFactory,
    SectionFactory,
    SubsectionFactory,
)
from toxtempass.tests.history_helpers import age_history, as_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def toxtemp():
    """A ToxTemp with two questions, nothing answered yet."""
    qset = QuestionSetFactory(label="versions")
    sub = SubsectionFactory(section=SectionFactory(question_set=qset))
    questions = [QuestionFactory(subsection=sub) for _ in range(2)]
    creator = PersonFactory()
    assay = AssayFactory(
        question_set=qset, description="asdf", title="HepG2", created_by=creator
    )
    return assay, questions, creator


def _answer_texts(document):
    return [
        q["answer"]["text"]
        for section in document["sections"]
        for sub in section["subsections"]
        for q in sub["questions"]
    ]


def _as_of(assay, version_id, **kwargs):
    version = versions.find(assay, uuid.UUID(version_id))
    assert version is not None
    return generate_json_from_assay(assay, version=version, **kwargs)


def test_every_saved_change_is_a_version_newest_first(toxtemp):
    assay, (q1, _), _ = toxtemp
    assert len(versions.versions(assay)) == 1  # the assay itself was created
    answer = AnswerFactory(assay=assay, question=q1, answer_text="one")
    answer.answer_text = "two"
    answer.save()
    assay.description = "a good description"
    assay.save()

    listed = versions.versions(assay)
    assert len(listed) == 4  # assay created, answer created, answer edited, assay edited
    assert [v.key for v in listed] == sorted((v.key for v in listed), reverse=True)
    assert listed[0].rank == versions.ASSAY_RANK  # the description was changed last


def test_a_save_that_changes_nothing_that_is_kept_adds_no_version(toxtemp):
    assay, _, _ = toxtemp
    before = len(versions.versions(assay))
    assay.status = LLMStatus.BUSY
    assay.processing_log = "[abc] something\n"
    assay.save()
    assay.save()
    assert len(versions.versions(assay)) == before


def test_ids_are_the_same_every_time_and_differ_between_versions(toxtemp):
    assay, (q1, q2), _ = toxtemp
    AnswerFactory(assay=assay, question=q1)
    AnswerFactory(assay=assay, question=q2)
    first = [v.id for v in versions.versions(assay)]
    second = [v.id for v in versions.versions(assay)]
    assert first == second
    assert len(set(first)) == len(first)
    assert all(isinstance(i, uuid.UUID) and i.version == 5 for i in first)
    # Another ToxTemp's versions never collide with these.
    other = AssayFactory()
    assert not {v.id for v in versions.versions(other)} & set(first)


def test_an_earlier_version_shows_the_answers_and_description_it_had(toxtemp):
    assay, (q1, _), _ = toxtemp
    answer = AnswerFactory(assay=assay, question=q1, answer_text="first")
    created = versions.latest_version(assay)
    answer.answer_text = "second"
    answer.save()
    edited = versions.latest_version(assay)
    assay.description = "a good description"
    assay.save()
    described = versions.latest_version(assay)

    at_creation = _as_of(assay, str(created.id))
    assert "first" in _answer_texts(at_creation)
    assert at_creation["assay"]["description"] == "asdf"
    at_edit = _as_of(assay, str(edited.id))
    assert "second" in _answer_texts(at_edit) and "first" not in _answer_texts(at_edit)
    assert at_edit["assay"]["description"] == "asdf"
    at_description = _as_of(assay, str(described.id))
    assert at_description["assay"]["description"] == "a good description"
    assert "second" in _answer_texts(at_description)

    now = generate_json_from_assay(assay)
    assert now["assay"]["description"] == "a good description"
    assert now["assay"]["version"] == str(described.id)


def test_a_version_is_exactly_what_the_document_was_then(toxtemp):
    """Take the document, change everything, rebuild it from its version id."""
    assay, (q1, q2), creator = toxtemp
    editor = PersonFactory()
    one = AnswerFactory(assay=assay, question=q1, answer_text="kept")
    two = AnswerFactory(assay=assay, question=q2, answer_text="draft")
    two.answer_text = "edited by a person"
    two._history_user = editor
    two.save()

    then = generate_json_from_assay(assay)

    one.answer_text = "rewritten"
    one.accepted = True
    one.save()
    two.delete()
    assay.title = "A new title"
    assay.description = "something else"
    assay.save()

    rebuilt = _as_of(assay, then["assay"]["version"])
    assert rebuilt["sections"] == then["sections"]
    for key in ("title", "description", "question_set", "last_modified", "version"):
        assert rebuilt["assay"][key] == then["assay"][key]
    assert rebuilt["metadata"]["authors"] == then["metadata"]["authors"]
    assert rebuilt["metadata"]["filename"] == then["metadata"]["filename"]
    assert rebuilt["history"][0]["id"] != then["history"][0]["id"]  # history is complete
    assert {h["id"] for h in then["history"]} <= {h["id"] for h in rebuilt["history"]}


def test_a_deleted_answer_is_gone_only_from_later_versions(toxtemp):
    assay, (q1, _), _ = toxtemp
    answer = AnswerFactory(assay=assay, question=q1, answer_text="soon gone")
    while_there = versions.latest_version(assay)
    answer.delete()
    after = versions.latest_version(assay)
    assert "soon gone" in _answer_texts(_as_of(assay, str(while_there.id)))
    assert "soon gone" not in _answer_texts(_as_of(assay, str(after.id)))


def test_authors_are_those_who_had_edited_by_then(toxtemp):
    assay, (q1, _), creator = toxtemp
    editor = PersonFactory()
    answer = AnswerFactory(assay=assay, question=q1, answer_text="a")
    before_edit = versions.latest_version(assay)
    answer.answer_text = "b"
    answer._history_user = editor
    answer.save()
    after_edit = versions.latest_version(assay)

    def names(document):
        return [a["name"] for a in document["metadata"]["authors"]]

    early = names(_as_of(assay, str(before_edit.id)))
    late = names(_as_of(assay, str(after_edit.id)))
    assert editor.get_full_name() not in early
    assert editor.get_full_name() in late
    assert creator.get_full_name() in early and creator.get_full_name() in late


def test_models_used_are_those_recorded_by_then(toxtemp):
    assay, (q1, _), _ = toxtemp
    answer = AnswerFactory(assay=assay, question=q1, answer_text="a")
    AssayCost.objects.create(assay=assay, model_key="1:EARLY", model_id="early-model")
    answer.answer_text = "b"
    answer.save()
    version = versions.latest_version(assay)
    AssayCost.objects.create(assay=assay, model_key="1:LATE", model_id="late-model")

    then = _as_of(assay, str(version.id))["metadata"]["models_used"]
    now = generate_json_from_assay(assay)["metadata"]["models_used"]
    assert [m["model_id"] for m in then] == ["early-model"]
    assert [m["model_id"] for m in now] == ["early-model", "late-model"]


def test_the_status_is_not_recorded_so_an_earlier_version_has_none(toxtemp):
    assay, (q1, _), _ = toxtemp
    AnswerFactory(assay=assay, question=q1)
    version = versions.latest_version(assay)
    assay.status = LLMStatus.DONE
    assay.completion_time_seconds = 600
    assay.save()
    now = generate_json_from_assay(assay)["assay"]
    then = _as_of(assay, str(version.id))["assay"]
    assert (now["status"], now["completion_time_seconds"]) == ("done", 600)
    assert (then["status"], then["completion_time_seconds"]) == (None, None)


def test_the_newest_version_is_the_same_with_or_without_the_list_annotations(toxtemp):
    from toxtempass.models import Assay

    assay, (q1, _), _ = toxtemp
    AnswerFactory(assay=assay, question=q1)
    plain = versions.latest_version(assay)
    annotated = versions.with_latest_versions(Assay.objects.filter(pk=assay.pk)).get()
    assert versions.latest_version(annotated) == plain == versions.versions(assay)[0]
    assert annotated.last_modified == plain.at


def test_last_modified_follows_a_change_of_the_description(toxtemp):
    from toxtempass.export import assay_last_modified

    assay, _, _ = toxtemp
    before = assay_last_modified(assay)
    assay.description = "a good description"
    assay.save()
    assert assay_last_modified(assay) > before


def test_a_toxtemp_with_no_history_has_no_version(toxtemp):
    """Assays from before the history began, and never saved since, have none."""
    from toxtempass.models import Assay

    assay, _, _ = toxtemp
    Assay.history.model.objects.filter(id=assay.pk).delete()
    document = generate_json_from_assay(assay)
    assert document["assay"]["version"] is None and document["history"] == []
    assert versions.latest_version(assay) is None


def test_an_earlier_version_still_builds_when_the_assay_history_is_missing(toxtemp):
    from toxtempass.models import Assay

    assay, (q1, _), _ = toxtemp
    AnswerFactory(assay=assay, question=q1, answer_text="kept")
    version = versions.latest_version(assay)
    Assay.history.model.objects.filter(id=assay.pk).delete()
    document = _as_of(assay, str(version.id))
    assert "kept" in _answer_texts(document)
    assert document["assay"]["title"] == assay.title  # the best that is known


def test_changes_saved_in_the_same_instant_keep_their_order(toxtemp):
    """Two rows can carry the same timestamp; the row id then says which came first."""
    from toxtempass.models import Answer, Assay

    assay, (q1, _), _ = toxtemp
    answer = AnswerFactory(assay=assay, question=q1, answer_text="first")
    answer.answer_text = "second"
    answer.save()
    assay.description = "a good description"
    assay.save()
    # Make every change of this ToxTemp happen at the same moment.
    moment = versions.versions(assay)[0].at
    Answer.history.model.objects.filter(assay_id=assay.pk).update(history_date=moment)
    Assay.history.model.objects.filter(id=assay.pk).update(history_date=moment)

    listed = sorted(versions.versions(assay), key=lambda v: v.key)
    kinds = [(v.rank, v.history_id) for v in listed]
    # The answer rows come before the assay rows, each group in the order saved.
    assert kinds == sorted(kinds)

    def text_and_description(version):
        document = _as_of(assay, str(version.id))
        return _answer_texts(document)[0], document["assay"]["description"]

    created, edited = (v for v in listed if v.rank == versions.ANSWER_RANK)
    assay_rows = [v for v in listed if v.rank == versions.ASSAY_RANK]
    assert text_and_description(created)[0] == "first"
    assert text_and_description(edited)[0] == "second"
    # An answer saved "at the same time" as the assay came first, so it is in the
    # assay's version; but the assay's later description is not in the answer's.
    assert text_and_description(edited)[1] == "asdf"
    assert text_and_description(assay_rows[-1]) == ("second", "a good description")


class TestSavesThatBelongTogether:
    """One action saves many rows; a person is shown one version for them."""

    def test_creating_every_answer_at_once_is_one_version(self):
        """Submitting the answers page creates (and saves) an answer per question."""
        qset = QuestionSetFactory(label="burst")
        sub = SubsectionFactory(section=SectionFactory(question_set=qset))
        assay = AssayFactory(question_set=qset, created_by=PersonFactory())
        person = PersonFactory()
        with as_user(person):
            for _ in range(130):
                answer = AnswerFactory(
                    assay=assay, question=QuestionFactory(subsection=sub)
                )
                answer.accepted = True  # a second save of the same answer
                answer.save()

        assert len(versions.versions(assay)) > 130  # every row is a saved state
        # The assay's own creation (by nobody here) and the page's submit.
        assert len(versions.history(assay)) == 2

    def test_saves_close_together_by_one_person_are_one_version(self, toxtemp):
        assay, (q1, q2), _ = toxtemp
        person = PersonFactory()
        age_history(assay, 3600)
        for question in (q1, q2):
            with as_user(person):
                AnswerFactory(assay=assay, question=question, answer_text="x")
            age_history(assay, 5)  # five seconds between saves, inside the gap
        shown = versions.history(assay)
        assert len(shown) == 2  # what the assay started with, and this edit session

    def test_a_pause_longer_than_the_gap_starts_another_version(self, toxtemp):
        from toxtempass import config

        assay, (q1, q2), _ = toxtemp
        person = PersonFactory()
        age_history(assay, 3600)
        with as_user(person):
            AnswerFactory(assay=assay, question=q1, answer_text="x")
        age_history(assay, config._version_gap_seconds + 1)
        with as_user(person):
            AnswerFactory(assay=assay, question=q2, answer_text="y")
        assert len(versions.history(assay)) == 3  # creation, the first save, the second

    def test_a_different_person_starts_another_version_at_once(self, toxtemp):
        assay, (q1, q2), _ = toxtemp
        age_history(assay, 3600)
        for question, person in ((q1, PersonFactory()), (q2, PersonFactory())):
            with as_user(person):
                AnswerFactory(assay=assay, question=question, answer_text="x")
        assert len(versions.history(assay)) == 3

    def test_a_drafting_run_with_no_person_is_one_version(self, toxtemp):
        """The drafting task has no request, so its saves carry no user."""
        assay, (q1, q2), _ = toxtemp
        age_history(assay, 3600)
        for question in (q1, q2):
            answer = AnswerFactory(assay=assay, question=question, answer_text="")
            answer.answer_text = "the model's draft"
            answer.save(update_fields=["answer_text"])
            age_history(assay, 10)  # answers finish seconds apart
        assert len(versions.history(assay)) == 2

    def test_a_person_editing_after_the_run_is_another_version(self, toxtemp):
        assay, (q1, _), _ = toxtemp
        age_history(assay, 3600)
        answer = AnswerFactory(assay=assay, question=q1, answer_text="draft")
        age_history(assay, 5)
        answer.answer_text = "edited by a person"
        answer._history_user = PersonFactory()
        answer.save()
        assert len(versions.history(assay)) == 3

    def test_a_version_is_the_state_after_the_last_save_of_its_burst(self, toxtemp):
        assay, (q1, q2), _ = toxtemp
        age_history(assay, 3600)
        with as_user(PersonFactory()):
            for question, text in ((q1, "first answer"), (q2, "second answer")):
                AnswerFactory(assay=assay, question=question, answer_text=text)
        newest = versions.history(assay)[0]
        document = _as_of(assay, str(newest.id))
        assert {"first answer", "second answer"} <= set(_answer_texts(document))
        assert generate_json_from_assay(assay)["assay"]["version"] == str(newest.id)

    def test_an_id_that_was_listed_keeps_working_when_later_saves_join_its_burst(
        self, toxtemp
    ):
        assay, (q1, q2), _ = toxtemp
        age_history(assay, 3600)
        person = PersonFactory()
        with as_user(person):
            AnswerFactory(assay=assay, question=q1, answer_text="one")
        seen_by_a_partner = str(versions.history(assay)[0].id)
        then = _as_of(assay, seen_by_a_partner)

        with as_user(person):
            AnswerFactory(assay=assay, question=q2, answer_text="two")  # same burst

        listed = [str(v.id) for v in versions.history(assay)]
        assert listed[0] != seen_by_a_partner  # the burst now ends later
        assert seen_by_a_partner not in listed
        again = _as_of(assay, seen_by_a_partner)  # but the old id still resolves
        assert again["sections"] == then["sections"]
        assert "two" not in _answer_texts(again) and "one" in _answer_texts(again)

    def test_the_newest_version_is_always_the_last_save(self, toxtemp):
        assay, (q1, _), _ = toxtemp
        AnswerFactory(assay=assay, question=q1)
        assert versions.history(assay)[0] == versions.versions(assay)[0]
        assert versions.history(assay)[0] == versions.latest_version(assay)

    def test_an_assay_with_no_saves_has_an_empty_history(self, toxtemp):
        from toxtempass.models import Assay

        assay, _, _ = toxtemp
        Assay.history.model.objects.filter(id=assay.pk).delete()
        assert versions.history(assay) == []
