"""Tests for recording contacts and observations, their permanence, and retraction."""

import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from axisence.providers.models import Competitor
from axisence.research import contacts
from axisence.research.call_sheet import ContactForm
from axisence.research.contacts import (
    ContactEntry,
    DefinitionReference,
    NewDefinition,
    ObservationEntry,
    RecordingError,
    RetractionError,
)
from axisence.research.models import (
    Contact,
    ContactMethod,
    DiscountKind,
    JobCompetitor,
    JobStatus,
    Observation,
    ObservationKind,
    PermanentRecordError,
    PricePeriod,
)
from tests.providers.factories import make_competitor, make_researcher
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_job,
    make_observation,
)

pytestmark = pytest.mark.django_db

NOTE_MARKER = "synthetic note marker 7f3a"


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def competitor():
    return make_competitor(display_name="Synthetic Day Care")


def _entry(competitor, **fields):
    values = {
        "competitor": competitor,
        "job": None,
        "contact_date": timezone.localdate(),
        "method": ContactMethod.CALL,
        "person_role": "",
        "notes": "",
        "new_definitions": (),
        "retired_definition_ids": frozenset(),
        "observations": (),
    }
    values.update(fields)
    return ContactEntry(**values)


def _weeks_open(weeks=50):
    return ObservationEntry(
        kind=ObservationKind.WEEKS_OPEN, values={"weeks_open_per_year": weeks}
    )


def test_recording_stamps_the_researcher_and_the_entry_time(competitor, researcher):
    before = timezone.now()

    contact = contacts.record_contact(
        entry=_entry(competitor, observations=(_weeks_open(),)), researcher=researcher
    )

    contact.refresh_from_db()
    assert contact.researcher == researcher
    assert before <= contact.entered_at <= timezone.now()
    assert contact.observations.get().weeks_open_per_year == 50


def test_contact_dates_are_never_in_the_future(competitor, researcher):
    tomorrow = timezone.localdate() + timedelta(days=1)

    with pytest.raises(RecordingError, match="cannot be in the future"):
        contacts.record_contact(
            entry=_entry(competitor, contact_date=tomorrow), researcher=researcher
        )
    assert not ContactForm(
        {
            "contact_date": tomorrow.isoformat(),
            "method": ContactMethod.CALL,
        }
    ).is_valid()


@pytest.mark.parametrize(
    ("status", "listed", "linked"),
    [
        (JobStatus.RESEARCHING, True, True),
        (JobStatus.DRAFT, True, False),
        (JobStatus.RESEARCHING, False, False),
    ],
    ids=["researching and listed", "draft", "not listed"],
)
def test_contacts_link_to_a_job_only_while_researching_and_for_its_competitors(
    competitor, researcher, status, listed, linked
):
    job = make_job(
        researcher=researcher, point=BOROUGH_HALL, confirmed=True, status=status
    )
    if listed:
        JobCompetitor.objects.create(job=job, competitor=competitor)

    if linked:
        contact = contacts.record_contact(
            entry=_entry(competitor, job=job), researcher=researcher
        )
        assert contact.job == job
    else:
        with pytest.raises(RecordingError, match="cannot be linked to the job"):
            contacts.record_contact(
                entry=_entry(competitor, job=job), researcher=researcher
            )


def test_contacts_are_recorded_only_for_current_competitors(competitor, researcher):
    receiving = make_competitor(display_name="Synthetic Receiving Day Care")
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )

    with pytest.raises(RecordingError, match="only for current competitors"):
        contacts.record_contact(entry=_entry(competitor), researcher=researcher)
    assert not Contact.objects.exists()


def test_prices_can_refer_to_definitions_created_in_the_same_contact(
    competitor, researcher
):
    entry = _entry(
        competitor,
        new_definitions=(
            NewDefinition(
                key="A",
                kind=ObservationKind.AGE_GROUP,
                values={"name": "Infants", "youngest_age_months": 6},
            ),
            NewDefinition(
                key="A",
                kind=ObservationKind.SCHEDULE,
                values={"name": "Full time", "days_per_week": 5},
            ),
        ),
        observations=(
            ObservationEntry(
                kind=ObservationKind.PRICE,
                values={
                    "amount": Decimal(2400),
                    "price_period": PricePeriod.MONTHLY,
                    "includes_discounts": False,
                },
                age_group=DefinitionReference(new_key="A"),
                schedule=DefinitionReference(new_key="A"),
            ),
        ),
    )

    contact = contacts.record_contact(entry=entry, researcher=researcher)

    price = contact.observations.get(kind=ObservationKind.PRICE)
    assert price.age_group is not None
    assert price.schedule is not None
    assert price.age_group.name == "Infants"
    assert price.schedule.name == "Full time"
    assert price.age_group.contact == contact
    assert price.value_summary() == "$2,400.00 monthly for Infants on Full time"


def test_a_kind_cannot_be_both_offered_and_not_offered_in_one_contact(
    competitor, researcher
):
    entry = _entry(
        competitor,
        observations=(
            ObservationEntry(
                kind=ObservationKind.DISCOUNT,
                values={"discount_kind": DiscountKind.SIBLING, "is_none_offered": True},
            ),
            ObservationEntry(
                kind=ObservationKind.DISCOUNT,
                values={"discount_kind": DiscountKind.SIBLING, "percent": Decimal(10)},
            ),
        ),
    )

    with pytest.raises(RecordingError, match="both offered and not offered"):
        contacts.record_contact(entry=entry, researcher=researcher)
    assert not Contact.objects.exists()


def _definitions(competitor, researcher):
    contact = make_contact(competitor=competitor, researcher=researcher)
    age_group = make_observation(
        contact=contact,
        kind=ObservationKind.AGE_GROUP,
        name="Infants",
        youngest_age_months=6,
    )
    schedule = make_observation(
        contact=contact, kind=ObservationKind.SCHEDULE, name="Full", days_per_week=5
    )
    return age_group, schedule


def _price(age_group, schedule):
    return ObservationEntry(
        kind=ObservationKind.PRICE,
        values={
            "amount": Decimal(2400),
            "price_period": PricePeriod.MONTHLY,
            "includes_discounts": False,
        },
        age_group=age_group,
        schedule=schedule,
    )


def test_retiring_definitions_records_one_retirement_per_definition(
    competitor, researcher
):
    age_group, schedule = _definitions(competitor, researcher)

    contact = contacts.record_contact(
        entry=_entry(
            competitor, retired_definition_ids=frozenset({age_group.pk, schedule.pk})
        ),
        researcher=researcher,
    )

    assert {
        (observation.kind, observation.age_group_id, observation.schedule_id)
        for observation in contact.observations.all()
    } == {
        (ObservationKind.AGE_GROUP_RETIREMENT, age_group.pk, None),
        (ObservationKind.SCHEDULE_RETIREMENT, None, schedule.pk),
    }


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("retiring another competitor's definition", "not current"),
        ("pricing another competitor's definition", "not current"),
        ("pricing a definition retired in the same contact", "same contact"),
        ("pricing a new definition the entry lacks", "does not define"),
        ("two facts of one single-fact kind", "invalid values"),
    ],
)
def test_definition_references_must_be_current_and_defined(
    competitor, researcher, case, message
):
    age_group, schedule = _definitions(competitor, researcher)
    other_competitor = make_competitor(display_name="Synthetic Other Day Care")
    other_age_group, _ = _definitions(other_competitor, researcher)
    existing_schedule = DefinitionReference(existing_id=schedule.pk)
    entry = {
        "retiring another competitor's definition": _entry(
            competitor, retired_definition_ids=frozenset({other_age_group.pk})
        ),
        "pricing another competitor's definition": _entry(
            competitor,
            observations=(
                _price(
                    DefinitionReference(existing_id=other_age_group.pk),
                    existing_schedule,
                ),
            ),
        ),
        "pricing a definition retired in the same contact": _entry(
            competitor,
            retired_definition_ids=frozenset({age_group.pk}),
            observations=(
                _price(
                    DefinitionReference(existing_id=age_group.pk), existing_schedule
                ),
            ),
        ),
        "pricing a new definition the entry lacks": _entry(
            competitor,
            observations=(_price(DefinitionReference(new_key="B"), existing_schedule),),
        ),
        "two facts of one single-fact kind": _entry(
            competitor, observations=(_weeks_open(50), _weeks_open(48))
        ),
    }[case]
    contact_count = Contact.objects.count()

    with pytest.raises(RecordingError, match=message):
        contacts.record_contact(entry=entry, researcher=researcher)
    assert Contact.objects.count() == contact_count


def test_notes_hold_at_most_2000_characters(competitor, researcher):
    form_data = {"contact_date": timezone.localdate().isoformat(), "method": "call"}

    assert ContactForm({**form_data, "notes": "n" * 2000}).is_valid()
    assert not ContactForm({**form_data, "notes": "n" * 2001}).is_valid()
    with pytest.raises(DataError), transaction.atomic():
        make_contact(competitor=competitor, researcher=researcher, notes="n" * 2001)


def test_the_role_is_one_of_the_listed_roles(competitor, researcher):
    form_data = {"contact_date": timezone.localdate().isoformat(), "method": "call"}

    assert ContactForm({**form_data, "person_role": "director"}).is_valid()
    assert not ContactForm({**form_data, "person_role": "neighbour"}).is_valid()
    with pytest.raises(RecordingError, match="invalid values"):
        contacts.record_contact(
            entry=_entry(competitor, person_role="neighbour"), researcher=researcher
        )


def test_contacts_and_observations_are_never_changed_or_deleted_by_code(
    competitor, researcher
):
    contact = make_contact(competitor=competitor, researcher=researcher)
    observation = make_observation(
        contact=contact, kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50
    )

    contact.notes = "Changed"
    observation.weeks_open_per_year = 48
    for record in (contact, observation):
        model = type(record)
        with pytest.raises(PermanentRecordError):
            record.save()
        with pytest.raises(PermanentRecordError):
            record.save(update_fields=("retracted_at", "notes"))
        with pytest.raises(PermanentRecordError):
            model.objects.filter(pk=record.pk).update(retraction_reason="Changed")
        with pytest.raises(PermanentRecordError):
            model.objects.filter(pk=record.pk).delete()
        # bulk_update raises inside an atomic block of its own without a savepoint,
        # which would break the test's transaction.
        with pytest.raises(PermanentRecordError), transaction.atomic():
            model.objects.bulk_update([record], ("retraction_reason",))
        with pytest.raises(PermanentRecordError):
            record.delete()
    assert Contact.objects.get().notes == ""
    assert Observation.objects.get().weeks_open_per_year == 50


def test_the_database_refuses_observations_that_mix_kinds(competitor, researcher):
    contact = make_contact(competitor=competitor, researcher=researcher)
    age_group = make_observation(
        contact=contact,
        kind=ObservationKind.AGE_GROUP,
        name="Infants",
        youngest_age_months=6,
    )
    schedule = make_observation(
        contact=contact, kind=ObservationKind.SCHEDULE, name="Full", days_per_week=5
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        make_observation(
            contact=contact,
            kind=ObservationKind.PRICE,
            age_group=age_group,
            schedule=schedule,
            amount=Decimal(2400),
            price_period=PricePeriod.MONTHLY,
            includes_discounts=False,
            percent=Decimal(10),
        )


def test_retraction_needs_a_reason_and_is_final(competitor, researcher):
    contact = make_contact(competitor=competitor, researcher=researcher)
    observation = make_observation(
        contact=contact, kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50
    )

    with pytest.raises(RetractionError, match="A reason is required"):
        contacts.retract_contact(contact=contact, researcher=researcher, reason="  ")
    with pytest.raises(RetractionError, match="at most 500"):
        contacts.retract_contact(
            contact=contact, researcher=researcher, reason="r" * 501
        )

    contacts.retract_contact(
        contact=contact, researcher=researcher, reason="Synthetic mistake"
    )

    contact.refresh_from_db()
    assert contact.retracted_by == researcher
    assert contact.retracted_at is not None
    assert contact.retraction_reason == "Synthetic mistake"
    with pytest.raises(RetractionError, match="already retracted"):
        contacts.retract_contact(
            contact=contact, researcher=researcher, reason="Synthetic mistake"
        )
    with pytest.raises(RetractionError, match="Its contact is already retracted"):
        contacts.retract_observation(
            observation=observation, researcher=researcher, reason="Synthetic mistake"
        )


def test_notes_never_reach_the_logs(competitor, researcher, caplog):
    caplog.set_level(logging.DEBUG)

    # Capturing queries turns on the debug cursor, which debug mode logs SQL through.
    with CaptureQueriesContext(connection):
        contacts.record_contact(
            entry=_entry(competitor, notes=NOTE_MARKER, observations=(_weeks_open(),)),
            researcher=researcher,
        )

    assert "Recorded contact" in caplog.text
    assert NOTE_MARKER not in caplog.text
