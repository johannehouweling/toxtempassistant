import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from toxtempass.demo import seed_demo_assay_for_user
from toxtempass.models import (
    Answer,
    Assay,
    Investigation,
    Question,
    QuestionSet,
    Section,
    Study,
    Subsection,
)
from toxtempass.tests.fixtures.factories import PersonFactory


@pytest.mark.django_db
def test_seed_demo_assay_for_user_creates_copy_once():
    template_owner = PersonFactory()
    question_set = QuestionSet.objects.create(display_name="Demo QS", hide_from_display=False)
    section = Section.objects.create(question_set=question_set, title="Section")
    subsection = Subsection.objects.create(section=section, title="Subsection")
    question = Question.objects.create(subsection=subsection, question_text="Demo question?")

    inv = Investigation.objects.create(owner=template_owner, title="Template Investigation")
    study = Study.objects.create(investigation=inv, title="Template Study")
    template_assay = Assay.objects.create(
        study=study,
        title="Template Assay",
        description="",
        question_set=question_set,
        demo_lock=True,
        demo_template=True,
    )
    Answer.objects.create(
        assay=template_assay,
        question=question,
        answer_text="Demo answer",
        accepted=True,
        answer_documents=["demo.pdf"],
    )

    user = PersonFactory() # signal will call seed_demo_assay_for_user on creation
    
    demo_assay = Assay.objects.get(demo_lock=True,study__investigation__owner=user)
    assert demo_assay is not None
    assert demo_assay.demo_source == template_assay
    assert demo_assay.study.investigation.owner == user
    assert demo_assay.answers.count() == 1

    # Subsequent calls should not create duplicates
    second = seed_demo_assay_for_user(user)
    assert second is None
    assert (
        Assay.objects.filter(
            demo_source=template_assay, study__investigation__owner=user
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_seed_uses_newest_flagged_template():
    """If several assays are flagged demo_template, the newest (highest pk) wins."""
    owner = PersonFactory()
    question_set = QuestionSet.objects.create(
        display_name="Demo QS", hide_from_display=False
    )
    inv = Investigation.objects.create(owner=owner, title="Template Investigation")
    study = Study.objects.create(investigation=inv, title="Template Study")

    older_template = Assay.objects.create(
        study=study,
        title="Old Template",
        description="",
        question_set=question_set,
        demo_lock=True,
        demo_template=True,
    )
    newer_template = Assay.objects.create(
        study=study,
        title="New Template",
        description="",
        question_set=question_set,
        demo_lock=True,
        demo_template=True,
    )
    assert newer_template.pk > older_template.pk

    user = PersonFactory()  # signal seeds from the newest flagged template

    demo_assay = Assay.objects.get(demo_lock=True, study__investigation__owner=user)
    assert demo_assay.demo_source == newer_template


def _create_demo_template():
    """Create a demo template assay (with a question_set) ready for seeding."""
    owner = PersonFactory()
    question_set = QuestionSet.objects.create(
        display_name="Demo QS", hide_from_display=False
    )
    inv = Investigation.objects.create(owner=owner, title="Template Investigation")
    study = Study.objects.create(investigation=inv, title="Template Study")
    return Assay.objects.create(
        study=study,
        title="Template Assay",
        description="",
        question_set=question_set,
        demo_lock=True,
        demo_template=True,
    )


@pytest.mark.django_db
def test_new_user_sees_demo_in_overview(client):
    """A new user with no real assays sees the seeded demo on the overview page."""
    # Template must exist before the user is created (seeded by the post_save signal).
    _create_demo_template()

    user = PersonFactory()
    demo_assay = Assay.objects.get(demo_lock=True, study__investigation__owner=user)

    client.force_login(user)
    response = client.get(reverse("overview"))

    assert response.status_code == 200
    shown = list(response.context["object_list"])
    assert demo_assay in shown, "new user should see the demo assay in the overview"


@pytest.mark.django_db
def test_overview_keeps_demo_visible_when_real_assay_exists(client):
    """The demo stays visible alongside real work — it is never auto-hidden."""
    template = _create_demo_template()

    user = PersonFactory()
    demo_assay = Assay.objects.get(demo_lock=True, study__investigation__owner=user)

    # The user creates a real (non-demo) assay of their own.
    real_inv = Investigation.objects.create(owner=user, title="Real Investigation")
    real_study = Study.objects.create(investigation=real_inv, title="Real Study")
    real_assay = Assay.objects.create(
        study=real_study,
        title="Real Assay",
        description="",
        question_set=template.question_set,
        demo_lock=False,
    )

    client.force_login(user)
    response = client.get(reverse("overview"))

    assert response.status_code == 200
    shown = list(response.context["object_list"])
    assert real_assay in shown
    assert demo_assay in shown, "demo should remain visible alongside real work"


@pytest.mark.django_db
def test_user_can_delete_their_own_demo_copy(client):
    """A user may delete their own seeded demo copy (it is theirs to remove)."""
    _create_demo_template()
    user = PersonFactory()
    demo = Assay.objects.get(demo_lock=True, study__investigation__owner=user)

    client.force_login(user)
    response = client.post(reverse("delete_assay", kwargs={"pk": demo.pk}))

    assert response.status_code in (301, 302)  # redirect after a successful delete
    assert not Assay.objects.filter(pk=demo.pk).exists()


@pytest.mark.django_db
def test_seed_skips_template_without_question_set():
    """A demo_template without a question_set is unusable; nothing is seeded."""
    owner = PersonFactory()
    inv = Investigation.objects.create(owner=owner, title="Template Investigation")
    study = Study.objects.create(investigation=inv, title="Template Study")
    Assay.objects.create(
        study=study,
        title="Bad Template",
        description="",
        question_set=None,
        demo_lock=True,
        demo_template=True,
    )

    user = PersonFactory()  # seeding runs but finds no usable template

    assert not Assay.objects.filter(
        demo_lock=True, study__investigation__owner=user
    ).exists()


@pytest.mark.django_db
def test_seed_ignores_demo_copy_flagged_as_template():
    """A demo copy mis-flagged as a template is ignored in favour of a real one."""
    template = _create_demo_template()  # valid: has question_set, no demo_source

    first_user = PersonFactory()
    copy = Assay.objects.get(demo_lock=True, study__investigation__owner=first_user)
    copy.demo_template = True  # the messy state: a copy flagged as a template
    copy.save()

    second_user = PersonFactory()
    demo = Assay.objects.get(
        demo_lock=True, demo_template=False, study__investigation__owner=second_user
    )
    # Seeded from the real template, not from the copy-of-a-copy.
    assert demo.demo_source == template


@pytest.mark.django_db
def test_clean_rejects_demo_copy_as_template():
    """clean() forbids flagging a demo copy as the template."""
    _create_demo_template()
    user = PersonFactory()
    copy = Assay.objects.get(demo_lock=True, study__investigation__owner=user)
    copy.demo_template = True
    with pytest.raises(ValidationError):
        copy.clean()


@pytest.mark.django_db
def test_clean_rejects_template_without_question_set():
    """clean() forbids a template with no question_set (would be hidden)."""
    owner = PersonFactory()
    inv = Investigation.objects.create(owner=owner, title="Inv")
    study = Study.objects.create(investigation=inv, title="Study")
    assay = Assay.objects.create(
        study=study,
        title="No QS",
        description="",
        question_set=None,
    )
    assay.demo_template = True
    with pytest.raises(ValidationError):
        assay.clean()


@pytest.mark.django_db
def test_admin_can_hide_demos_and_non_admin_cannot(client):
    """?hide_demo hides demo assays for superusers only; for anyone else it is ignored."""
    _create_demo_template()
    admin = PersonFactory(is_superuser=True, is_staff=True)
    user = PersonFactory()
    admin_demo = Assay.objects.get(demo_lock=True, study__investigation__owner=admin)
    user_demo = Assay.objects.get(demo_lock=True, study__investigation__owner=user)

    client.force_login(admin)
    response = client.get(reverse("overview"))
    assert admin_demo in response.context["object_list"]
    assert "Hide demos" in response.content.decode()

    response = client.get(reverse("overview"), {"sort": "assay", "hide_demo": "1"})
    assert admin_demo not in response.context["object_list"]
    # The "Demos hidden" link keeps the active sort and drops hide_demo on the way back.
    assert 'href="?sort=assay"' in response.content.decode()

    client.force_login(user)
    response = client.get(reverse("overview"), {"hide_demo": "1"})
    assert user_demo in response.context["object_list"], "non-admins ignore ?hide_demo"
    assert "Hide demos" not in response.content.decode()
