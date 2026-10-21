"""Tests for what a teaser shows, built from public data as of its cut-off."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from axisence.ingestion.models import IngestionSource
from axisence.providers.models import Competitor, Provider, ProviderKind, ProviderSource
from axisence.reports.figure_lists import FIGURE_LISTS, TEASER_FIGURE_CODES
from axisence.reports.formatting import EN_DASH, PLUS_MINUS
from axisence.reports.rendering import render_teaser_html
from axisence.reports.schematic_map import MAP_HEIGHT, MAP_WIDTH
from axisence.reports.teaser_content import ContactDetails, KindCount, build_teaser
from axisence.research.models import (
    AvailabilityStatus,
    ObservationKind,
    OperatingStatus,
    PricePeriod,
    ResearchStatus,
)
from tests.providers.factories import make_provider, make_researcher
from tests.reports.factories import (
    add_candidate,
    add_definitions,
    add_job_competitor,
    add_price,
    make_area,
    make_census_2024,
    make_prospect_job,
    make_provider_run,
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

NEW_YORK = ZoneInfo("America/New_York")
# A point in the harbor, outside every synthetic tabulation area.
OUTSIDE_EVERY_AREA = Point(-74.0450, 40.6200, srid=4326)
SYNTHETIC_CONTACT = ContactDetails(
    website_url="https://axisence.example/previews",
    email="previews@axisence.example",
    email_url="mailto:previews@axisence.example",
    phone="(555) 010-4400",
)


@pytest.fixture
def researcher():
    return make_researcher()


def _build(job, cut_off=None):
    return build_teaser(job=job, cut_off=cut_off or timezone.now())


def _retire(competitor, receiver):
    Provider.objects.filter(competitor=competitor).update(competitor=receiver)
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiver, is_active=False
    )


def _delist(competitor):
    Provider.objects.filter(competitor=competitor).update(missing_since=timezone.now())
    Competitor.objects.filter(pk=competitor.pk).update(is_active=False)


def _report_status(competitor, researcher, status, *, entered_at):
    make_observation(
        contact=make_contact(
            competitor=competitor, researcher=researcher, entered_at=entered_at
        ),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=status,
    )


def test_counted_competitors_are_the_search_candidates_without_permanent_closures(
    researcher,
):
    cut_off = timezone.now()
    own = add_candidate(0.05, "Synthetic Own Program")
    job = make_prospect_job(researcher=researcher, own_competitor=own)
    near = add_candidate(0.1, "Synthetic Near")
    # Created in this order, so the tie puts the first one first by its lower number.
    add_candidate(0.3, "Synthetic Tied First")
    add_candidate(0.3, "Synthetic Tied Second")
    closed_later = add_candidate(0.6, "Synthetic Closed Later")
    _report_status(
        closed_later,
        researcher,
        OperatingStatus.PERMANENTLY_CLOSED,
        entered_at=cut_off + timedelta(hours=1),
    )
    temporarily_closed = add_candidate(0.7, "Synthetic Temporarily Closed")
    _report_status(
        temporarily_closed,
        researcher,
        OperatingStatus.TEMPORARILY_CLOSED,
        entered_at=cut_off - timedelta(hours=1),
    )
    priced = add_candidate(0.9, "Synthetic Priced")
    contact = make_contact(
        competitor=priced,
        researcher=researcher,
        entered_at=cut_off - timedelta(hours=1),
    )
    (infants,), (full_time,) = add_definitions(
        contact, age_groups=[("Infants", 6, 18)], schedules=[("Full time", 5, None)]
    )
    add_price(contact, infants, full_time, "7777.77", PricePeriod.MONTHLY)
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.OPENINGS,
    )

    add_candidate(1.2, "Synthetic Outside")
    add_candidate(0.4, "Synthetic Family Home", kind=ProviderKind.FAMILY_DAY_CARE)
    closed = add_candidate(0.5, "Synthetic Closed")
    _report_status(
        closed,
        researcher,
        OperatingStatus.PERMANENTLY_CLOSED,
        entered_at=cut_off - timedelta(hours=1),
    )
    _delist(add_candidate(0.8, "Synthetic Delisted"))
    receiving = add_candidate(0.95, "Synthetic Receiving")
    retired = add_candidate(0.96, "Synthetic Retired")
    _report_status(
        retired,
        researcher,
        OperatingStatus.PERMANENTLY_CLOSED,
        entered_at=cut_off - timedelta(hours=1),
    )
    _retire(retired, receiving)

    add_job_competitor(
        job,
        add_candidate(3.0, "Synthetic Selected Far"),
        distance_meters=3.0 * METERS_PER_MILE,
    )
    add_job_competitor(
        job,
        near,
        distance_meters=0.1 * METERS_PER_MILE,
        research_status=ResearchStatus.UNREACHABLE,
    )

    with CaptureQueriesContext(connection) as queries:
        built = _build(job, cut_off)

    content = built.content
    assert [
        (competitor.number, competitor.name, competitor.distance)
        for competitor in content.named_competitors
    ] == [
        (1, "Synthetic Near", "0.10 mi"),
        (2, "Synthetic Tied First", "0.30 mi"),
        (3, "Synthetic Tied Second", "0.30 mi"),
        (4, "Synthetic Closed Later", "0.60 mi"),
        (5, "Synthetic Temporarily Closed", "0.70 mi"),
        (6, "Synthetic Priced", "0.90 mi"),
    ]
    assert content.summary.headline == ("6 competitors within 1.00 mi of your location")
    statements = [query["sql"] for query in queries.captured_queries]
    assert not any('"research_jobcompetitor"' in sql for sql in statements)
    # The labels' operating-status query.
    assert sum('"research_observation"' in sql for sql in statements) == 1


@pytest.mark.parametrize(
    ("count", "headline", "more_line"),
    [
        (1, "1 competitor within 1.00 mi of your location", ""),
        (8, "8 competitors within 1.00 mi of your location", ""),
        (
            9,
            "9 competitors within 1.00 mi of your location",
            "1 more competitor within the radius appears as a dot on the map.",
        ),
        (
            10,
            "10 competitors within 1.00 mi of your location",
            "2 more competitors within the radius appear as dots on the map.",
        ),
    ],
)
def test_the_named_list_the_more_line_and_the_map(
    researcher, count, headline, more_line
):
    job = make_prospect_job(researcher=researcher)
    for index in range(count):
        add_candidate(0.05 + index * 0.08, f"Synthetic Competitor {index + 1:02d}")

    content = _build(job).content

    named_count = min(count, 8)
    numbers = [competitor.number for competitor in content.named_competitors]
    assert numbers == list(range(1, named_count + 1))
    assert content.summary.headline == headline
    assert content.more_competitors_line == more_line
    market_map = content.market_map
    assert [marker.number for marker in market_map.markers] == numbers
    assert len(market_map.dots) == max(count - 8, 0)
    for point in (*market_map.markers, *market_map.dots):
        assert 0 <= point.x <= MAP_WIDTH
        assert 0 <= point.y <= MAP_HEIGHT


def test_competitor_texts_counts_and_capacity(researcher):
    job = make_prospect_job(researcher=researcher)
    add_candidate(0.1, "Synthetic Center", total_capacity=40)
    with_school_age = add_candidate(
        0.2, "Synthetic Center With School-Age Care", total_capacity=30
    )
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Center With School-Age Care",
        kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
        competitor=with_school_age,
    )
    school = add_candidate(
        0.3, "Synthetic School Program", kind=ProviderKind.SCHOOL_BASED_CHILD_CARE
    )
    Competitor.objects.filter(pk=school.pk).update(is_school_based=True)
    # A second kind of the job, so the school program counts under two of them.
    make_provider(
        original_name="Synthetic School Program",
        kind=ProviderKind.DAY_CARE_CENTER,
        competitor=school,
    )
    unnamed = add_candidate(0.4, "Synthetic Unnamed", total_capacity=12)
    Competitor.objects.filter(pk=unnamed.pk).update(display_name="")

    content = _build(job).content

    assert [
        (
            competitor.name,
            competitor.is_school_based,
            competitor.kinds,
            competitor.licensed_capacity,
        )
        for competitor in content.named_competitors
    ] == [
        ("Synthetic Center", False, "Day care center", "40"),
        (
            "Synthetic Center With School-Age Care",
            False,
            "Day care center, School-age child care",
            "30 (incomplete)",
        ),
        (
            "Synthetic School Program",
            True,
            "Day care center, School-based child care",
            "Unpublished",
        ),
        ("Name not published", False, "Day care center", "12"),
    ]
    summary = content.summary
    # Only the job's kinds, zeros included: the school-age program is not counted.
    assert summary.kind_counts == (
        KindCount("Day care center", "4"),
        KindCount("Small day care center", "0"),
        KindCount("School-based child care", "1"),
    )
    assert summary.several_kinds_note == (
        "A competitor whose providers are of several kinds counts under each kind."
    )
    assert summary.total_capacity == "Total licensed capacity: 82"
    assert summary.incomplete_capacity == (
        "Competitors with unpublished or incomplete capacity: 2"
    )
    assert summary.capacity_note == (
        "Licensed capacity is the regulators' maximum, not enrollment or availability."
    )
    assert summary.kinds_counted == (
        "Kinds counted: Day care center, Small day care center, School-based child "
        "care."
    )
    assert summary.own_program_line == ""

    school_job = make_prospect_job(
        researcher=researcher,
        provider_kinds=["school_based_child_care", "small_day_care_center"],
    )

    school_content = _build(school_job).content

    assert school_content.summary.kinds_counted == (
        "Kinds counted: Small day care center, School-based child care."
    )
    assert [competitor.name for competitor in school_content.named_competitors] == [
        "Synthetic School Program"
    ]
    assert school_content.summary.kind_counts == (
        KindCount("Small day care center", "0"),
        KindCount("School-based child care", "1"),
    )
    # The school program's day care center is not one of this job's kinds.
    assert school_content.summary.several_kinds_note == ""


def test_the_own_program_line_shows_no_fact_of_the_own_program(researcher):
    own = add_candidate(0.1, "Synthetic Own Program Marker 4431", total_capacity=4321)
    job = make_prospect_job(researcher=researcher, own_competitor=own)
    add_candidate(0.3, "Synthetic Other Program")
    without_own = make_prospect_job(researcher=researcher)

    content = _build(job).content
    html = render_teaser_html(content, number=41, contact=SYNTHETIC_CONTACT)

    assert content.summary.own_program_line == "Excludes your own program."
    assert "Synthetic Own Program Marker 4431" not in html
    assert "4321" not in html
    assert "4,321" not in html
    assert _build(without_own).content.summary.own_program_line == ""


def test_the_headline_figures(researcher):
    make_census_2024(
        make_area("11201", BOROUGH_HALL),
        values={"DP02_0054": {"estimate": None, "estimate_margin_of_error": None}},
    )
    job = make_prospect_job(researcher=researcher)
    add_candidate(0.2)

    built = _build(job)

    demographics = built.content.demographics
    assert demographics.heading == (
        "ZIP Code Tabulation Area 11201: American Community Survey 5-year estimates, "
        f"2020{EN_DASH}2024, tables DP02 to DP05"
    )
    assert demographics.caveat == (
        "ZIP Code Tabulation Area 11201 is the Census Bureau's approximation of the "
        "zip code area; it is not the zip code itself."
    )
    assert demographics.message == ""
    (group,) = demographics.groups
    assert group.heading == ""
    assert [(row.label, row.estimate, row.percent) for row in group.rows] == [
        (
            "Children under 5 years (Census age bracket)",
            f"3,036 {PLUS_MINUS} 526",
            f"7.0% {PLUS_MINUS} 1.1 of total population",
        ),
        (
            "With all parents in the family in the labor force",
            "",
            f"87.8% {PLUS_MINUS} 4.9 of own children under 6 years",
        ),
        (
            "Households with one or more people under 18 years",
            "",
            f"25.0% {PLUS_MINUS} 2.0 of households",
        ),
        ("Enrolled in nursery school or preschool", "Not published", ""),
        (
            "Median household income (2024 dollars)",
            f"$162,863 {PLUS_MINUS} $12,941",
            "",
        ),
    ]
    assert built.census_vintage == 2024
    assert built.tabulation_area_code == "11201"
    assert built.content.sources.census_line == (
        "US Census Bureau, American Community Survey 5-year estimates, "
        f"2020{EN_DASH}2024, tables DP02 to DP05."
    )


def test_a_coded_median(researcher):
    make_census_2024(
        make_area("11201", BOROUGH_HALL),
        values={
            "DP03_0062": {
                "estimate": Decimal(250001),
                "estimate_margin_of_error": None,
            }
        },
    )
    job = make_prospect_job(researcher=researcher)
    add_candidate(0.2)

    (group,) = _build(job).content.demographics.groups

    assert group.rows[-1].estimate == "$250,000 or more"


@pytest.mark.parametrize("case", ["no vintage", "outside"])
def test_demographics_without_figures(researcher, case):
    if case == "no vintage":
        make_area("11201", BOROUGH_HALL)
        job = make_prospect_job(researcher=researcher)
        add_candidate(0.2)
        message = "No Census figures are loaded."
    else:
        make_census_2024(make_area("11201", BOROUGH_HALL))
        job = make_prospect_job(researcher=researcher, target_point=OUTSIDE_EVERY_AREA)
        make_located_competitor(point_north_of(OUTSIDE_EVERY_AREA, 0.2))
        message = (
            "Your location lies outside every ZIP Code Tabulation Area, so no Census "
            "figures are shown."
        )

    built = _build(job)

    assert built.content.demographics.message == message
    assert built.content.demographics.groups == ()
    assert built.census_vintage is None
    assert built.tabulation_area_code == ("11201" if case == "no vintage" else "")
    assert built.content.sources.census_line == (
        "US Census Bureau, American Community Survey 5-year estimates: no figures "
        "shown in this preview."
    )


def test_the_teaser_figure_codes_are_declared_for_every_vintage():
    assert TEASER_FIGURE_CODES.keys() == FIGURE_LISTS.keys()
    for vintage, codes in TEASER_FIGURE_CODES.items():
        listed = {definition.code for definition in FIGURE_LISTS[vintage]}
        assert len(codes) == 5
        assert len(set(codes)) == 5
        assert set(codes) <= listed


def _at_noon(month, day):
    return datetime(2026, month, day, 12, tzinfo=NEW_YORK)


RUN_CASES = (
    ("none", (), ("not loaded", "not loaded", "")),
    (
        "both",
        (
            (IngestionSource.NY_STATE_PROVIDERS, _at_noon(1, 13), _at_noon(1, 12)),
            (IngestionSource.NYC_HEALTH_PROVIDERS, _at_noon(1, 14), None),
        ),
        ("published data as of Jan 12, 2026", "loaded on Jan 14, 2026", "Jan 12, 2026"),
    ),
    (
        "city earlier",
        (
            (IngestionSource.NY_STATE_PROVIDERS, _at_noon(3, 3), _at_noon(3, 2)),
            (IngestionSource.NYC_HEALTH_PROVIDERS, _at_noon(1, 14), None),
        ),
        ("published data as of Mar 2, 2026", "loaded on Jan 14, 2026", "Jan 14, 2026"),
    ),
    (
        "one source",
        ((IngestionSource.NYC_HEALTH_PROVIDERS, _at_noon(1, 14), None),),
        ("not loaded", "loaded on Jan 14, 2026", ""),
    ),
)


@pytest.mark.parametrize(
    ("runs", "expected"),
    [(runs, expected) for _, runs, expected in RUN_CASES],
    ids=[name for name, *_ in RUN_CASES],
)
def test_the_header_small_print_and_footer_dates(researcher, runs, expected):
    for source, finished_at, publisher_rows_updated_at in runs:
        make_provider_run(
            source,
            finished_at=finished_at,
            publisher_rows_updated_at=publisher_rows_updated_at,
        )
    job = make_prospect_job(researcher=researcher)
    add_candidate(0.2)

    content = _build(job, cut_off=datetime(2026, 10, 21, 2, tzinfo=UTC)).content

    assert (
        content.sources.ny_state_as_of,
        content.sources.nyc_health_as_of,
        content.public_data_as_of,
    ) == expected
    assert content.preview_date == "October 20, 2026"
    assert content.organization_name == "Synthetic Prospect"


@pytest.mark.parametrize(
    ("provider_kinds", "candidate_kind", "expected"),
    [
        (None, ProviderKind.DAY_CARE_CENTER, ""),
        (
            ["day_care_center", "family_day_care"],
            ProviderKind.DAY_CARE_CENTER,
            "Programs whose address the regulator withholds are not counted.",
        ),
        (
            ["group_family_day_care"],
            ProviderKind.GROUP_FAMILY_DAY_CARE,
            "Programs whose address the regulator withholds are not counted.",
        ),
    ],
    ids=["default kinds", "family day care", "group family day care"],
)
def test_the_withheld_address_line_follows_the_job_kinds(
    researcher, provider_kinds, candidate_kind, expected
):
    if provider_kinds is None:
        job = make_prospect_job(researcher=researcher)
    else:
        job = make_prospect_job(researcher=researcher, provider_kinds=provider_kinds)
    add_candidate(0.2, kind=candidate_kind)

    assert _build(job).content.sources.withheld_address_line == expected


def test_building_runs_a_fixed_number_of_queries(researcher, django_assert_num_queries):
    make_census_2024(make_area("11201", BOROUGH_HALL))
    now = timezone.now()
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS, finished_at=now - timedelta(days=1)
    )
    make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    job = make_prospect_job(researcher=researcher)
    add_candidate(0.1, "Synthetic First")
    add_candidate(0.15, "Synthetic Second")
    _build(job)
    with CaptureQueriesContext(connection) as small_teaser:
        _build(job)

    added = [
        add_candidate(0.2 + index * 0.05, f"Synthetic Added {index + 1:02d}")
        for index in range(12)
    ]
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Added 01",
        kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
        competitor=added[0],
    )
    _report_status(
        added[1],
        researcher,
        OperatingStatus.PERMANENTLY_CLOSED,
        entered_at=now - timedelta(hours=1),
    )
    merged = add_candidate(0.9, "Synthetic Merged")
    _report_status(
        merged,
        researcher,
        OperatingStatus.PERMANENTLY_CLOSED,
        entered_at=now - timedelta(hours=1),
    )
    _retire(merged, added[2])

    with django_assert_num_queries(len(small_teaser.captured_queries)):
        _build(job)
