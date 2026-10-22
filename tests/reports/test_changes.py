"""Tests for the "Changes since report #N" section of a repeat job's report, built as
of the compared report's cut-off and as of this one.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from axisence.providers.models import Competitor, Provider, ProviderKind
from axisence.reports import changes, listed_competitors
from axisence.reports.changes import ListedChange, ValueChangeRow
from axisence.reports.content import build_report
from axisence.reports.formatting import EN_DASH
from axisence.reports.models import ListedCompetitor, ReportKind, ReportListing
from axisence.reports.rendering import render_report_html
from axisence.research.models import (
    AvailabilityStatus,
    ChargeFrequency,
    ChargeKind,
    DiscountKind,
    JobStatus,
    Observation,
    ObservationKind,
    OperatingStatus,
    PersonRole,
    PricePeriod,
)
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.reports.factories import (
    add_definitions,
    add_job_competitor,
    add_price,
    make_listed_report,
    make_ready_job,
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
from tests.subscriptions.factories import make_repeat_job

pytestmark = pytest.mark.django_db

NEW_YORK = ZoneInfo("America/New_York")
BEFORE = datetime(2026, 8, 20, 10, tzinfo=NEW_YORK)
COMPARED_CUT_OFF = datetime(2026, 9, 1, 12, tzinfo=NEW_YORK)
BETWEEN = datetime(2026, 9, 10, 10, tzinfo=NEW_YORK)
CUT_OFF = datetime(2026, 10, 1, 12, tzinfo=NEW_YORK)
AFTER_CUT_OFF = datetime(2026, 10, 5, 10, tzinfo=NEW_YORK)

VALUES_NOTE = (
    "Values are compared as stated: a price by its amount, period, and whether the "
    "amount includes discounts; availability by its status; a discount or charge by "
    "its value and frequency. Descriptions, earliest start dates, and expected waits "
    "are not compared."
)
TUITION_NOTE = (
    "Tuition at the reference ages compares the monthly equivalents of 5-day prices, "
    "as in the tuition comparison, so it bridges changes in a provider's own age "
    "groups and schedules."
)

ADDRESS_MARKER = "Synthetic Address Marker 6601"
REQUEST_ADDRESS_MARKER = "Synthetic Request Marker 6602"
MATCHED_ADDRESS_MARKER = "Synthetic Matched Marker 6603"
NOTES_MARKER = "synthetic notes marker 6604"
CALLER_MARKER = "synthetic-caller-6605"
ASSIGNED_MARKER = "synthetic-assigned-6606"
CONFIRMING_MARKER = "synthetic-confirming-6607"
RETRACTION_MARKER = "synthetic retraction marker 6608"
MARKERS = (
    ADDRESS_MARKER,
    REQUEST_ADDRESS_MARKER,
    MATCHED_ADDRESS_MARKER,
    NOTES_MARKER,
    CALLER_MARKER,
    ASSIGNED_MARKER,
    CONFIRMING_MARKER,
    RETRACTION_MARKER,
)


@pytest.fixture
def researcher():
    return make_researcher()


def _competitor(name, miles=0.3, *, first_seen_at=BEFORE):
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, miles), name=name)
    Provider.objects.filter(competitor=competitor).update(first_seen_at=first_seen_at)
    return competitor


def _repeat_market(
    researcher, *, compared_main, this_main, predecessor_fields=None, **repeat_fields
):
    """Return the compared report and a Researching repeat job listing this_main in
    its order.
    """
    predecessor = make_report_job(
        researcher=researcher, status=JobStatus.COMPLETE, **(predecessor_fields or {})
    )
    compared = make_listed_report(
        predecessor, main=compared_main, generated_at=COMPARED_CUT_OFF
    )
    repeat_job = make_repeat_job(predecessor, researcher=researcher, **repeat_fields)
    for index, competitor in enumerate(this_main):
        add_job_competitor(
            repeat_job,
            competitor,
            distance_meters=(0.1 + 0.05 * index) * METERS_PER_MILE,
        )
    return compared, repeat_job


def _build(repeat_job):
    return build_report(job=repeat_job, kind=ReportKind.DRAFT, cut_off=CUT_OFF)


def _section(repeat_job):
    return _build(repeat_job).content.changes


def _html(repeat_job):
    return render_report_html(_build(repeat_job).content, number=41)


def _between(html, start, end):
    start_index = html.index(start)
    return html[start_index : html.index(end, start_index)]


def _contact(competitor, researcher, entered_at, **fields):
    return make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=timezone.localdate(entered_at),
        entered_at=entered_at,
        **fields,
    )


def _definitions(contact, age_groups=(("Infants", 6, 18),)):
    return add_definitions(
        contact, age_groups=list(age_groups), schedules=[("Full time", 5, None)]
    )


def _discount(contact, kind, **values):
    return make_observation(
        contact=contact, kind=ObservationKind.DISCOUNT, discount_kind=kind, **values
    )


def _charge(contact, kind, **values):
    return make_observation(
        contact=contact, kind=ObservationKind.OTHER_CHARGE, charge_kind=kind, **values
    )


def _retire(competitor, receiver):
    Provider.objects.filter(competitor=competitor).update(competitor=receiver)
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiver, is_active=False
    )


def _report_closure(competitor, researcher, **fields):
    make_observation(
        contact=_contact(competitor, researcher, BETWEEN, **fields),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.PERMANENTLY_CLOSED,
    )


def _retract_at(record, moment, researcher, reason="Synthetic mistake"):
    record.retracted_at = moment
    record.retracted_by = researcher
    record.retraction_reason = reason
    record.save(update_fields=("retracted_at", "retracted_by", "retraction_reason"))


def _monthly(name, youngest, oldest):
    return f"{name} ({youngest}{EN_DASH}{oldest} months), Full time"


def test_competitors_match_through_merges_in_both_directions(researcher):
    receiver = _competitor("Synthetic Receiver")
    merged_entry = _competitor("Synthetic Merged Entry")
    continuing = _competitor("Synthetic Continuing")
    merged_since = _competitor("Synthetic Merged Since")
    first_of_two = _competitor("Synthetic First of Two")
    second_of_two = _competitor("Synthetic Second of Two")
    earlier = _contact(merged_since, researcher, BEFORE)
    (infants,), (full_time,) = _definitions(earlier)
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    compared, repeat_job = _repeat_market(
        researcher,
        compared_main=[merged_entry, continuing, first_of_two, second_of_two],
        this_main=[receiver, continuing, first_of_two],
    )
    _retire(merged_entry, receiver)
    _retire(merged_since, continuing)
    _retire(second_of_two, first_of_two)

    section = _section(repeat_job)

    assert section.added == ()
    assert section.gone == (
        ListedChange(
            1,
            "Synthetic Merged Entry",
            "Merged into Synthetic Receiver, number 1 in this report",
        ),
        ListedChange(
            4,
            "Synthetic Second of Two",
            "Merged into Synthetic First of Two, number 3 in this report",
        ),
    )
    assert [competitor.heading for competitor in section.continuing] == [
        "1. Synthetic Receiver",
        "2. Synthetic Continuing",
        "3. Synthetic First of Two",
    ]
    # The merged competitor's earlier price now counts on both sides.
    assert section.continuing[1].rows == ()
    assert section.continuing[1].counts_line == "1 value not re-checked."
    assert section.notes == (
        VALUES_NOTE,
        TUITION_NOTE,
        f"A competitor merged since report #{compared.pk} is compared with the "
        "combined values of the competitors merged into it.",
    )


def test_added_reasons(researcher):
    earlier_only = _competitor("Synthetic Earlier Only")
    newly_listed = _competitor(
        "Synthetic Newly Listed", first_seen_at=CUT_OFF - timedelta(days=3)
    )
    newly_included = _competitor("Synthetic Newly Included")
    # One provider first seen after the compared cut-off is not enough.
    make_provider(competitor=newly_included, kind=ProviderKind.SCHOOL_AGE_CHILD_CARE)
    without_providers = make_competitor(
        display_name="Synthetic Without Providers",
        location=point_north_of(BOROUGH_HALL, 0.4),
    )
    compared, repeat_job = _repeat_market(
        researcher,
        compared_main=[earlier_only],
        this_main=[newly_listed, newly_included, without_providers],
    )

    section = _section(repeat_job)
    html = _html(repeat_job)

    assert section.added == (
        ListedChange(
            1,
            "Synthetic Newly Listed",
            f"Newly listed by its regulator since report #{compared.pk}",
        ),
        ListedChange(2, "Synthetic Newly Included", "Newly included"),
        ListedChange(3, "Synthetic Without Providers", "Newly included"),
    )
    assert section.continuing == ()
    assert "<p>None.</p>" in _between(
        html, "<h3>Competitors in both reports</h3>", "</section>"
    )


def test_gone_reasons_in_their_order(researcher):
    closed_listed = _competitor("Synthetic Closed Listed")
    merged = _competitor("Synthetic Merged")
    receiver = _competitor("Synthetic Receiver")
    delisted = _competitor("Synthetic Delisted")
    closed_unlisted = _competitor("Synthetic Closed Unlisted")
    not_included = _competitor("Synthetic Not Included")
    merged_into_inactive = _competitor("Synthetic Merged Into Inactive")
    inactive_receiver = _competitor("Synthetic Inactive Receiver")
    _, repeat_job = _repeat_market(
        researcher,
        compared_main=[
            closed_listed,
            merged,
            receiver,
            delisted,
            closed_unlisted,
            not_included,
            merged_into_inactive,
            inactive_receiver,
        ],
        this_main=[closed_listed, receiver, inactive_receiver],
    )
    _report_closure(closed_listed, researcher)
    _report_closure(closed_unlisted, researcher)
    # The closure outranks the delisting, and the merge outranks the receiver's.
    Competitor.objects.filter(
        pk__in=[delisted.pk, closed_unlisted.pk, inactive_receiver.pk]
    ).update(is_active=False)
    _retire(merged, receiver)
    _retire(merged_into_inactive, inactive_receiver)

    section = _section(repeat_job)
    html = _html(repeat_job)

    assert section.gone == (
        ListedChange(
            1,
            "Synthetic Closed Listed",
            "Reported permanently closed (Sep 2026, call)",
        ),
        ListedChange(
            2,
            "Synthetic Merged",
            "Merged into Synthetic Receiver, number 1 in this report",
        ),
        ListedChange(4, "Synthetic Delisted", "No longer listed by its regulator"),
        ListedChange(
            5,
            "Synthetic Closed Unlisted",
            "Reported permanently closed (Sep 2026, call)",
        ),
        ListedChange(6, "Synthetic Not Included", "Not included in this report"),
        ListedChange(
            7,
            "Synthetic Merged Into Inactive",
            "Merged into Synthetic Inactive Receiver, number 2 in this report",
        ),
    )
    assert section.added == ()
    assert "<p>None.</p>" in _between(
        html, "<h3>Competitors added</h3>", "<h3>Competitors gone</h3>"
    )


def _settings_market(researcher, **repeat_fields):
    competitor = _competitor("Synthetic Continuing")
    _, repeat_job = _repeat_market(
        researcher, compared_main=[competitor], this_main=[competitor], **repeat_fields
    )
    return repeat_job


def test_search_settings_lines_and_the_point_threshold(researcher):
    changed = _settings_market(
        researcher,
        search_radius_miles=Decimal("1.50"),
        provider_kinds=[
            ProviderKind.FAMILY_DAY_CARE,
            ProviderKind.DAY_CARE_CENTER,
            ProviderKind.SMALL_DAY_CARE_CENTER,
            ProviderKind.GROUP_FAMILY_DAY_CARE,
        ],
        target_point=point_north_of(BOROUGH_HALL, 0.25),
    )
    barely_moved = _settings_market(
        researcher, target_point=point_north_of(BOROUGH_HALL, 0.004)
    )
    moved = _settings_market(
        researcher, target_point=point_north_of(BOROUGH_HALL, 0.006)
    )

    changed_section = _section(changed)
    unchanged_section = _section(barely_moved)

    assert changed_section.search_settings == (
        "The search radius changed from 1.00 mi to 1.50 mi.",
        "Provider kinds added: Group family day care, Family day care.",
        "Provider kinds removed: School-based child care.",
        "The target location moved 0.25 mi (straight line).",
    )
    assert changed_section.search_settings_note == (
        "Changes to the search settings can explain competitors added or gone."
    )
    assert "<h3>Search settings</h3>" in _html(changed)
    assert unchanged_section.search_settings == ()
    assert unchanged_section.search_settings_note == ""
    assert "<h3>Search settings</h3>" not in _html(barely_moved)
    assert _section(moved).search_settings == (
        "The target location moved 0.01 mi (straight line).",
    )


def test_verified_again_takes_the_newest_counting_contact_between_the_cut_offs(
    researcher,
):
    verified = _competitor("Synthetic Verified")
    unverified = _competitor("Synthetic Unverified")
    compared, repeat_job = _repeat_market(
        researcher,
        compared_main=[verified, unverified],
        this_main=[verified, unverified],
    )
    _discount(
        _contact(verified, researcher, BEFORE),
        DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    _discount(
        _contact(verified, researcher, BETWEEN),
        DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    # An unanswered call holds no observation, so it never counts.
    _contact(verified, researcher, BETWEEN + timedelta(days=5))
    retracted = _discount(
        _contact(verified, researcher, BETWEEN + timedelta(days=10)),
        DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    _retract_at(retracted, BETWEEN + timedelta(days=12), researcher)
    earlier = _contact(unverified, researcher, BEFORE)
    (infants,), (full_time,) = _definitions(earlier)
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    # Entered after this report's cut-off, so it counts in neither side.
    add_price(
        _contact(unverified, researcher, AFTER_CUT_OFF),
        infants,
        full_time,
        "2600",
        PricePeriod.MONTHLY,
    )

    first, second = _section(repeat_job).continuing

    assert first.verified_line == "Verified again: Call, Sep 10, 2026"
    assert first.counts_line == "1 value re-confirmed unchanged."
    assert second.verified_line == (f"Not verified again since report #{compared.pk}.")
    assert second.rows == ()
    assert second.counts_line == "1 value not re-checked."


def test_value_classes(researcher):
    competitor = _competitor("Synthetic Classes")
    _, repeat_job = _repeat_market(
        researcher, compared_main=[competitor], this_main=[competitor]
    )
    earlier = _contact(competitor, researcher, BEFORE)
    (infants, toddlers), (full_time,) = _definitions(
        earlier, age_groups=[("Infants", 6, 18), ("Toddlers", 12, 36)]
    )
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    add_price(earlier, toddlers, full_time, "2300", PricePeriod.MONTHLY)
    make_observation(
        contact=earlier,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.WAITLIST,
    )
    _discount(earlier, DiscountKind.MULTI_DAY, amount=Decimal(50))
    _discount(earlier, DiscountKind.SIBLING, is_none_offered=True)
    _discount(earlier, DiscountKind.REFERRAL, percent=Decimal(5))
    declined_upfront = _discount(
        earlier, DiscountKind.UPFRONT_PAYMENT, is_none_offered=True
    )
    _charge(earlier, ChargeKind.REGISTRATION, is_none_offered=True)
    _charge(earlier, ChargeKind.DEPOSIT, is_none_offered=True)
    later = _contact(competitor, researcher, BETWEEN)
    (twos,), _ = add_definitions(later, age_groups=[("Twos", 24, 36)])
    make_observation(
        contact=later, kind=ObservationKind.AGE_GROUP_RETIREMENT, age_group=toddlers
    )
    add_price(later, infants, full_time, "2600", PricePeriod.MONTHLY)
    add_price(later, twos, full_time, "2350", PricePeriod.MONTHLY)
    make_observation(
        contact=later,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.WAITLIST,
    )
    _discount(later, DiscountKind.SIBLING, percent=Decimal(10))
    _discount(later, DiscountKind.REFERRAL, is_none_offered=True)
    _charge(
        later,
        ChargeKind.REGISTRATION,
        amount=Decimal(150),
        charge_frequency=ChargeFrequency.ONCE,
    )
    _charge(later, ChargeKind.LATE_PICKUP, is_none_offered=True)
    _retract_at(declined_upfront, BETWEEN, researcher)

    (continuing,) = _section(repeat_job).continuing

    assert [row for row in continuing.rows if not row.value.startswith("Tuition")] == [
        ValueChangeRow(
            _monthly("Infants", 6, 18),
            ("$2,450 per month",),
            ("$2,600 per month",),
            "Changed",
        ),
        ValueChangeRow(
            _monthly("Toddlers", 12, 36),
            ("$2,300 per month",),
            ("No longer current",),
            "Ended",
        ),
        ValueChangeRow(
            _monthly("Twos", 24, 36), ("Not recorded",), ("$2,350 per month",), "New"
        ),
        ValueChangeRow("Sibling discount", ("None offered",), ("10%",), "New"),
        ValueChangeRow("Referral discount", ("5%",), ("None offered",), "Ended"),
        ValueChangeRow("Registration charge", ("None offered",), ("$150 once",), "New"),
    ]
    # Availability re-confirmed; the multi-day discount and the deposit declined on
    # both sides not re-checked; the upfront and late pickup kinds neither.
    assert continuing.counts_line == (
        "1 value re-confirmed unchanged; 2 values not re-checked."
    )


def test_a_retraction_after_the_compared_cut_off_gives_changed(researcher):
    competitor = _competitor("Synthetic Retracted Since")
    _, repeat_job = _repeat_market(
        researcher, compared_main=[competitor], this_main=[competitor]
    )
    first = _contact(competitor, researcher, BEFORE - timedelta(days=10))
    _discount(first, DiscountKind.SIBLING, percent=Decimal(10))
    corrected = _discount(
        _contact(competitor, researcher, BEFORE),
        DiscountKind.SIBLING,
        percent=Decimal(15),
    )
    _retract_at(corrected, BETWEEN, researcher)

    (continuing,) = _section(repeat_job).continuing

    assert continuing.rows == (
        ValueChangeRow("Sibling discount", ("15%",), ("10%",), "Changed"),
    )


def test_tuition_rows_compare_cells_with_their_star(researcher):
    starred = _competitor("Synthetic Starred")
    first_priced = _competitor("Synthetic First Priced")
    _, repeat_job = _repeat_market(
        researcher,
        compared_main=[starred, first_priced],
        this_main=[starred, first_priced],
    )
    earlier = _contact(starred, researcher, BEFORE)
    (infants,), (full_time,) = _definitions(earlier)
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    add_price(
        _contact(starred, researcher, BETWEEN),
        infants,
        full_time,
        "2450",
        PricePeriod.MONTHLY,
        includes_discounts=True,
    )
    later = _contact(first_priced, researcher, BETWEEN)
    (preschool,), (preschool_full_time,) = _definitions(
        later, age_groups=[("Preschool", 36, 60)]
    )
    add_price(later, preschool, preschool_full_time, "1800", PricePeriod.MONTHLY)

    first, second = _section(repeat_job).continuing

    assert [row for row in first.rows if row.value.startswith("Tuition")] == [
        ValueChangeRow("Tuition at 12 months", ("$2,450",), ("$2,450*",), "Changed")
    ]
    assert [row for row in second.rows if row.value.startswith("Tuition")] == [
        ValueChangeRow(
            f"Tuition at {age} months", ("No verified prices",), (text,), "Changed"
        )
        for age, text in (
            (12, "not offered"),
            (24, "not offered"),
            (36, "$1,800"),
            (48, "$1,800"),
        )
    ]


def test_rows_order_cells_and_empty_text(researcher):
    new_values = _competitor("Synthetic New Values")
    ended_value = _competitor("Synthetic Ended Value")
    unchanged = _competitor("Synthetic Unchanged")
    _, repeat_job = _repeat_market(
        researcher,
        compared_main=[new_values, ended_value, unchanged],
        this_main=[new_values, ended_value, unchanged],
    )
    later = _contact(new_values, researcher, BETWEEN)
    _charge(later, ChargeKind.OTHER)
    _charge(
        later,
        ChargeKind.DEPOSIT,
        months_of_tuition=Decimal(1),
        charge_frequency=ChargeFrequency.ONCE,
        description="refundable",
    )
    _discount(later, DiscountKind.OTHER, description="synthetic loyalty offer")
    _discount(later, DiscountKind.MULTI_DAY, amount=Decimal(50))
    _discount(later, DiscountKind.SIBLING, percent=Decimal(10))
    (infants,), (full_time,) = _definitions(later)
    make_observation(
        contact=later,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.WAITLIST,
        earliest_start_on=date(2027, 1, 5),
        expected_wait_months=3,
    )
    add_price(later, infants, full_time, "2450", PricePeriod.MONTHLY)
    retracted = _discount(
        _contact(ended_value, researcher, BEFORE),
        DiscountKind.MULTI_DAY,
        amount=Decimal(50),
    )
    _retract_at(retracted, BETWEEN, researcher)
    for entered_at in (BEFORE, BETWEEN):
        _discount(
            _contact(unchanged, researcher, entered_at),
            DiscountKind.SIBLING,
            percent=Decimal(10),
        )

    first, second, third = _section(repeat_job).continuing
    html = _html(repeat_job)

    assert [row.value for row in first.rows] == [
        "Tuition at 12 months",
        "Tuition at 24 months",
        "Tuition at 36 months",
        "Tuition at 48 months",
        _monthly("Infants", 6, 18),
        f"Availability, Infants (6{EN_DASH}18 months)",
        "Sibling discount",
        "Multi-day discount",
        "Other discount",
        "Deposit charge",
        "Other charge",
    ]
    assert first.rows[0].this == ("$2,450",)
    assert first.rows[1].this == ("not offered",)
    assert [row.this for row in first.rows[4:]] == [
        ("$2,450 per month",),
        ("Waitlist, earliest start Jan 5, 2027, expected wait 3 months",),
        ("10%",),
        ("$50",),
        ("Offered (synthetic loyalty offer)",),
        ("1 month of tuition once (refundable)",),
        ("Charged",),
    ]
    assert {(row.earlier, row.change) for row in first.rows[4:]} == {
        (("Not recorded",), "New")
    }
    assert second.rows == (
        ValueChangeRow("Multi-day discount", ("$50",), ("No longer current",), "Ended"),
    )
    assert third.rows == ()
    assert "<p>No compared value changed.</p>" in _between(
        html, "<h4>3. Synthetic Unchanged</h4>", "</div>"
    )


def test_counts_in_the_singular_and_the_plural(researcher):
    kinds = (
        DiscountKind.SIBLING,
        DiscountKind.MULTI_DAY,
        DiscountKind.UPFRONT_PAYMENT,
        DiscountKind.REFERRAL,
    )
    cases = (
        ("Synthetic One", 1, 4),
        ("Synthetic Three", 3, 4),
        ("Synthetic Two", 2, 2),
    )
    competitors = [_competitor(name) for name, _, _ in cases]
    changed_only = _competitor("Synthetic Changed Only")
    _, repeat_job = _repeat_market(
        researcher,
        compared_main=[*competitors, changed_only],
        this_main=[*competitors, changed_only],
    )
    for competitor, (_, confirmed_count, recorded_count) in zip(
        competitors, cases, strict=True
    ):
        earlier = _contact(competitor, researcher, BEFORE)
        later = _contact(competitor, researcher, BETWEEN)
        for index, kind in enumerate(kinds[:recorded_count]):
            _discount(earlier, kind, percent=Decimal(10))
            if index < confirmed_count:
                _discount(later, kind, percent=Decimal(10))
    _discount(
        _contact(changed_only, researcher, BEFORE),
        DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    _discount(
        _contact(changed_only, researcher, BETWEEN),
        DiscountKind.SIBLING,
        percent=Decimal(15),
    )

    section = _section(repeat_job)
    html = _html(repeat_job)

    assert [competitor.counts_line for competitor in section.continuing] == [
        "1 value re-confirmed unchanged; 3 values not re-checked.",
        "3 values re-confirmed unchanged; 1 value not re-checked.",
        "2 values re-confirmed unchanged.",
        "",
    ]
    assert 'class="note"' not in _between(
        html, "<h4>4. Synthetic Changed Only</h4>", "</div>"
    )


def test_section_texts_notes_and_the_cover_line(researcher):
    competitor = _competitor("Synthetic Continuing")
    compared, repeat_job = _repeat_market(
        researcher, compared_main=[competitor], this_main=[competitor]
    )
    number = compared.pk

    built = _build(repeat_job)
    section = built.content.changes
    html = render_report_html(built.content, number=41)

    assert section.heading == f"Changes since report #{number}"
    assert section.opening == (
        f"Compared with report #{number} of September 1, 2026. Values are compared as "
        "they stood when each report was generated."
    )
    assert section.gone_number_heading == f"No. in report #{number}"
    assert section.compared_column_heading == f"Report #{number}"
    # Without a merge, only the two fixed notes.
    assert section.notes == (VALUES_NOTE, TUITION_NOTE)
    assert built.content.compared_report_line == (
        f"Follows report #{number} of September 1, 2026"
    )
    assert (
        html.index("<p>Report #41</p>")
        < html.index(f"<p>Follows report #{number} of September 1, 2026</p>")
        < html.index('<p class="draft-mark">')
        < html.index("<h2>Summary</h2>")
        < html.index(f"<h2>Changes since report #{number}</h2>")
        < html.index("<h3>Competitors added</h3>")
        < html.index("<h3>Competitors gone</h3>")
        < html.index("<h3>Competitors in both reports</h3>")
        < html.index(VALUES_NOTE)
        < html.index("<h2>Map and overview</h2>")
    )


def test_reports_without_a_compared_report_have_no_section_and_no_line(researcher):
    built = build_report(
        job=make_ready_job(researcher=researcher),
        kind=ReportKind.DRAFT,
        cut_off=CUT_OFF,
    )
    html = render_report_html(built.content, number=41)

    assert built.content.changes is None
    assert built.content.compared_report_line == ""
    assert built.compared_report is None
    assert "Changes since report" not in html
    assert "Follows report" not in html


def test_the_section_holds_no_personal_data(caplog):
    confirming_user = make_researcher(CONFIRMING_MARKER)
    caller = make_researcher(CALLER_MARKER)
    job_fields = {
        "target_address": ADDRESS_MARKER,
        "geocoding_request_address": REQUEST_ADDRESS_MARKER,
        "geocoding_matched_address": MATCHED_ADDRESS_MARKER,
        "assigned_researcher": make_researcher(ASSIGNED_MARKER),
    }
    continuing = _competitor("Synthetic Continuing")
    closed = _competitor("Synthetic Closed")
    moved_point = point_north_of(BOROUGH_HALL, 0.25)
    _, repeat_job = _repeat_market(
        confirming_user,
        compared_main=[continuing, closed],
        this_main=[continuing],
        predecessor_fields=job_fields,
        target_point=moved_point,
        **job_fields,
    )
    _report_closure(
        closed, caller, notes=NOTES_MARKER, person_role=PersonRole.FRONT_DESK
    )
    earlier = _contact(continuing, caller, BEFORE, notes=NOTES_MARKER)
    (infants,), (full_time,) = _definitions(earlier)
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    retracted = add_price(
        _contact(continuing, caller, BEFORE + timedelta(days=2)),
        infants,
        full_time,
        "2525",
        PricePeriod.MONTHLY,
    )
    _retract_at(retracted, BETWEEN, caller, reason=RETRACTION_MARKER)
    add_price(
        _contact(
            continuing,
            caller,
            BETWEEN,
            notes=NOTES_MARKER,
            person_role=PersonRole.FRONT_DESK,
        ),
        infants,
        full_time,
        "2600",
        PricePeriod.MONTHLY,
    )

    with caplog.at_level(logging.DEBUG):
        html = _html(repeat_job)

    assert "The target location moved" in html
    assert "Reported permanently closed (" in html
    assert "Verified again: Call," in html
    assert "<td>Changed</td>" in html
    forbidden = [
        *MARKERS,
        "Front desk",
        "Contact #",
        *(str(observation) for observation in Observation.objects.all()),
        f"{BOROUGH_HALL.x:.4f}",
        f"{BOROUGH_HALL.y:.4f}",
        f"{moved_point.x:.4f}",
        f"{moved_point.y:.4f}",
    ]
    for text in forbidden:
        assert text not in html
    for record in caplog.records:
        message = record.getMessage()
        assert not any(text in message for text in forbidden)


def _add_entries_of_every_kind(compared, repeat_job, researcher, start):
    """List a continuing competitor with values and one gone entry of each reason in
    the compared report, numbered from start.
    """
    continuing = _competitor(f"Synthetic Continuing {start}")
    merged = _competitor(f"Synthetic Merged {start}")
    closed = _competitor(f"Synthetic Closed {start}")
    delisted = _competitor(f"Synthetic Delisted {start}")
    left_out = _competitor(f"Synthetic Left Out {start}")
    ListedCompetitor.objects.bulk_create(
        [
            ListedCompetitor(
                report=compared,
                competitor=competitor,
                listing=ReportListing.MAIN,
                number=start + offset,
            )
            for offset, competitor in enumerate(
                (continuing, merged, closed, delisted, left_out)
            )
        ]
    )
    add_job_competitor(
        repeat_job, continuing, distance_meters=(0.1 + 0.01 * start) * METERS_PER_MILE
    )
    earlier = _contact(continuing, researcher, BEFORE)
    (infants,), (full_time,) = _definitions(earlier)
    add_price(earlier, infants, full_time, "2450", PricePeriod.MONTHLY)
    _discount(earlier, DiscountKind.SIBLING, percent=Decimal(10))
    later = _contact(continuing, researcher, BETWEEN)
    add_price(later, infants, full_time, "2600", PricePeriod.MONTHLY)
    _discount(later, DiscountKind.SIBLING, percent=Decimal(10))
    _discount(
        _contact(merged, researcher, BEFORE), DiscountKind.REFERRAL, percent=Decimal(5)
    )
    _retire(merged, continuing)
    _report_closure(closed, researcher)
    Competitor.objects.filter(pk=delisted.pk).update(is_active=False)


def test_building_a_repeat_report_runs_a_fixed_number_of_queries(
    researcher, django_assert_num_queries, monkeypatch
):
    first = _competitor("Synthetic First")
    compared, repeat_job = _repeat_market(
        researcher, compared_main=[first], this_main=[first]
    )
    _add_entries_of_every_kind(compared, repeat_job, researcher, 2)
    _build(repeat_job)
    with CaptureQueriesContext(connection) as small_report:
        _build(repeat_job)

    for start in (7, 12, 17):
        _add_entries_of_every_kind(compared, repeat_job, researcher, start)
    comparison_query_counts = []

    def counting(function):
        def counted(*arguments, **keyword_arguments):
            with CaptureQueriesContext(connection) as captured:
                result = function(*arguments, **keyword_arguments)
            comparison_query_counts.append(len(captured.captured_queries))
            return result

        return counted

    # The lookup of the compared report and the section are all a repeat report adds.
    monkeypatch.setattr(
        listed_competitors,
        "latest_final_report_with_listings",
        counting(listed_competitors.latest_final_report_with_listings),
    )
    monkeypatch.setattr(
        changes, "build_changes_section", counting(changes.build_changes_section)
    )

    with django_assert_num_queries(len(small_report.captured_queries)):
        _build(repeat_job)

    assert len(comparison_query_counts) == 2
    assert sum(comparison_query_counts) <= 5
