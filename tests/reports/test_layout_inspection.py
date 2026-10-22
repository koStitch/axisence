"""Renders the reports and teasers inspected page by page into
scratch/report-inspection/. Run only with -m layout_inspection; the PDFs remain after
the run.
"""

import math
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.utils import timezone

from axisence.demographics.models import TabulationArea
from axisence.ingestion.models import IngestionSource
from axisence.providers.models import (
    Competitor,
    Provider,
    ProviderKind,
    ProviderSource,
)
from axisence.reports import generation
from axisence.research import contacts
from axisence.research.distances import distances_from
from axisence.research.models import (
    AvailabilityStatus,
    ChargeFrequency,
    ChargeKind,
    ContactMethod,
    DiscountKind,
    JobStatus,
    Observation,
    ObservationKind,
    OperatingStatus,
    PricePeriod,
    ResearchStatus,
)
from axisence.transit.combining import natural_order_key
from axisence.transit.models import TransitMode
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.reports.factories import (
    add_definitions,
    add_job_competitor,
    add_price,
    make_census_2024,
    make_prospect_job,
    make_provider_run,
    make_report_job,
    record_every_value,
    set_contact_settings,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_DEGREE_OF_LATITUDE,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
)
from tests.transit.factories import make_transit_line, make_transit_stop

pytestmark = pytest.mark.django_db

LONG_NAME = (
    "Synthetic Very Long Named Early Learning Center and Family Enrichment Academy "
    "of Brooklyn Heights"
)
# A point in the harbor, far from every synthetic tabulation area.
OUTSIDE_EVERY_AREA = Point(-74.0450, 40.6200, srid=4326)
GRID_CODES = (
    ("11231", "11205", "11238"),
    ("11215", "11201", "11217"),
    ("11232", "11220", "11218"),
)
# Moves each shared corner of the grid a little, so the areas are irregular.
CORNER_SHIFTS = (0.0, 0.12, -0.08, 0.05)
# The subway's route_sort_order of each line the synthetic stations serve.
SUBWAY_LINE_POSITIONS = (
    ("B", 4),
    ("D", 5),
    ("N", 13),
    ("Q", 14),
    ("R", 15),
    ("W", 16),
    ("2", 21),
    ("3", 22),
    ("4", 23),
    ("5", 24),
)
ALL_SUBWAY_LINES = tuple(name for name, _ in SUBWAY_LINE_POSITIONS)
# Each group: its name, distance, bearing, and the lines of each member.
STATION_GROUPS = (
    (
        "Synthetic Atlantic Av-Barclays Ctr-Flatbush Av Interchange",
        0.12,
        40,
        (("2", "3", "4", "5"), ("B", "D", "N", "Q", "R", "W")),
    ),
    (
        "Synthetic Borough Hall-Court St-Joralemon St Transfer Complex",
        0.2,
        200,
        (("2", "3", "4", "5"), ("N", "R", "W")),
    ),
    ("Synthetic Jay St-MetroTech", 0.3, 100, (("B", "D", "N", "Q", "R"),)),
    ("Synthetic DeKalb Av", 0.42, 300, (("B", "Q", "R"),)),
    ("Synthetic Hoyt-Schermerhorn Sts", 0.5, 160, (("2", "3"),)),
    ("Synthetic Clark St-Brooklyn Heights Promenade", 0.58, 330, (ALL_SUBWAY_LINES,)),
)
BUS_LINE_NAMES = (
    "B25",
    "B26",
    "B38",
    "B41",
    "B45",
    "B52",
    "B54",
    "B57",
    "B61",
    "B62",
    "B63",
    "B65",
    "B67",
    "B103",
    "B44-SBS",
    "B46-SBS",
    "BM1",
    "BM2",
)


def _point_at(origin, miles, bearing_degrees):
    meters = miles * METERS_PER_MILE
    bearing = math.radians(bearing_degrees)
    return Point(
        origin.x
        + meters
        * math.sin(bearing)
        / (METERS_PER_DEGREE_OF_LATITUDE * math.cos(math.radians(origin.y))),
        origin.y + meters * math.cos(bearing) / METERS_PER_DEGREE_OF_LATITUDE,
        srid=4326,
    )


def _make_area_grid():
    """Tile irregular synthetic areas around the target, 11201 containing it."""
    step = 0.018
    # The grid is off center, so the target does not hide its area's code.
    west_edge = BOROUGH_HALL.x - 1.3 * step
    north_edge = BOROUGH_HALL.y + 1.6 * step

    def corner(column, row):
        shift = CORNER_SHIFTS[(column * 3 + row) % len(CORNER_SHIFTS)] * step
        return (west_edge + column * step + shift, north_edge - row * step - shift)

    for row, codes in enumerate(GRID_CODES):
        for column, code in enumerate(codes):
            ring = (
                corner(column, row + 1),
                corner(column + 1, row + 1),
                corner(column + 1, row),
                corner(column, row),
                corner(column, row + 1),
            )
            TabulationArea.objects.create(
                code=code, boundary=MultiPolygon(Polygon(ring), srid=4326)
            )


def _listed(job, miles, bearing, name, research_status=ResearchStatus.DONE, **fields):
    competitor = make_located_competitor(
        _point_at(job.target_point, miles, bearing), name=name, **fields
    )
    add_job_competitor(
        job,
        competitor,
        distance_meters=miles * METERS_PER_MILE,
        research_status=research_status,
    )
    return competitor


def _report_status(competitor, researcher, status, contact_date):
    make_observation(
        contact=make_contact(
            competitor=competitor, researcher=researcher, contact_date=contact_date
        ),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=status,
    )


def _price_competitor(competitor, researcher, job, monthly):
    contact = make_contact(
        competitor=competitor,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 10, 7),
    )
    (infants, toddlers, preschool), (full_time, part_time) = add_definitions(
        contact,
        age_groups=[
            ("Infants", 6, 18),
            ("Toddlers", 12, 36),
            ("Preschool", 36, 60),
        ],
        schedules=[("Full time", 5, "10"), ("Part time", 3, None)],
    )
    for age_group, offset in ((infants, 400), (toddlers, 200), (preschool, 0)):
        add_price(
            contact, age_group, full_time, str(monthly + offset), PricePeriod.MONTHLY
        )
        add_price(
            contact,
            age_group,
            part_time,
            str((monthly + offset) // 20),
            PricePeriod.DAILY,
        )
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=toddlers,
        availability_status=AvailabilityStatus.WAITLIST,
        expected_wait_months=4,
    )


def _final_every_state_job(researcher):
    job = make_report_job(
        researcher=researcher,
        status=JobStatus.COMPLETE,
        organization_name="Synthetic Inspection Client",
    )
    every_value = _listed(job, 0.15, 30, LONG_NAME, total_capacity=82)
    record_every_value(every_value, researcher, contact_date=date(2026, 10, 14))
    retiring = make_contact(
        competitor=every_value,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 10, 16),
    )
    (retired_group,), (retired_schedule,) = add_definitions(
        retiring,
        age_groups=[("Twos", 24, 36)],
        schedules=[("Evenings", 2, "3")],
    )
    later = make_contact(
        competitor=every_value,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 10, 17),
        method=ContactMethod.WEBSITE,
    )
    make_observation(
        contact=later,
        kind=ObservationKind.AGE_GROUP_RETIREMENT,
        age_group=retired_group,
    )
    make_observation(
        contact=later,
        kind=ObservationKind.SCHEDULE_RETIREMENT,
        schedule=retired_schedule,
    )

    overlapping = _listed(job, 0.25, 120, "Synthetic Overlapping Ages Center")
    _price_competitor(overlapping, researcher, job, 2100)

    temporarily_closed = _listed(job, 0.3, 200, "Synthetic Temporarily Closed")
    _report_status(
        temporarily_closed,
        researcher,
        OperatingStatus.TEMPORARILY_CLOSED,
        date(2026, 10, 9),
    )

    delisted = _listed(
        job,
        0.35,
        260,
        "Synthetic No Longer Listed Family Day Care",
        kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
        total_capacity=12,
    )
    Provider.objects.filter(competitor=delisted).update(missing_since=timezone.now())
    Competitor.objects.filter(pk=delisted.pk).update(is_active=False)

    _listed(
        job,
        0.4,
        320,
        "Synthetic Unreachable Center",
        research_status=ResearchStatus.UNREACHABLE,
    )

    outside = make_located_competitor(
        _point_at(BOROUGH_HALL, 1.3, 70), name="Synthetic Outside the Radius"
    )
    add_job_competitor(job, outside, distance_meters=1.3 * METERS_PER_MILE)

    school = _listed(
        job,
        0.5,
        160,
        "Synthetic School-Based Program",
        kind=ProviderKind.SCHOOL_BASED_CHILD_CARE,
    )
    Competitor.objects.filter(pk=school.pk).update(is_school_based=True)

    revocation = _listed(job, 0.55, 100, "Synthetic Pending Revocation Center")
    Provider.objects.filter(competitor=revocation).update(
        source=ProviderSource.NY_STATE, ny_state_facility_status="Pending Revocation"
    )

    withheld = make_competitor(
        display_name="Synthetic Address Withheld Home",
        location=_point_at(BOROUGH_HALL, 0.6, 220),
    )
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Address Withheld Home",
        street_address="",
        kind=ProviderKind.FAMILY_DAY_CARE,
        competitor=withheld,
        total_capacity=8,
        ny_state_facility_status="License",
    )
    add_job_competitor(job, withheld, distance_meters=0.6 * METERS_PER_MILE)

    receiving = _listed(job, 0.65, 10, "Synthetic Receiving Center")
    merged = make_located_competitor(
        _point_at(BOROUGH_HALL, 0.66, 12), name="Synthetic Merged Center"
    )
    _price_competitor(merged, researcher, job, 1800)
    Provider.objects.filter(competitor=merged).update(competitor=receiving)
    Competitor.objects.filter(pk=merged.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )

    for miles, bearing, name in (
        (0.45, 290, "Synthetic Permanently Closed Center"),
        (0.7, 140, "Synthetic Second Permanently Closed Center"),
    ):
        closed = _listed(job, miles, bearing, name, total_capacity=40)
        _report_status(
            closed, researcher, OperatingStatus.PERMANENTLY_CLOSED, date(2026, 9, 28)
        )

    hourly = _listed(job, 0.75, 240, "Synthetic Hourly Only")
    contact = make_contact(
        competitor=hourly,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 10, 2),
        method=ContactMethod.EMAIL,
    )
    (toddlers,), (drop_in,) = add_definitions(
        contact,
        age_groups=[("Toddlers", 18, 36)],
        schedules=[("Drop-in", 5, None)],
    )
    add_price(
        contact, toddlers, drop_in, "18", PricePeriod.HOURLY, includes_discounts=True
    )

    unlocated = make_competitor(
        display_name="Synthetic Unlocated Center",
        street_address="100 Example Street",
        location=None,
    )
    make_provider(original_name="Synthetic Unlocated Center", competitor=unlocated)
    add_job_competitor(job, unlocated, distance_meters=None)
    return job


def _make_transit_network():
    """Store a dense synthetic network around Borough Hall: six station groups within
    0.6 mi, two of them of two members a few meters apart, with long names and up to
    ten lines, and eighteen bus lines on a ring of sixteen stops 0.05 to 0.2 mi away.
    """
    subway_lines = {
        name: make_transit_line(TransitMode.SUBWAY, name, position)
        for name, position in SUBWAY_LINE_POSITIONS
    }
    for index, (name, miles, bearing, member_lines) in enumerate(STATION_GROUPS):
        group_key = "+".join(f"S{index}{member}" for member in range(len(member_lines)))
        point = _point_at(BOROUGH_HALL, miles, bearing)
        for member, line_names in enumerate(member_lines):
            make_transit_stop(
                mode=TransitMode.SUBWAY,
                published_stop_id=f"S{index}{member}",
                # The second member lies about 6 meters east of the first.
                point=_point_at(point, 0.004 * member, 90),
                name=name,
                lines=[subway_lines[line_name] for line_name in line_names],
                station_group_key=group_key,
            )
    bus_lines = [
        make_transit_line(TransitMode.BUS, name, position)
        for position, name in enumerate(
            sorted(BUS_LINE_NAMES, key=natural_order_key), start=1
        )
    ]
    for index in range(16):
        make_transit_stop(
            mode=TransitMode.BUS,
            published_stop_id=f"9{index:05d}",
            point=_point_at(BOROUGH_HALL, 0.05 + 0.01 * index, index * 22.5),
            lines=[
                bus_lines[(index + offset) % len(bus_lines)]
                for offset in (0, 2, 5, 9, 13)
            ],
        )


def _draft_in_progress_job(researcher):
    job = make_report_job(
        researcher=researcher, organization_name="Synthetic Draft Client"
    )
    for index, (status, miles) in enumerate(
        (
            (ResearchStatus.DONE, 0.2),
            (ResearchStatus.IN_PROGRESS, 0.3),
            (ResearchStatus.TO_DO, 0.45),
            (ResearchStatus.TO_DO, 0.6),
        )
    ):
        competitor = _listed(
            job,
            miles,
            index * 85,
            f"Synthetic Draft Competitor {index + 1}",
            research_status=status,
            total_capacity=30 + index * 15,
        )
        if status == ResearchStatus.DONE:
            _price_competitor(competitor, researcher, job, 1950)
    return job


def _many_competitors_job(researcher):
    job = make_report_job(
        researcher=researcher,
        organization_name="Synthetic Large Market Client",
        search_radius_miles=Decimal("1.50"),
    )
    for index in range(30):
        competitor = _listed(
            job,
            0.08 + index * 0.045,
            index * 47,
            f"Synthetic Competitor {index + 1:02d}",
            total_capacity=20 + index * 7,
        )
        _price_competitor(competitor, researcher, job, 1500 + index * 35)
    return job


def _outside_every_area_job(researcher):
    job = make_report_job(
        researcher=researcher,
        organization_name="Synthetic Harbor Client",
        target_point=OUTSIDE_EVERY_AREA,
    )
    for index, miles in enumerate((0.3, 0.6)):
        _listed(job, miles, 90 + index * 60, f"Synthetic Harbor Center {index + 1}")
    return job


@pytest.mark.layout_inspection
def test_render_the_inspection_reports(settings):
    researcher = make_researcher()
    _make_area_grid()
    make_census_2024(
        TabulationArea.objects.get(code="11201"),
        values={
            "DP03_0062": {
                "estimate": Decimal(250001),
                "estimate_margin_of_error": None,
            },
            "DP03_0086": {"estimate": Decimal(2499), "estimate_margin_of_error": None},
            "DP02_0054": {"estimate": None, "estimate_margin_of_error": None},
        },
    )
    now = timezone.now()
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS,
        finished_at=now - timedelta(days=1),
        publisher_rows_updated_at=now - timedelta(days=1, hours=6),
    )
    make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    _make_transit_network()
    target_directory = Path(settings.BASE_DIR) / "scratch" / "report-inspection"
    target_directory.mkdir(parents=True, exist_ok=True)

    for name, job in (
        ("final-every-state", _final_every_state_job(researcher)),
        ("draft-in-progress", _draft_in_progress_job(researcher)),
        ("many-competitors", _many_competitors_job(researcher)),
        ("outside-every-area", _outside_every_area_job(researcher)),
    ):
        report = generation.generate_report(job=job, generated_by=None)
        shutil.copyfile(
            Path(settings.REPORT_DIRECTORY) / report.file_name,
            target_directory / f"{name}.pdf",
        )
        assert (target_directory / f"{name}.pdf").stat().st_size == report.size_bytes


def _candidate(job, miles, bearing, name, **fields):
    return make_located_competitor(
        _point_at(job.target_point, miles, bearing), name=name, **fields
    )


def _dense_market_job(researcher):
    job = make_prospect_job(
        researcher=researcher,
        organization_name=(
            "Synthetic Brooklyn Heights Early Learning Cooperative and Family "
            "Enrichment Collective " * 3
        )[:255],
    )
    for index in range(74):
        if index < 8:
            name = (
                "Synthetic Long Named Early Learning Center and Family Enrichment "
                f"Academy Number {index + 1} of Brooklyn Heights"
            )
        else:
            name = f"Synthetic Competitor {index + 1:02d}"
        is_school_based = index % 9 == 4
        competitor = _candidate(
            job,
            0.05 + index * 0.0125,
            index * 137.508,
            name,
            kind=ProviderKind.SCHOOL_BASED_CHILD_CARE
            if is_school_based
            else ProviderKind.DAY_CARE_CENTER,
            total_capacity=20 + index * 5,
        )
        if is_school_based:
            Competitor.objects.filter(pk=competitor.pk).update(is_school_based=True)
        if index % 5 == 1:
            make_provider(
                source=ProviderSource.NY_STATE,
                original_name=name,
                kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
                competitor=competitor,
                total_capacity=15,
            )
    return job


def _one_competitor_job(researcher):
    job = make_prospect_job(researcher=researcher)
    _candidate(job, 0.3, 40, "Synthetic Only Competitor", total_capacity=60)
    return job


def _outside_every_area_teaser_job(researcher):
    job = make_prospect_job(researcher=researcher, target_point=OUTSIDE_EVERY_AREA)
    for index, miles in enumerate((0.3, 0.6)):
        _candidate(
            job,
            miles,
            90 + index * 60,
            f"Synthetic Harbor Center {index + 1}",
            total_capacity=45,
        )
    return job


def _no_census_vintage_job(researcher):
    job = make_prospect_job(researcher=researcher)
    for index, miles in enumerate((0.2, 0.45, 0.7)):
        _candidate(
            job,
            miles,
            index * 120,
            f"Synthetic Center {index + 1}",
            total_capacity=30 + index * 10,
        )
    return job


def _unpublished_capacities_job(researcher):
    job = make_prospect_job(researcher=researcher)
    _candidate(job, 0.15, 20, "Synthetic Published Center", total_capacity=64)
    _candidate(job, 0.3, 100, "Synthetic Unpublished Center")
    incomplete = _candidate(
        job, 0.45, 180, "Synthetic Incomplete Center", total_capacity=36
    )
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Incomplete Center",
        kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
        competitor=incomplete,
    )
    school = _candidate(
        job,
        0.6,
        260,
        "Synthetic School Program",
        kind=ProviderKind.SCHOOL_BASED_CHILD_CARE,
    )
    Competitor.objects.filter(pk=school.pk).update(is_school_based=True)
    _candidate(job, 0.75, 330, "Synthetic Second Published Center", total_capacity=48)
    return job


def _own_program_home_kinds_job(researcher):
    own = make_located_competitor(
        _point_at(BOROUGH_HALL, 0.05, 300), name="Synthetic Own Program"
    )
    job = make_prospect_job(
        researcher=researcher,
        provider_kinds=["day_care_center", "group_family_day_care", "family_day_care"],
        own_competitor=own,
    )
    _candidate(job, 0.25, 60, "Synthetic Center", total_capacity=50)
    _candidate(
        job,
        0.4,
        150,
        "Synthetic Group Family Day Care",
        kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
        total_capacity=12,
    )
    _candidate(
        job,
        0.55,
        240,
        "Synthetic Family Day Care",
        kind=ProviderKind.FAMILY_DAY_CARE,
        total_capacity=8,
    )
    withheld = make_competitor(display_name="Synthetic Address Withheld Home")
    make_provider(
        source=ProviderSource.NY_STATE,
        original_name="Synthetic Address Withheld Home",
        street_address="",
        kind=ProviderKind.FAMILY_DAY_CARE,
        competitor=withheld,
        total_capacity=8,
    )
    return job


TEASER_CASES = {
    "dense-market": _dense_market_job,
    "one-competitor": _one_competitor_job,
    "outside-every-area": _outside_every_area_teaser_job,
    "no-census-vintage": _no_census_vintage_job,
    "unpublished-capacities": _unpublished_capacities_job,
    "own-program-home-kinds": _own_program_home_kinds_job,
}


# One test per case, so each runs in a transaction of its own: a teaser counts every
# search candidate, so cases sharing one would count each other's competitors.
@pytest.mark.layout_inspection
@pytest.mark.parametrize("case", list(TEASER_CASES))
def test_render_the_inspection_teasers(settings, case):
    researcher = make_researcher()
    _make_area_grid()
    if case != "no-census-vintage":
        make_census_2024(TabulationArea.objects.get(code="11201"))
    now = timezone.now()
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS,
        finished_at=now - timedelta(days=1),
        publisher_rows_updated_at=now - timedelta(days=1, hours=6),
    )
    make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    set_contact_settings(
        settings,
        website_url=(
            "https://www.axisence-childcare-market-analysis.example/brooklyn/"
            "market-preview"
        ),
        email="brooklyn-market-previews@axisence-childcare-analysis.example",
        phone="+1 (555) 010-4400 ext. 2047",
    )
    target_directory = Path(settings.BASE_DIR) / "scratch" / "report-inspection"
    target_directory.mkdir(parents=True, exist_ok=True)

    teaser = generation.generate_teaser(
        job=TEASER_CASES[case](researcher), generated_by=None
    )

    target = target_directory / f"teaser-{case}.pdf"
    shutil.copyfile(Path(settings.REPORT_DIRECTORY) / teaser.file_name, target)
    assert target.stat().st_size == teaser.size_bytes


def _definition(competitor, kind, name):
    return Observation.objects.get(contact__competitor=competitor, kind=kind, name=name)


def _later_contact(competitor, researcher, job):
    # Entered now, after the predecessor's report was generated.
    return make_contact(
        competitor=competitor,
        researcher=researcher,
        job=job,
        contact_date=date(2026, 10, 20),
    )


def _copy_prices_and_availability(competitor, researcher, job):
    contact = _later_contact(competitor, researcher, job)
    for observation in Observation.objects.filter(
        contact__competitor=competitor,
        kind__in=(ObservationKind.PRICE, ObservationKind.AVAILABILITY),
    ):
        make_observation(
            contact=contact,
            kind=observation.kind,
            age_group=observation.age_group,
            schedule=observation.schedule,
            amount=observation.amount,
            price_period=observation.price_period,
            includes_discounts=observation.includes_discounts,
            availability_status=observation.availability_status,
            earliest_start_on=observation.earliest_start_on,
            expected_wait_months=observation.expected_wait_months,
        )


def _change_values(competitors, researcher, job):
    """Record a change of every class on the first ten continuing competitors."""
    rising, redefined, opening, declining, charging, deposit, retracted = competitors[
        :7
    ]
    contact = _later_contact(rising, researcher, job)
    full_time = _definition(rising, ObservationKind.SCHEDULE, "Full time")
    for name, monthly in (("Infants", 2050), ("Toddlers", 1850), ("Preschool", 1650)):
        add_price(
            contact,
            _definition(rising, ObservationKind.AGE_GROUP, name),
            full_time,
            str(monthly),
            PricePeriod.MONTHLY,
        )
    contact = _later_contact(redefined, researcher, job)
    make_observation(
        contact=contact,
        kind=ObservationKind.AGE_GROUP_RETIREMENT,
        age_group=_definition(redefined, ObservationKind.AGE_GROUP, "Toddlers"),
    )
    (twos,), _ = add_definitions(contact, age_groups=[("Twos", 24, 36)])
    add_price(
        contact,
        twos,
        _definition(redefined, ObservationKind.SCHEDULE, "Full time"),
        "1790",
        PricePeriod.MONTHLY,
    )
    make_observation(
        contact=_later_contact(opening, researcher, job),
        kind=ObservationKind.AVAILABILITY,
        age_group=_definition(opening, ObservationKind.AGE_GROUP, "Toddlers"),
        availability_status=AvailabilityStatus.OPENINGS,
        earliest_start_on=date(2027, 1, 5),
    )
    make_observation(
        contact=_later_contact(declining, researcher, job),
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        is_none_offered=True,
    )
    make_observation(
        contact=_later_contact(charging, researcher, job),
        kind=ObservationKind.OTHER_CHARGE,
        charge_kind=ChargeKind.REGISTRATION,
        amount=Decimal(150),
        charge_frequency=ChargeFrequency.ONCE,
    )
    make_observation(
        contact=_later_contact(deposit, researcher, job),
        kind=ObservationKind.OTHER_CHARGE,
        charge_kind=ChargeKind.DEPOSIT,
        months_of_tuition=Decimal(1),
        charge_frequency=ChargeFrequency.ONCE,
        description="refundable before the first day",
    )
    contacts.retract_observation(
        observation=Observation.objects.get(
            contact__competitor=retracted,
            kind=ObservationKind.PRICE,
            age_group__name="Infants",
            schedule__name="Full time",
        ),
        researcher=researcher,
        reason="Synthetic mistake",
    )
    for competitor in competitors[7:10]:
        _copy_prices_and_availability(competitor, researcher, job)


def _repeat_dense_market_job(researcher):
    predecessor = make_report_job(
        researcher=researcher,
        status=JobStatus.COMPLETE,
        organization_name="Synthetic Repeat Client",
    )
    competitors = []
    for index in range(32):
        competitor = _listed(
            predecessor,
            0.08 + index * 0.027,
            index * 47,
            "Synthetic Long Named Early Learning Center and Family Enrichment "
            f"Academy Number {index + 1} of Brooklyn Heights",
            total_capacity=20 + index * 7,
        )
        _price_competitor(competitor, researcher, predecessor, 1500 + index * 35)
        competitors.append(competitor)
    earlier = make_contact(
        competitor=competitors[3],
        researcher=researcher,
        job=predecessor,
        contact_date=date(2026, 10, 7),
    )
    make_observation(
        contact=earlier,
        kind=ObservationKind.DISCOUNT,
        discount_kind=DiscountKind.SIBLING,
        percent=Decimal(10),
    )
    make_observation(
        contact=make_contact(
            competitor=competitors[4],
            researcher=researcher,
            job=predecessor,
            contact_date=date(2026, 10, 7),
        ),
        kind=ObservationKind.OTHER_CHARGE,
        charge_kind=ChargeKind.REGISTRATION,
        is_none_offered=True,
    )
    # Listed by its regulator before the earlier report, which did not include it.
    newly_included = make_located_competitor(
        _point_at(BOROUGH_HALL, 0.85, 205), name="Synthetic Newly Included Center"
    )
    generation.generate_report(job=predecessor, generated_by=None)

    repeat_job = make_report_job(
        researcher=researcher,
        status=JobStatus.COMPLETE,
        organization_name="Synthetic Repeat Client",
        predecessor=predecessor,
        search_radius_miles=Decimal("1.50"),
        provider_kinds=[
            ProviderKind.DAY_CARE_CENTER,
            ProviderKind.SMALL_DAY_CARE_CENTER,
            ProviderKind.GROUP_FAMILY_DAY_CARE,
        ],
        target_point=_point_at(BOROUGH_HALL, 0.3, 0),
    )
    continuing = competitors[:28]
    merged, closed, delisted, _not_selected = competitors[28:]
    Provider.objects.filter(competitor=merged).update(competitor=continuing[12])
    Competitor.objects.filter(pk=merged.pk).update(
        retired_at=timezone.now(), merged_into=continuing[12], is_active=False
    )
    _report_status(
        closed, researcher, OperatingStatus.PERMANENTLY_CLOSED, date(2026, 10, 19)
    )
    Provider.objects.filter(competitor=delisted).update(missing_since=timezone.now())
    Competitor.objects.filter(pk=delisted.pk).update(is_active=False)
    newly_listed = [
        make_located_competitor(
            _point_at(BOROUGH_HALL, miles, bearing),
            name=name,
            kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
            total_capacity=12,
        )
        for miles, bearing, name in (
            (0.5, 75, "Synthetic Newly Listed Group Family Day Care"),
            (1.1, 290, "Synthetic Second Newly Listed Group Family Day Care"),
        )
    ]
    selected = [*continuing, closed, newly_included, *newly_listed]
    distances = distances_from(
        repeat_job.target_point, [competitor.pk for competitor in selected]
    )
    for competitor in selected:
        add_job_competitor(
            repeat_job, competitor, distance_meters=distances[competitor.pk]
        )
    _change_values(continuing, researcher, repeat_job)
    return repeat_job


def _repeat_nothing_verified_again_job(researcher):
    predecessor = make_report_job(
        researcher=researcher,
        status=JobStatus.COMPLETE,
        organization_name="Synthetic Unverified Repeat Client",
    )
    competitors = []
    for index, miles in enumerate((0.2, 0.35, 0.5, 0.65)):
        competitor = _listed(
            predecessor, miles, index * 90, f"Synthetic Earlier Competitor {index + 1}"
        )
        _price_competitor(competitor, researcher, predecessor, 1700 + index * 80)
        competitors.append((competitor, miles))
    generation.generate_report(job=predecessor, generated_by=None)
    repeat_job = make_report_job(
        researcher=researcher,
        organization_name="Synthetic Unverified Repeat Client",
        predecessor=predecessor,
    )
    for competitor, miles in competitors:
        add_job_competitor(
            repeat_job,
            competitor,
            distance_meters=miles * METERS_PER_MILE,
            research_status=ResearchStatus.TO_DO,
        )
    return repeat_job


REPEAT_CASES = {
    "repeat-dense-market": _repeat_dense_market_job,
    "repeat-nothing-verified-again": _repeat_nothing_verified_again_job,
}


# One test per case, so each runs in a transaction of its own.
@pytest.mark.layout_inspection
@pytest.mark.parametrize("case", list(REPEAT_CASES))
def test_render_the_inspection_repeat_reports(settings, case):
    researcher = make_researcher()
    _make_area_grid()
    make_census_2024(TabulationArea.objects.get(code="11201"))
    now = timezone.now()
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS,
        finished_at=now - timedelta(days=1),
        publisher_rows_updated_at=now - timedelta(days=1, hours=6),
    )
    make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    target_directory = Path(settings.BASE_DIR) / "scratch" / "report-inspection"
    target_directory.mkdir(parents=True, exist_ok=True)

    report = generation.generate_report(
        job=REPEAT_CASES[case](researcher), generated_by=None
    )

    assert report.compared_report is not None
    target = target_directory / f"{case}.pdf"
    shutil.copyfile(Path(settings.REPORT_DIRECTORY) / report.file_name, target)
    assert target.stat().st_size == report.size_bytes
