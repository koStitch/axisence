"""Tests for the call sheet's history of values, as runs of one stated value."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from axisence.providers.models import Competitor
from axisence.research import contacts
from axisence.research.current_values import load_competitor_observations
from axisence.research.merges import MergeChains
from axisence.research.models import (
    AvailabilityStatus,
    ChargeFrequency,
    ChargeKind,
    ContactMethod,
    DiscountKind,
    ObservationKind,
    PersonRole,
    PricePeriod,
)
from axisence.research.value_history import HistoryRun, build_value_history
from tests.providers.factories import make_competitor, make_researcher
from tests.reports.factories import add_definitions, add_price
from tests.research.factories import make_contact, make_observation

pytestmark = pytest.mark.django_db

# The en dash of ranges, as a code point, because ruff flags the literal character.
EN_DASH = chr(0x2013)
NOTES_MARKER = "synthetic history notes marker 5501"
RESEARCHER_MARKER = "synthetic-history-researcher-5502"


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def competitor():
    return make_competitor(display_name="Synthetic Day Care")


def _history(competitor):
    return build_value_history(
        load_competitor_observations(competitor.pk, MergeChains.load())
    )


def _contact(competitor, researcher, day, **fields):
    return make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=date(2026, 10, day),
        entered_at=timezone.now() + timedelta(minutes=day),
        **fields,
    )


def _definitions(contact):
    (infants,), (full_time,) = add_definitions(
        contact, age_groups=[("Infants", 6, 18)], schedules=[("Full time", 5, None)]
    )
    return infants, full_time


def _sibling_discount(contact, **values):
    return make_observation(
        contact=contact,
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        **values,
    )


def _call_sheet(client, competitor):
    return client.get(
        reverse("admin:research_contact_call_sheet", args=(competitor.pk,))
    ).text


def _between(page, start, end):
    start_index = page.index(start)
    return page[start_index : page.index(end, start_index)]


def test_equal_consecutive_contacts_form_one_run_and_a_change_starts_another(
    competitor, researcher
):
    first = _contact(competitor, researcher, 1)
    infants, full_time = _definitions(first)
    add_price(first, infants, full_time, "2450", PricePeriod.MONTHLY)
    add_price(
        _contact(competitor, researcher, 5),
        infants,
        full_time,
        "2450.00",
        PricePeriod.MONTHLY,
    )
    add_price(
        _contact(competitor, researcher, 9),
        infants,
        full_time,
        "2600",
        PricePeriod.MONTHLY,
    )

    (slot,) = _history(competitor)

    assert slot.runs == (
        HistoryRun(
            values=("$2,600.00 monthly",),
            first_recorded="2026-10-09, Call",
            last_confirmed="2026-10-09, Call",
            contact_count=1,
        ),
        HistoryRun(
            values=("$2,450.00 monthly",),
            first_recorded="2026-10-01, Call",
            last_confirmed="2026-10-05, Call",
            contact_count=2,
        ),
    )


def test_retracted_observations_and_contacts_are_left_out(competitor, researcher):
    _sibling_discount(_contact(competitor, researcher, 1), percent=Decimal(10))
    retracted_value = _sibling_discount(
        _contact(competitor, researcher, 3), percent=Decimal(15)
    )
    retracted_contact = _contact(competitor, researcher, 5)
    _sibling_discount(retracted_contact, percent=Decimal(20))
    _sibling_discount(_contact(competitor, researcher, 7), percent=Decimal(10))
    contacts.retract_observation(
        observation=retracted_value, researcher=researcher, reason="Synthetic mistake"
    )
    contacts.retract_contact(
        contact=retracted_contact, researcher=researcher, reason="Synthetic mistake"
    )

    (slot,) = _history(competitor)

    assert slot.runs == (
        HistoryRun(
            values=("Sibling: 10%",),
            first_recorded="2026-10-01, Call",
            last_confirmed="2026-10-07, Call",
            contact_count=2,
        ),
    )


def test_merged_competitors_values_are_included(competitor, researcher):
    merged = make_competitor(display_name="Synthetic Merged Day Care")
    _sibling_discount(_contact(merged, researcher, 1), percent=Decimal(10))
    _sibling_discount(_contact(competitor, researcher, 4), percent=Decimal(10))
    Competitor.objects.filter(pk=merged.pk).update(
        retired_at=timezone.now(), merged_into=competitor, is_active=False
    )

    (slot,) = _history(competitor)

    assert slot.runs == (
        HistoryRun(
            values=("Sibling: 10%",),
            first_recorded="2026-10-01, Call",
            last_confirmed="2026-10-04, Call",
            contact_count=2,
        ),
    )


def test_retired_definitions_keep_their_slots_with_retirement_lines(
    competitor, researcher
):
    defining = _contact(competitor, researcher, 1)
    (toddlers,), (full_time,) = add_definitions(
        defining, age_groups=[("Toddlers", 12, 36)], schedules=[("Full time", 5, None)]
    )
    add_price(defining, toddlers, full_time, "2100", PricePeriod.MONTHLY)
    retiring = _contact(competitor, researcher, 3, method=ContactMethod.WEBSITE)
    make_observation(
        contact=retiring, kind=ObservationKind.AGE_GROUP_RETIREMENT, age_group=toddlers
    )
    make_observation(
        contact=retiring, kind=ObservationKind.SCHEDULE_RETIREMENT, schedule=full_time
    )

    (slot,) = _history(competitor)

    assert slot.heading == f"Price for Toddlers (12{EN_DASH}36 months) on Full time"
    assert slot.retirement_lines == (
        "Age group retired 2026-10-03, Website",
        "Schedule retired 2026-10-03, Website",
    )
    assert slot.runs[0].values == ("$2,100.00 monthly",)


def test_none_offered_runs_and_values_without_their_slot_name(competitor, researcher):
    contact = _contact(competitor, researcher, 2)
    infants, full_time = _definitions(contact)
    add_price(contact, infants, full_time, "2450", PricePeriod.MONTHLY)
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.WAITLIST,
        earliest_start_on=date(2027, 1, 5),
        expected_wait_months=3,
    )
    _sibling_discount(contact, is_none_offered=True)

    price, availability, discount = _history(competitor)

    assert price.runs[0].values == ("$2,450.00 monthly",)
    assert availability.runs[0].values == (
        "Waitlist, earliest start 2027-01-05, wait 3 months",
    )
    assert discount.runs[0].values == ("Sibling: none offered",)


def test_slots_are_ordered_and_headed(competitor, researcher):
    contact = _contact(competitor, researcher, 2)
    make_observation(
        contact=contact,
        kind=ObservationKind.OTHER_CHARGE,
        charge_kind=ChargeKind.REGISTRATION,
        amount=Decimal(150),
        charge_frequency=ChargeFrequency.ONCE,
    )
    _sibling_discount(contact, percent=Decimal(10))
    (infants, preschool), (full_time,) = add_definitions(
        contact,
        age_groups=[("Infants", 6, 18), ("Preschool", 36, None)],
        schedules=[("Full time", 5, None)],
    )
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=preschool,
        availability_status=AvailabilityStatus.OPENINGS,
    )
    add_price(contact, infants, full_time, "2450", PricePeriod.MONTHLY)

    assert [slot.heading for slot in _history(competitor)] == [
        f"Price for Infants (6{EN_DASH}18 months) on Full time",
        "Availability for Preschool (36 months and older)",
        "Discount: Sibling",
        "Other charge: Registration",
    ]


def test_the_call_sheet_shows_history_between_current_values_and_previous_contacts(
    admin_client, competitor
):
    marked = _contact(
        competitor,
        make_researcher(RESEARCHER_MARKER),
        14,
        notes=NOTES_MARKER,
        person_role=PersonRole.FRONT_DESK,
    )
    infants, full_time = _definitions(marked)
    add_price(marked, infants, full_time, "2450", PricePeriod.MONTHLY)

    page = _call_sheet(admin_client, competitor)

    assert (
        page.index("<h2>Current values</h2>")
        < page.index("<h2>History</h2>")
        < page.index("<h2>Previous contacts</h2>")
    )
    history = _between(page, "<h2>History</h2>", "<h2>Previous contacts</h2>")
    previous_contacts = _between(page, "<h2>Previous contacts</h2>", "<form")
    assert "<td>$2,450.00 monthly</td>" in history
    assert "<td>2026-10-14, Call</td>" in history
    for marker in (NOTES_MARKER, "Front desk", RESEARCHER_MARKER):
        assert marker not in history
        assert marker in previous_contacts


def test_a_competitor_without_value_slots_reads_nothing_recorded_yet(
    admin_client, competitor, researcher
):
    contact = _contact(competitor, researcher, 14)
    add_definitions(contact, age_groups=[("Infants", 6, 18)])
    make_observation(
        contact=contact, kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50
    )

    page = _call_sheet(admin_client, competitor)

    history = _between(page, "<h2>History</h2>", "<h2>Previous contacts</h2>")
    assert "<p>Nothing recorded yet.</p>" in history
    assert "<h3>" not in history
    assert "50 weeks per year" in _between(
        page, "<h2>Current values</h2>", "<h2>History</h2>"
    )
    assert page.count("Nothing recorded yet.") == 1


def _record_slots(competitor, researcher, day, *, discount_kind=None):
    contact = _contact(competitor, researcher, day)
    (age_group,), (schedule,) = add_definitions(
        contact,
        age_groups=[(f"Synthetic Group {day}", day, day + 12)],
        schedules=[(f"Synthetic Schedule {day}", 5, None)],
    )
    add_price(contact, age_group, schedule, str(2000 + day), PricePeriod.MONTHLY)
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=age_group,
        availability_status=AvailabilityStatus.OPENINGS,
    )
    if discount_kind is not None:
        make_observation(
            contact=contact,
            kind=ObservationKind.DISCOUNT,
            discount_kind=discount_kind,
            percent=Decimal(day),
        )


def test_the_call_sheet_history_adds_no_query_per_slot_or_observation(
    admin_client, competitor, researcher, django_assert_num_queries
):
    _record_slots(competitor, researcher, 1)
    # A first request fills the caches every later request shares.
    _call_sheet(admin_client, competitor)
    with CaptureQueriesContext(connection) as small_sheet:
        _call_sheet(admin_client, competitor)

    for day, discount_kind in zip(
        (3, 5, 7, 9),
        (
            DiscountKind.MULTI_DAY,
            DiscountKind.UPFRONT_PAYMENT,
            DiscountKind.REFERRAL,
            DiscountKind.OTHER,
        ),
        strict=True,
    ):
        _record_slots(competitor, researcher, day, discount_kind=discount_kind)

    with django_assert_num_queries(len(small_sheet.captured_queries)):
        page = _call_sheet(admin_client, competitor)
    assert page.count("<h3>") >= 12
