"""Tests for the rules that pick the current value of every slot."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from axisence.providers.models import Competitor
from axisence.research import contacts
from axisence.research.current_values import (
    Slot,
    compute_current_values,
    load_current_values,
)
from axisence.research.merges import MergeChains
from axisence.research.models import (
    DiscountKind,
    Observation,
    ObservationKind,
    ParkingAvailability,
    PricePeriod,
)
from tests.providers.factories import make_competitor, make_researcher
from tests.research.factories import make_contact, make_observation

pytestmark = pytest.mark.django_db

FIRST_DAY = date(2026, 9, 1)
SECOND_DAY = date(2026, 9, 2)


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def competitor():
    return make_competitor(display_name="Synthetic Day Care")


def _definitions(competitor, researcher, contact_date=date(2026, 8, 1)):
    contact = make_contact(
        competitor=competitor, researcher=researcher, contact_date=contact_date
    )
    age_group = make_observation(
        contact=contact,
        kind=ObservationKind.AGE_GROUP,
        name="Infants",
        youngest_age_months=6,
        oldest_age_months=18,
    )
    schedule = make_observation(
        contact=contact,
        kind=ObservationKind.SCHEDULE,
        name="Full time",
        days_per_week=5,
    )
    return age_group, schedule


def _price(contact, age_group, schedule, amount):
    return make_observation(
        contact=contact,
        kind=ObservationKind.PRICE,
        age_group=age_group,
        schedule=schedule,
        amount=Decimal(amount),
        price_period=PricePeriod.MONTHLY,
        includes_discounts=False,
    )


def _current(competitor):
    return load_current_values(competitor.pk, MergeChains.load())


def _price_slot(age_group, schedule):
    return Slot(
        ObservationKind.PRICE, age_group_id=age_group.pk, schedule_id=schedule.pk
    )


def _current_as_of(competitor, moment):
    return compute_current_values(
        Observation.objects.filter(contact__competitor=competitor).select_related(
            "contact"
        ),
        as_of=moment,
    )


def test_the_newest_contact_wins_by_date_then_entry_time(competitor, researcher):
    age_group, schedule = _definitions(competitor, researcher)
    now = timezone.now()
    older_day = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=FIRST_DAY,
        entered_at=now,
    )
    _price(older_day, age_group, schedule, "2400")
    newer_day = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=SECOND_DAY,
        entered_at=now - timedelta(days=5),
    )
    _price(newer_day, age_group, schedule, "2500")
    assert (
        _current(competitor).slots[_price_slot(age_group, schedule)].contact
        == newer_day
    )

    later_entry = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=SECOND_DAY,
        entered_at=now - timedelta(days=4),
    )
    later_price = _price(later_entry, age_group, schedule, "2600")

    current_slot = _current(competitor).slots[_price_slot(age_group, schedule)]
    assert current_slot.contact == later_entry
    assert current_slot.observations == (later_price,)


def test_two_quotes_for_one_slot_in_one_contact_are_current_together(
    competitor, researcher
):
    age_group, schedule = _definitions(competitor, researcher)
    contact = make_contact(competitor=competitor, researcher=researcher)
    first = _price(contact, age_group, schedule, "2400")
    second = _price(contact, age_group, schedule, "2450")

    current_slot = _current(competitor).slots[_price_slot(age_group, schedule)]

    assert current_slot.observations == (first, second)


def test_none_offered_supersedes_older_rows_of_its_kind(competitor, researcher):
    older = make_contact(
        competitor=competitor, researcher=researcher, contact_date=FIRST_DAY
    )
    make_observation(
        contact=older,
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    multi_day = make_observation(
        contact=older,
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.MULTI_DAY,
        amount=Decimal(50),
    )
    newer = make_contact(
        competitor=competitor, researcher=researcher, contact_date=SECOND_DAY
    )
    make_observation(
        contact=newer,
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        is_none_offered=True,
    )

    slots = _current(competitor).slots
    sibling = slots[Slot(ObservationKind.DISCOUNT, subkind=DiscountKind.SIBLING)]

    assert [observation.is_none_offered for observation in sibling.observations] == [
        True
    ]
    assert sibling.contact == newer
    assert slots[
        Slot(ObservationKind.DISCOUNT, subkind=DiscountKind.MULTI_DAY)
    ].observations == (multi_day,)


@pytest.mark.parametrize("retracted", ["observation", "contact"])
def test_retractions_fall_back_to_the_next_newest_contact(
    competitor, researcher, retracted
):
    age_group, schedule = _definitions(competitor, researcher)
    older = make_contact(
        competitor=competitor, researcher=researcher, contact_date=FIRST_DAY
    )
    older_price = _price(older, age_group, schedule, "2400")
    newer = make_contact(
        competitor=competitor, researcher=researcher, contact_date=SECOND_DAY
    )
    newer_price = _price(newer, age_group, schedule, "2500")

    if retracted == "observation":
        contacts.retract_observation(
            observation=newer_price, researcher=researcher, reason="Synthetic mistake"
        )
    else:
        contacts.retract_contact(
            contact=newer, researcher=researcher, reason="Synthetic mistake"
        )

    current_slot = _current(competitor).slots[_price_slot(age_group, schedule)]
    assert current_slot.observations == (older_price,)


def test_retired_or_retracted_definitions_take_their_facts_out_of_current_values(
    competitor, researcher
):
    age_group, schedule = _definitions(competitor, researcher)
    contact = make_contact(competitor=competitor, researcher=researcher)
    _price(contact, age_group, schedule, "2400")
    retiring = make_contact(competitor=competitor, researcher=researcher)
    retirement = make_observation(
        contact=retiring, kind=ObservationKind.AGE_GROUP_RETIREMENT, age_group=age_group
    )

    current = _current(competitor)
    assert current.age_groups == ()
    assert current.slots == {}

    contacts.retract_observation(
        observation=retirement, researcher=researcher, reason="Synthetic mistake"
    )
    current = _current(competitor)
    assert current.age_groups == (age_group,)
    assert _price_slot(age_group, schedule) in current.slots

    contacts.retract_observation(
        observation=age_group, researcher=researcher, reason="Synthetic mistake"
    )
    current = _current(competitor)
    assert current.age_groups == ()
    assert current.schedules == (schedule,)
    assert current.slots == {}


def test_observations_of_merged_competitors_count_with_their_own_dates(researcher):
    first = make_competitor(display_name="Synthetic First Day Care")
    second = make_competitor(display_name="Synthetic Second Day Care")
    current = make_competitor(display_name="Synthetic Current Day Care")
    # The newest value belongs to the competitor two merges away from the current one.
    for competitor, contact_date, weeks in (
        (current, date(2026, 8, 30), 52),
        (first, SECOND_DAY, 50),
        (second, FIRST_DAY, 48),
    ):
        contact = make_contact(
            competitor=competitor, researcher=researcher, contact_date=contact_date
        )
        make_observation(
            contact=contact,
            kind=ObservationKind.WEEKS_OPEN,
            weeks_open_per_year=weeks,
        )
    now = timezone.now()
    Competitor.objects.filter(pk=first.pk).update(
        retired_at=now, merged_into=second, is_active=False
    )
    Competitor.objects.filter(pk=second.pk).update(
        retired_at=now, merged_into=current, is_active=False
    )

    current_slot = _current(current).slots[Slot(ObservationKind.WEEKS_OPEN)]

    assert current_slot.contact.competitor_id == first.pk
    assert current_slot.contact.contact_date == SECOND_DAY
    assert current_slot.observations[0].weeks_open_per_year == 50


def test_as_of_a_moment_a_contact_entered_later_does_not_count(competitor, researcher):
    age_group, schedule = _definitions(competitor, researcher)
    moment = timezone.now()
    older = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=FIRST_DAY,
        entered_at=moment - timedelta(hours=1),
    )
    older_price = _price(older, age_group, schedule, "2400")
    newer = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=SECOND_DAY,
        entered_at=moment + timedelta(hours=1),
    )
    newer_price = _price(newer, age_group, schedule, "2500")
    slot = _price_slot(age_group, schedule)

    assert _current_as_of(competitor, moment).slots[slot].observations == (older_price,)
    assert _current(competitor).slots[slot].observations == (newer_price,)


def test_as_of_a_moment_a_later_retraction_does_not_count(competitor, researcher):
    retracted_observation_contact = make_contact(
        competitor=competitor, researcher=researcher
    )
    retracted_observation = make_observation(
        contact=retracted_observation_contact,
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=50,
    )
    retracted_contact = make_contact(competitor=competitor, researcher=researcher)
    make_observation(
        contact=retracted_contact, kind=ObservationKind.STATED_PLACES, stated_places=40
    )
    early_retraction_contact = make_contact(
        competitor=competitor, researcher=researcher
    )
    early_retraction = make_observation(
        contact=early_retraction_contact,
        kind=ObservationKind.PARKING,
        parking=ParkingAvailability.STREET_ONLY,
    )
    contacts.retract_observation(
        observation=early_retraction, researcher=researcher, reason="Synthetic mistake"
    )
    moment = timezone.now()
    contacts.retract_observation(
        observation=retracted_observation,
        researcher=researcher,
        reason="Synthetic mistake",
    )
    contacts.retract_contact(
        contact=retracted_contact, researcher=researcher, reason="Synthetic mistake"
    )

    as_of_moment = _current_as_of(competitor, moment).slots
    as_of_now = _current_as_of(competitor, timezone.now()).slots

    assert set(as_of_moment) == {
        Slot(ObservationKind.WEEKS_OPEN),
        Slot(ObservationKind.STATED_PLACES),
    }
    assert as_of_now == {}
    assert _current(competitor).slots == {}


def test_as_of_a_moment_a_later_retirement_does_not_retire(competitor, researcher):
    age_group, schedule = _definitions(competitor, researcher)
    moment = timezone.now()
    contact = make_contact(
        competitor=competitor,
        researcher=researcher,
        entered_at=moment - timedelta(hours=1),
    )
    _price(contact, age_group, schedule, "2400")
    retiring = make_contact(
        competitor=competitor,
        researcher=researcher,
        entered_at=moment + timedelta(hours=1),
    )
    make_observation(
        contact=retiring, kind=ObservationKind.AGE_GROUP_RETIREMENT, age_group=age_group
    )

    as_of_moment = _current_as_of(competitor, moment)

    assert as_of_moment.age_groups == (age_group,)
    assert _price_slot(age_group, schedule) in as_of_moment.slots
    assert _current(competitor).age_groups == ()
