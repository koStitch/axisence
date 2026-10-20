"""Tests for monthly equivalents and the cells of the tuition comparison."""

import itertools
from decimal import Decimal

import pytest
from django.utils import timezone

from axisence.reports.content import build_report
from axisence.reports.formatting import EN_DASH
from axisence.reports.models import ReportKind
from axisence.reports.prices import comparison_cell, monthly_equivalent
from axisence.research.current_values import CurrentSlot, CurrentValues, Slot
from axisence.research.models import Contact, Observation, ObservationKind, PricePeriod
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    add_definitions,
    add_job_competitor,
    add_price,
    make_report_job,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
    point_north_of,
)

_primary_keys = itertools.count(1)


def _schedule(days, hours=None):
    return Observation(
        pk=next(_primary_keys),
        kind=ObservationKind.SCHEDULE,
        name=f"{days} days",
        days_per_week=days,
        hours_per_day=None if hours is None else Decimal(hours),
    )


def _age_group(youngest, oldest):
    return Observation(
        pk=next(_primary_keys),
        kind=ObservationKind.AGE_GROUP,
        name=f"From {youngest}",
        youngest_age_months=youngest,
        oldest_age_months=oldest,
    )


def _price(age_group, schedule, amount, period=PricePeriod.MONTHLY, discounts=False):
    return Observation(
        pk=next(_primary_keys),
        kind=ObservationKind.PRICE,
        age_group=age_group,
        schedule=schedule,
        amount=Decimal(amount),
        price_period=period,
        includes_discounts=discounts,
    )


def _current(age_groups, schedules, prices):
    contact = Contact(pk=next(_primary_keys))
    slots: dict[Slot, CurrentSlot] = {}
    for price in prices:
        slot = Slot(
            ObservationKind.PRICE,
            age_group_id=price.age_group_id,
            schedule_id=price.schedule_id,
        )
        existing = slots.get(slot)
        slots[slot] = CurrentSlot(
            slot=slot,
            contact=contact,
            observations=(*(existing.observations if existing else ()), price),
        )
    return CurrentValues(
        age_groups=tuple(age_groups), schedules=tuple(schedules), slots=slots
    )


@pytest.mark.parametrize(
    ("amount", "period", "schedule", "expected"),
    [
        ("2450", PricePeriod.MONTHLY, _schedule(5), Decimal(2450)),
        ("575", PricePeriod.WEEKLY, _schedule(5), Decimal(2492)),
        ("95", PricePeriod.DAILY, _schedule(3), Decimal(1235)),
        ("20", PricePeriod.HOURLY, _schedule(5, "10"), Decimal(4333)),
        ("30000", PricePeriod.ANNUAL, _schedule(5), Decimal(2500)),
        ("30", PricePeriod.ANNUAL, _schedule(5), Decimal(3)),
        ("20", PricePeriod.HOURLY, _schedule(5), None),
    ],
    ids=[
        "monthly",
        "weekly",
        "daily",
        "hourly",
        "annual",
        "a half rounds up",
        "hourly without hours",
    ],
)
def test_monthly_equivalents(amount, period, schedule, expected):
    price = _price(_age_group(0, None), schedule, amount, period)

    assert monthly_equivalent(price) == expected


def _inclusive_youngest():
    group, schedule = _age_group(12, 24), _schedule(5)
    return [group], [schedule], [_price(group, schedule, "2000")], 12, "$2,000"


def _inclusive_oldest():
    group, schedule = _age_group(0, 12), _schedule(5)
    return [group], [schedule], [_price(group, schedule, "2000")], 12, "$2,000"


def _open_ended():
    group, schedule = _age_group(36, None), _schedule(5)
    return [group], [schedule], [_price(group, schedule, "1800")], 48, "$1,800"


def _overlapping():
    younger, older, schedule = _age_group(6, 18), _age_group(12, 36), _schedule(5)
    prices = [_price(younger, schedule, "2300"), _price(older, schedule, "1950")]
    return [younger, older], [schedule], prices, 12, f"$1,950{EN_DASH}$2,300"


def _equal_amounts():
    younger, older, schedule = _age_group(6, 18), _age_group(12, 36), _schedule(5)
    prices = [_price(younger, schedule, "2000"), _price(older, schedule, "2000")]
    return [younger, older], [schedule], prices, 12, "$2,000"


def _discounted():
    group, schedule = _age_group(0, 24), _schedule(5)
    prices = [_price(group, schedule, "2000", discounts=True)]
    return [group], [schedule], prices, 12, "$2,000*"


def _three_day_ignored():
    group, five_days, three_days = _age_group(0, 24), _schedule(5), _schedule(3)
    prices = [_price(group, five_days, "2000"), _price(group, three_days, "1200")]
    return [group], [five_days, three_days], prices, 12, "$2,000"


def _uncomputable_adds_no_star():
    group = _age_group(0, 24)
    five_days, hours_unstated = _schedule(5, "10"), _schedule(5)
    prices = [
        _price(group, five_days, "2000"),
        _price(group, hours_unstated, "20", PricePeriod.HOURLY, discounts=True),
    ]
    return [group], [five_days, hours_unstated], prices, 12, "$2,000"


def _not_offered():
    group, schedule = _age_group(0, 12), _schedule(5)
    return [group], [schedule], [_price(group, schedule, "2000")], 48, "not offered"


def _no_five_day_price():
    group, schedule = _age_group(0, 24), _schedule(3)
    return [group], [schedule], [_price(group, schedule, "1200")], 12, "no 5-day price"


def _not_computable():
    group, schedule = _age_group(0, 24), _schedule(5)
    prices = [_price(group, schedule, "20", PricePeriod.HOURLY)]
    return [group], [schedule], prices, 12, "not computable"


CELLS = (
    _inclusive_youngest,
    _inclusive_oldest,
    _open_ended,
    _overlapping,
    _equal_amounts,
    _discounted,
    _three_day_ignored,
    _uncomputable_adds_no_star,
    _not_offered,
    _no_five_day_price,
    _not_computable,
)


@pytest.mark.parametrize(
    "case", CELLS, ids=[case.__name__.strip("_") for case in CELLS]
)
def test_comparison_cells(case):
    age_groups, schedules, prices, age_months, expected = case()

    cell = comparison_cell(_current(age_groups, schedules, prices), age_months)

    assert cell.text == expected
    assert cell.note == ""


@pytest.mark.django_db
def test_comparison_rows_note_free_seats_and_missing_prices():
    researcher = make_researcher()
    job = make_report_job(researcher=researcher)
    priced = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.2), name="Synthetic Priced"
    )
    unpriced = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.3), name="Synthetic Unpriced"
    )
    for competitor, miles in ((priced, 0.2), (unpriced, 0.3)):
        add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    contact = make_contact(competitor=priced, researcher=researcher)
    (group,), (schedule,) = add_definitions(
        contact, age_groups=[("Preschool", 30, 60)], schedules=[("Full time", 5, None)]
    )
    add_price(contact, group, schedule, "1900", PricePeriod.MONTHLY)
    unpriced_contact = make_contact(competitor=unpriced, researcher=researcher)
    for recorded, kind in (
        (contact, ObservationKind.THREE_K_FOR_ALL),
        (contact, ObservationKind.PRE_K_FOR_ALL),
        (unpriced_contact, ObservationKind.THREE_K_FOR_ALL),
    ):
        make_observation(contact=recorded, kind=kind, yes_no_answer=True)

    rows = build_report(
        job=job, kind=ReportKind.DRAFT, cut_off=timezone.now()
    ).content.comparison_rows

    priced_row, unpriced_row = rows
    assert [(cell.text, cell.note) for cell in priced_row.cells] == [
        ("not offered", ""),
        ("not offered", ""),
        ("$1,900", "3-K for All seats"),
        ("$1,900", "Pre-K for All seats"),
    ]
    assert unpriced_row.cells == ()
    assert unpriced_row.no_price_text == "No verified prices; 3-K for All seats"
