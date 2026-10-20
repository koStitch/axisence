"""Renders the reports inspected page by page into scratch/report-inspection/. Run only
with -m layout_inspection; the PDFs remain after the run.
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
from axisence.research.models import (
    AvailabilityStatus,
    ContactMethod,
    JobStatus,
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
    make_census_2024,
    make_provider_run,
    make_report_job,
    record_every_value,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_DEGREE_OF_LATITUDE,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
)

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
    return job


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
