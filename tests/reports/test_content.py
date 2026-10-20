"""Tests for what a report shows, built from the database as of its cut-off."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from axisence.ingestion.models import IngestionSource
from axisence.providers.models import Competitor, Provider, ProviderKind, ProviderSource
from axisence.reports.competitors import FactLine, PriceGroup, PriceRow, ValueLine
from axisence.reports.content import build_report
from axisence.reports.formatting import EN_DASH
from axisence.reports.models import ReportKind
from axisence.research.models import (
    AvailabilityStatus,
    ContactMethod,
    JobCompetitor,
    ObservationKind,
    OperatingStatus,
    PricePeriod,
    ResearchStatus,
)
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.reports.factories import (
    add_definitions,
    add_job_competitor,
    add_price,
    make_area,
    make_census_2024,
    make_provider_run,
    make_report_job,
    record_every_value,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
    point_north_of,
)

pytestmark = pytest.mark.django_db

OCTOBER_14 = date(2026, 10, 14)
NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def job(researcher):
    return make_report_job(researcher=researcher)


def _build(job, kind=ReportKind.DRAFT, cut_off=None):
    return build_report(job=job, kind=kind, cut_off=cut_off or timezone.now())


def _listed(job, miles, name, research_status=ResearchStatus.DONE, **provider_fields):
    competitor = make_located_competitor(
        point_north_of(BOROUGH_HALL, miles), name=name, **provider_fields
    )
    add_job_competitor(
        job,
        competitor,
        distance_meters=miles * METERS_PER_MILE,
        research_status=research_status,
    )
    return competitor


def _entry(built, name):
    return next(entry for entry in built.content.competitors if entry.name == name)


def _summary(built):
    return {row.label: row.value for row in built.content.summary.rows}


def _retire(competitor, receiver):
    Provider.objects.filter(competitor=competitor).update(competitor=receiver)
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiver, is_active=False
    )


def _delist(competitor):
    Provider.objects.filter(competitor=competitor).update(missing_since=timezone.now())
    Competitor.objects.filter(pk=competitor.pk).update(is_active=False)


def _report_closure(competitor, researcher, status, contact_date=OCTOBER_14, **fields):
    contact = make_contact(
        competitor=competitor,
        researcher=researcher,
        contact_date=contact_date,
        **fields,
    )
    make_observation(
        contact=contact, kind=ObservationKind.OPERATING_STATUS, operating_status=status
    )
    return contact


def _retract_at(record, moment, researcher):
    record.retracted_at = moment
    record.retracted_by = researcher
    record.retraction_reason = "Synthetic mistake"
    record.save(update_fields=("retracted_at", "retracted_by", "retraction_reason"))


def test_competitors_appear_once_each_nearest_first_with_shared_numbers(
    job, researcher
):
    # Created first, so the tie puts it first by its lower primary key.
    tied_first = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.3), name="Synthetic Tied First"
    )
    tied_second = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.3), name="Synthetic Tied Second"
    )
    near = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.1), name="Synthetic Near"
    )
    receiving = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.5), name="Synthetic Receiving"
    )
    merged = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.05), name="Synthetic Merged"
    )
    own = make_located_competitor(BOROUGH_HALL, name="Synthetic Own")
    unlocated = make_competitor(display_name="Synthetic Unlocated")
    make_provider(original_name="Synthetic Unlocated", competitor=unlocated)
    for competitor, miles in (
        (tied_second, 0.3),
        (near, 0.1),
        (tied_first, 0.3),
        (merged, 0.05),
        (own, 0.0),
    ):
        add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    add_job_competitor(job, unlocated, distance_meters=None)
    job.own_competitor = own
    job.save()
    _retire(merged, receiving)

    content = _build(job).content

    assert [
        (entry.number, entry.name, entry.distance) for entry in content.competitors
    ] == [
        (1, "Synthetic Near", "0.10 mi"),
        (2, "Synthetic Tied First", "0.30 mi"),
        (3, "Synthetic Tied Second", "0.30 mi"),
        (4, "Synthetic Receiving", "0.50 mi"),
        (5, "Synthetic Unlocated", "Unknown"),
    ]
    assert [(row.number, row.name) for row in content.comparison_rows] == [
        (entry.number, entry.name) for entry in content.competitors
    ]
    assert [marker.number for marker in content.report_map.markers] == [1, 2, 3, 4]


def _temporarily_closed(competitor, row, researcher):
    _report_closure(competitor, researcher, OperatingStatus.TEMPORARILY_CLOSED)


def _no_longer_listed(competitor, row, researcher):
    _delist(competitor)


def _research_status(status):
    def change(competitor, row, researcher):
        JobCompetitor.objects.filter(pk=row.pk).update(research_status=status)

    return change


def _outside_the_radius(competitor, row, researcher):
    JobCompetitor.objects.filter(pk=row.pk).update(
        distance_meters=1.5 * METERS_PER_MILE
    )


def _school_based(competitor, row, researcher):
    Competitor.objects.filter(pk=competitor.pk).update(is_school_based=True)
    Provider.objects.filter(competitor=competitor).update(
        kind=ProviderKind.SCHOOL_BASED_CHILD_CARE
    )


def _suspended(competitor, row, researcher):
    Provider.objects.filter(competitor=competitor).update(
        source=ProviderSource.NY_STATE, ny_state_facility_status="Suspended"
    )


STATE_LABELS = (
    (
        "temporarily closed",
        _temporarily_closed,
        "Reported temporarily closed (Oct 2026, call)",
    ),
    ("no longer listed", _no_longer_listed, "No longer listed by its regulator"),
    (
        "unreachable",
        _research_status(ResearchStatus.UNREACHABLE),
        "Not reached for this report",
    ),
    (
        "in progress",
        _research_status(ResearchStatus.IN_PROGRESS),
        "Research in progress",
    ),
    ("outside the radius", _outside_the_radius, "Outside the search radius"),
    ("school-based", _school_based, "School-based"),
    ("facility status", _suspended, "Suspended"),
)


@pytest.mark.parametrize(
    ("change", "label"),
    [(change, label) for _, change, label in STATE_LABELS],
    ids=[name for name, *_ in STATE_LABELS],
)
def test_every_state_label(job, researcher, change, label):
    competitor = _listed(job, 0.2, "Synthetic Labelled")
    change(competitor, JobCompetitor.objects.get(job=job), researcher)

    draft = _entry(_build(job, ReportKind.DRAFT), "Synthetic Labelled")
    final = _entry(_build(job, ReportKind.FINAL), "Synthetic Labelled")

    assert draft.state_labels == (label,)
    if label == "Research in progress":
        assert final.state_labels == ()
        assert final.research_outcome == "Not researched, no verified information"
    else:
        assert final.state_labels == (label,)


def test_permanently_closed_competitors_are_listed_apart(job, researcher):
    _listed(job, 0.2, "Synthetic Open", total_capacity=40)
    farther = _listed(job, 0.4, "Synthetic Closed Farther", total_capacity=60)
    nearer = _listed(job, 0.3, "Synthetic Closed Nearer", total_capacity=60)
    for competitor in (farther, nearer):
        _report_closure(competitor, researcher, OperatingStatus.PERMANENTLY_CLOSED)

    content = _build(job).content

    assert [entry.name for entry in content.competitors] == ["Synthetic Open"]
    assert [row.name for row in content.comparison_rows] == ["Synthetic Open"]
    assert [marker.number for marker in content.report_map.markers] == [1]
    summary = {row.label: row.value for row in content.summary.rows}
    assert summary["Competitors"] == "1"
    assert summary["Reported permanently closed, listed under Reported closed"] == "2"
    assert summary["Total licensed capacity"] == "40"
    assert [(entry.name, entry.closure) for entry in content.reported_closed] == [
        (
            "Synthetic Closed Nearer",
            "Reported permanently closed (Oct 2026, call)",
        ),
        (
            "Synthetic Closed Farther",
            "Reported permanently closed (Oct 2026, call)",
        ),
    ]


def test_research_values_count_as_of_the_cut_off(job, researcher):
    cut_off = timezone.now()
    competitor = _listed(job, 0.2, "Synthetic Researched")
    definitions = make_contact(
        competitor=competitor,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 9, 1),
        entered_at=cut_off - timedelta(hours=3),
    )
    (toddlers, preschool), (full_time,) = add_definitions(
        definitions,
        age_groups=[("Toddlers", 12, 36), ("Preschool", 37, 60)],
        schedules=[("Full time", 5, None)],
    )
    add_price(definitions, toddlers, full_time, "2400", PricePeriod.MONTHLY)
    retracted_before = add_price(
        definitions, preschool, full_time, "2000", PricePeriod.MONTHLY
    )
    _retract_at(retracted_before, cut_off - timedelta(hours=1), researcher)
    retracted_later = make_contact(
        competitor=competitor,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 9, 2),
        entered_at=cut_off - timedelta(hours=2),
    )
    later_price = add_price(
        retracted_later, toddlers, full_time, "2500", PricePeriod.MONTHLY
    )
    _retract_at(later_price, cut_off + timedelta(hours=1), researcher)
    entered_later = make_contact(
        competitor=competitor,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 9, 3),
        entered_at=cut_off + timedelta(hours=1),
    )
    add_price(entered_later, toddlers, full_time, "2600", PricePeriod.MONTHLY)
    make_observation(
        contact=entered_later,
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.TEMPORARILY_CLOSED,
    )

    built = _build(job, cut_off=cut_off)

    entry = _entry(built, "Synthetic Researched")
    assert entry.state_labels == ()
    assert [
        (group.heading, [row.stated_price for row in group.rows])
        for group in entry.verified.price_groups
    ] == [(f"Toddlers (12{EN_DASH}36 months)", ["$2,500 per month"])]
    assert _summary(built)["Research period"] == "Sep 1, 2026 to Sep 2, 2026"


def test_current_values_come_from_any_job_with_their_dates(job, researcher):
    competitor = _listed(job, 0.2, "Synthetic Receiving")
    merged = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.3), name="Synthetic Merged"
    )
    other_job = make_report_job(researcher=researcher)
    for recorded_for, contact_job, contact_date, method, kind, values in (
        (
            competitor,
            None,
            date(2026, 10, 14),
            ContactMethod.CALL,
            ObservationKind.WEEKS_OPEN,
            {"weeks_open_per_year": 50},
        ),
        (
            competitor,
            other_job,
            date(2026, 10, 15),
            ContactMethod.WEBSITE,
            ObservationKind.STATED_PLACES,
            {"stated_places": 40},
        ),
        (
            merged,
            None,
            date(2026, 10, 16),
            ContactMethod.EMAIL,
            ObservationKind.MINIMUM_BOOKING,
            {"days_per_week": 3},
        ),
    ):
        contact = make_contact(
            competitor=recorded_for,
            researcher=researcher,
            job=contact_job,
            contact_date=contact_date,
            method=method,
        )
        make_observation(contact=contact, kind=kind, **values)
    _retire(merged, competitor)

    entry = _entry(_build(job), "Synthetic Receiving")

    assert entry.verified.facts == (
        FactLine("Weeks open per year", "50", "Call, Oct 14, 2026"),
        FactLine("Minimum booking", "3 days per week", "Email, Oct 16, 2026"),
        FactLine(
            "Places, as stated by the provider, not licensed capacity",
            "40",
            "Website, Oct 15, 2026",
        ),
    )


def test_the_summary(job, researcher):
    researched = _listed(job, 0.1, "Synthetic Researched", total_capacity=100)
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Researched",
        kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
        competitor=researched,
        total_capacity=20,
        ny_state_facility_status="License",
    )
    _listed(job, 0.2, "Synthetic Unreached", ResearchStatus.UNREACHABLE)
    in_progress = _listed(
        job, 0.3, "Synthetic In Progress", ResearchStatus.IN_PROGRESS, total_capacity=50
    )
    make_provider(original_name="Synthetic In Progress", competitor=in_progress)
    delisted = _listed(
        job,
        0.4,
        "Synthetic Delisted",
        ResearchStatus.TO_DO,
        kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
        total_capacity=12,
    )
    _delist(delisted)
    closed = _listed(job, 0.5, "Synthetic Closed", total_capacity=60)
    _report_closure(closed, researcher, OperatingStatus.PERMANENTLY_CLOSED)
    _report_closure(in_progress, researcher, OperatingStatus.TEMPORARILY_CLOSED)
    for competitor, status in (
        (researched, AvailabilityStatus.LIMITED_OPENINGS),
        (in_progress, AvailabilityStatus.WAITLIST),
    ):
        contact = make_contact(competitor=competitor, researcher=researcher)
        (age_group,), _ = add_definitions(contact, age_groups=[("Toddlers", 12, 36)])
        make_observation(
            contact=contact,
            kind=ObservationKind.AVAILABILITY,
            age_group=age_group,
            availability_status=status,
        )

    without_contacts = _build(job)
    make_contact(
        competitor=researched, researcher=researcher, job=job, contact_date=OCTOBER_14
    )
    draft = _build(job)
    final = _build(job, ReportKind.FINAL)

    assert [(row.label, row.value) for row in draft.content.summary.rows] == [
        ("Competitors", "4"),
        ("Day care center", "3"),
        ("Group family day care", "1"),
        ("School-age child care", "1"),
        ("Researched", "1"),
        ("Not reached", "1"),
        ("Reported temporarily closed", "1"),
        ("Research in progress", "2"),
        ("Reported permanently closed, listed under Reported closed", "1"),
        ("Total licensed capacity", "170"),
        ("Competitors with unpublished, incomplete, or no listed capacity", "3"),
        ("With openings in at least one age group", "1"),
        ("With a waitlist in at least one age group", "1"),
        ("Research period", "Oct 14, 2026"),
    ]
    assert draft.content.summary.notes == (
        "A competitor whose providers are of several kinds counts under each kind.",
        "Licensed capacity is the regulators' maximum, not enrollment or availability.",
    )
    assert "Research in progress" not in _summary(final)
    assert (
        _summary(without_contacts)["Research period"]
        == "No contacts recorded for this job"
    )


def test_the_overview_and_profile_texts(job, researcher):
    _listed(
        job,
        0.1,
        "Synthetic Large",
        total_capacity=1100,
        nyc_health_program_type="INFANT TODDLER",
        nyc_health_child_age_range="0 YEARS - 2 YEARS",
    )
    incomplete = _listed(job, 0.2, "Synthetic Incomplete", total_capacity=40)
    make_provider(original_name="Synthetic Incomplete", competitor=incomplete)
    _listed(job, 0.3, "Synthetic Unpublished")
    delisted = _listed(
        job,
        0.4,
        "Synthetic Delisted",
        kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
        total_capacity=12,
    )
    _delist(delisted)
    withheld = make_competitor(
        display_name="Synthetic Withheld", location=point_north_of(BOROUGH_HALL, 0.5)
    )
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Withheld",
        street_address="",
        kind=ProviderKind.FAMILY_DAY_CARE,
        competitor=withheld,
        ny_state_facility_status="License",
    )
    unaddressed = make_competitor(
        display_name="Synthetic Unaddressed",
        location=point_north_of(BOROUGH_HALL, 0.6),
    )
    make_provider(
        original_name="Synthetic Unaddressed", street_address="", competitor=unaddressed
    )
    for competitor, miles in ((withheld, 0.5), (unaddressed, 0.6)):
        add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    operating = _listed(job, 0.7, "Synthetic Operating")
    _report_closure(operating, researcher, OperatingStatus.OPERATING)
    verified = _listed(job, 0.8, "Synthetic Verified")
    record_every_value(verified, researcher)

    built = _build(job)

    assert [
        (entry.name, entry.kinds, entry.licensed_capacity, entry.ages_served)
        for entry in built.content.competitors[:4]
    ] == [
        (
            "Synthetic Large",
            "Day care center",
            "1,100",
            "INFANT TODDLER, 0 YEARS - 2 YEARS",
        ),
        ("Synthetic Incomplete", "Day care center", "40 (incomplete)", "Not published"),
        ("Synthetic Unpublished", "Day care center", "Unpublished", "Not published"),
        ("Synthetic Delisted", "Group family day care", "Not listed", "Not listed"),
    ]
    assert _entry(built, "Synthetic Withheld").address == (
        "Address withheld by the regulator"
    )
    assert _entry(built, "Synthetic Unaddressed").address == "Address not published"
    assert _entry(built, "Synthetic Large").address == "100 Example Street"
    assert _entry(built, "Synthetic Large").borough == "Brooklyn"
    operating_entry = _entry(built, "Synthetic Operating")
    assert operating_entry.research_outcome == "Researched, no verified information"
    assert operating_entry.verified is None
    data = _entry(built, "Synthetic Verified").verified
    recorded = "Call, Oct 14, 2026"
    assert data.age_groups == (
        ValueLine(f"Infants: 6{EN_DASH}18 months", recorded),
        ValueLine("Preschool: 36 months and older", recorded),
    )
    assert data.schedules == (
        ValueLine("Full time: 5 days per week, 10 hours per day", recorded),
        ValueLine("Part time: 3 days per week", recorded),
        ValueLine("Half day: 1 day per week, 1 hour per day", recorded),
    )
    assert data.price_groups == (
        PriceGroup(
            f"Infants (6{EN_DASH}18 months)",
            (
                PriceRow("Full time", "$2,450 per month", "about $2,450", recorded),
                PriceRow("Part time", "$62.50 per day*", "about $813", recorded),
            ),
        ),
        PriceGroup(
            "Preschool (36 months and older)",
            (
                PriceRow("Part time", "$15 per hour", "not computable", recorded),
                PriceRow("Half day", "$20 per hour", "about $87", recorded),
            ),
        ),
    )
    assert data.availability == (
        ValueLine(
            "Infants: Waitlist; earliest start Jan 5, 2027; expected wait 3 months",
            recorded,
        ),
        ValueLine("Preschool: Openings", recorded),
    )
    assert [line.text for line in data.discounts] == [
        "Sibling: 10%",
        "Multi-day: $50 (two or more days)",
        "Upfront payment: None offered",
        "Referral: Offered",
        "Other (synthetic loyalty offer)",
    ]
    assert [line.text for line in data.other_charges] == [
        "Registration: $150 once",
        "Deposit: 1 month of tuition",
        "Late pickup: $1.50 per hour",
        "Other: Charged",
    ]
    assert [(fact.label, fact.value) for fact in data.facts] == [
        ("Weekday hours", f"7:30 AM{EN_DASH}6:00 PM"),
        ("Weeks open per year", "50"),
        ("Minimum booking", "2 days per week"),
        ("Last fee rise", "March 2026"),
        ("Places, as stated by the provider, not licensed capacity", "1,200"),
        ("Inspection status, as stated", "Passed, inspected Jun 2, 2026"),
        ("Parking", "Street only (metered)"),
        ("Offers 3-K for All seats", "Yes"),
        ("Offers Pre-K for All seats", "No"),
        ("Accepts child care vouchers", "Yes"),
        ("Website address", "https://example.com/synthetic"),
        ("Description or unique selling point", "Synthetic garden & <b>play</b>"),
    ]
    assert {fact.recorded for fact in data.facts} == {recorded}


def test_the_sources_and_cover_texts(job, researcher):
    _listed(job, 0.2, "Synthetic Only")

    unloaded = _build(job).content

    assert unloaded.sources.ny_state_as_of == "not loaded"
    assert unloaded.sources.nyc_health_as_of == "not loaded"
    assert unloaded.sources.census_line == (
        "US Census Bureau, American Community Survey 5-year estimates: no figures "
        "shown in this report."
    )
    assert unloaded.cover_line == (
        "1 competitor near the target location; search radius 1.00 mi"
    )

    make_census_2024(make_area("11201", BOROUGH_HALL))
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS,
        finished_at=datetime(2026, 1, 13, 12, tzinfo=NEW_YORK),
        publisher_rows_updated_at=datetime(2026, 1, 12, 12, tzinfo=NEW_YORK),
    )
    make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS,
        finished_at=datetime(2026, 1, 14, 12, tzinfo=NEW_YORK),
    )
    _listed(job, 0.3, "Synthetic Second")

    loaded = _build(job).content

    assert loaded.sources.ny_state_as_of == "published data as of Jan 12, 2026"
    assert loaded.sources.nyc_health_as_of == "loaded on Jan 14, 2026"
    assert loaded.sources.census_line == (
        "US Census Bureau, American Community Survey 5-year estimates, "
        f"2020{EN_DASH}2024, tables DP02 to DP05."
    )
    assert loaded.cover_line == (
        "2 competitors near the target location in ZIP Code Tabulation Area 11201; "
        "search radius 1.00 mi"
    )


def _add_researched_competitors(job, researcher, count, first_miles):
    competitors = []
    for index in range(count):
        miles = first_miles + 0.05 * index
        competitor = _listed(job, miles, f"Synthetic Competitor {miles:.2f}")
        make_provider(
            source=ProviderSource.NY_STATE,
            original_name=f"Synthetic Competitor {miles:.2f}",
            kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
            competitor=competitor,
            ny_state_facility_status="Suspended",
        )
        record_every_value(competitor, researcher)
        competitors.append(competitor)
    return competitors


def test_building_runs_a_fixed_number_of_queries(
    job, researcher, django_assert_num_queries
):
    make_census_2024(make_area("11201", BOROUGH_HALL))
    _add_researched_competitors(job, researcher, 2, 0.1)
    _build(job)
    with CaptureQueriesContext(connection) as small_report:
        _build(job)

    receiving, *_ = _add_researched_competitors(job, researcher, 5, 0.3)
    merged = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.9), name="Synthetic Merged"
    )
    record_every_value(merged, researcher)
    _retire(merged, receiving)

    with django_assert_num_queries(len(small_report.captured_queries)):
        _build(job)
