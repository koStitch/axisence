"""Tests for stated values, compared to tell a change from a re-confirmation, and the
order of value slots.
"""

from datetime import date
from decimal import Decimal

import pytest

from axisence.research.models import (
    AvailabilityStatus,
    ChargeFrequency,
    ChargeKind,
    DiscountKind,
    Observation,
    ObservationKind,
    PricePeriod,
)
from axisence.research.stated_values import (
    slot_stated_value,
    stated_value,
    value_slot_sort_key,
)
from tests.providers.factories import make_competitor, make_researcher
from tests.reports.factories import add_definitions, add_price
from tests.research.factories import make_contact, make_observation


def _price(amount, period=PricePeriod.MONTHLY, *, includes_discounts=False):
    return Observation(
        kind=ObservationKind.PRICE,
        amount=Decimal(amount),
        price_period=period,
        includes_discounts=includes_discounts,
    )


def test_each_kind_states_its_compared_fields():
    availability = Observation(
        kind=ObservationKind.AVAILABILITY,
        availability_status=AvailabilityStatus.WAITLIST,
        earliest_start_on=date(2027, 1, 5),
        expected_wait_months=3,
    )
    later_availability = Observation(
        kind=ObservationKind.AVAILABILITY,
        availability_status=AvailabilityStatus.WAITLIST,
        earliest_start_on=date(2027, 3, 1),
        expected_wait_months=5,
    )
    discount = Observation(
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        percent=Decimal(10),
        description="first synthetic wording",
    )
    redescribed = Observation(
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        percent=Decimal("10.00"),
        description="second synthetic wording",
    )
    none_offered = Observation(
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        is_none_offered=True,
    )
    charge = Observation(
        kind=ObservationKind.OTHER_CHARGE,
        charge_kind=ChargeKind.DEPOSIT,
        months_of_tuition=Decimal(1),
        charge_frequency=ChargeFrequency.ONCE,
        description="synthetic deposit wording",
    )

    assert stated_value(_price("2450", includes_discounts=True)) == (
        Decimal(2450),
        PricePeriod.MONTHLY,
        True,
    )
    assert stated_value(_price("2450.00")) == stated_value(_price("2450"))
    assert stated_value(_price("2450")) != stated_value(_price("2450", "weekly"))
    assert stated_value(availability) == (AvailabilityStatus.WAITLIST,)
    assert stated_value(availability) == stated_value(later_availability)
    assert stated_value(discount) == (False, None, Decimal(10))
    assert stated_value(discount) == stated_value(redescribed)
    assert stated_value(none_offered) == (True, None, None)
    assert stated_value(charge) == (False, None, Decimal(1), ChargeFrequency.ONCE)
    with pytest.raises(ValueError, match="have no stated value"):
        stated_value(
            Observation(kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50)
        )


def test_a_slot_is_compared_as_a_set_of_its_values():
    full_day = _price("2450")
    half_day = _price("1600")
    daily = _price("120", PricePeriod.DAILY)

    assert slot_stated_value([full_day, half_day]) == slot_stated_value(
        [half_day, full_day]
    )
    assert slot_stated_value([full_day, half_day]) != slot_stated_value(
        [full_day, half_day, daily]
    )
    assert slot_stated_value([full_day]) != slot_stated_value([half_day])


@pytest.mark.django_db
def test_value_slots_sort_by_kind_definition_and_subkind():
    contact = make_contact(
        competitor=make_competitor(display_name="Synthetic Day Care"),
        researcher=make_researcher(),
    )
    (infants, toddlers), (full_time, mornings, part_time) = add_definitions(
        contact,
        age_groups=[("Infants", 6, 18), ("Toddlers", 12, 36)],
        schedules=[
            ("Full time", 5, None),
            ("Mornings", 5, "4"),
            ("Part time", 3, None),
        ],
    )
    expected = [
        add_price(contact, infants, full_time, "2450", PricePeriod.MONTHLY),
        add_price(contact, infants, mornings, "1700", PricePeriod.MONTHLY),
        add_price(contact, infants, part_time, "90", PricePeriod.DAILY),
        add_price(contact, toddlers, full_time, "2300", PricePeriod.MONTHLY),
        *(
            make_observation(
                contact=contact,
                kind=ObservationKind.AVAILABILITY,
                age_group=age_group,
                availability_status=AvailabilityStatus.OPENINGS,
            )
            for age_group in (infants, toddlers)
        ),
        *(
            make_observation(
                contact=contact, kind=ObservationKind.DISCOUNT, discount_kind=kind
            )
            for kind in (DiscountKind.SIBLING, DiscountKind.REFERRAL)
        ),
        *(
            make_observation(
                contact=contact, kind=ObservationKind.OTHER_CHARGE, charge_kind=kind
            )
            for kind in (ChargeKind.REGISTRATION, ChargeKind.LATE_PICKUP)
        ),
    ]
    shuffled = [*expected[1::2], *expected[::2]]

    assert sorted(shuffled, key=value_slot_sort_key) == expected
