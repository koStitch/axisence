"""Factories for report-ready and teaser-ready jobs, report rows, staff users, contact
settings, Census figures, provider runs, and research values, all synthetic.
"""

import itertools
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.utils import timezone

from axisence.demographics.models import CensusFigure, CensusVariable, TabulationArea
from axisence.ingestion.models import IngestionOutcome, IngestionRun
from axisence.reports.figure_lists import FIGURE_LISTS
from axisence.reports.models import Report, ReportKind
from axisence.research.models import (
    AvailabilityStatus,
    ChargeFrequency,
    ChargeKind,
    DiscountKind,
    JobCompetitor,
    JobPurpose,
    JobStatus,
    ObservationKind,
    OperatingStatus,
    ParkingAvailability,
    PricePeriod,
    ResearchStatus,
)
from tests.providers.factories import make_researcher
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_job,
    make_located_competitor,
    make_observation,
    point_north_of,
)

SYNTHETIC_WEBSITE_URL = "https://axisence.example/previews"
SYNTHETIC_CONTACT_EMAIL = "previews@axisence.example"
SYNTHETIC_CONTACT_PHONE = "(555) 010-4400"

JOB_PERMISSIONS = (
    "view_researchjob",
    "change_researchjob",
    "view_jobcompetitor",
    "change_jobcompetitor",
)

_file_numbers = itertools.count(1)

# Census figures in the column layout the Census Bureau publishes: base rows repeat
# their count as the percent without a percent margin, DP03_0121 holds only a
# percent, and the medians hold only an estimate. Each is estimate, estimate margin,
# percent, and percent margin.
_DEFAULT_FIGURES = {
    "DP05_0001": ("43210", "1500", "43210", None),
    "DP05_0005": ("3036", "526", "7.0", "1.1"),
    "DP05_0006": ("2400", "410", "5.6", "0.9"),
    "DP03_0014": ("2900", "380", "2900", None),
    "DP03_0015": ("2546", "360", "87.8", "4.9"),
    "DP02_0001": ("19800", "620", "19800", None),
    "DP02_0014": ("4950", "400", "25.0", "2.0"),
    "DP02_0054": ("1210", "250", "2.9", "0.6"),
    "DP02_0114": ("9800", "900", "23.6", "2.1"),
    "DP03_0062": ("162863", "12941", None, None),
    "DP03_0086": ("210500", "18800", None, None),
    "DP03_0121": (None, None, "1.5", "1.9"),
    "DP03_0021": ("14100", "900", "56.4", "3.0"),
    "DP04_0047": ("12900", "700", "65.2", "2.4"),
    "DP04_0134": ("2888", "136", None, None),
}
_FIGURE_FIELD_NAMES = (
    "estimate",
    "estimate_margin_of_error",
    "percent",
    "percent_margin_of_error",
)


def make_report_job(*, researcher, status=JobStatus.RESEARCHING, **fields):
    return make_job(
        researcher=researcher,
        point=BOROUGH_HALL,
        confirmed=True,
        status=status,
        **fields,
    )


def make_ready_job(*, researcher, status=JobStatus.RESEARCHING, **fields):
    """Return a report job listing one researched competitor 0.2 miles away."""
    job = make_report_job(researcher=researcher, status=status, **fields)
    competitor = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.2), name="Synthetic Near Day Care"
    )
    add_job_competitor(job, competitor, distance_meters=0.2 * METERS_PER_MILE)
    return job


def add_job_competitor(
    job, competitor, *, distance_meters, research_status=ResearchStatus.DONE
):
    return JobCompetitor.objects.create(
        job=job,
        competitor=competitor,
        distance_meters=distance_meters,
        research_status=research_status,
    )


def make_prospect_job(*, researcher, status=JobStatus.DRAFT, **fields):
    values = {
        "purpose": JobPurpose.PROSPECT,
        "organization_name": "Synthetic Prospect",
    }
    values.update(fields)
    return make_report_job(researcher=researcher, status=status, **values)


def add_candidate(miles, name="Synthetic Near Day Care", **provider_fields):
    """Return a competitor due north of Borough Hall, a search candidate of a job
    there.
    """
    return make_located_competitor(
        point_north_of(BOROUGH_HALL, miles), name=name, **provider_fields
    )


def make_teaser_ready_job(*, researcher, status=JobStatus.DRAFT, **fields):
    """Return a prospect job with one candidate 0.2 miles away and no selection."""
    job = make_prospect_job(researcher=researcher, status=status, **fields)
    add_candidate(0.2)
    return job


def set_contact_settings(
    settings,
    *,
    website_url=SYNTHETIC_WEBSITE_URL,
    email=SYNTHETIC_CONTACT_EMAIL,
    phone=SYNTHETIC_CONTACT_PHONE,
):
    settings.AXISENCE_WEBSITE_URL = website_url
    settings.AXISENCE_CONTACT_EMAIL = email
    settings.AXISENCE_CONTACT_PHONE = phone


def make_staff_with(*codenames):
    user = make_researcher("synthetic-staff")
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def make_report_row(job, kind=ReportKind.DRAFT, **fields):
    values = {
        "job": job,
        "kind": kind,
        "generated_at": timezone.now(),
        "file_name": f"report-synthetic-{next(_file_numbers)}.pdf",
        "size_bytes": 0,
        "sha256": "0" * 64,
    }
    values.update(fields)
    return Report.objects.create(**values)


def make_area(code, center, half_size_degrees=0.01):
    return TabulationArea.objects.create(
        code=code,
        boundary=MultiPolygon(
            Polygon.from_bbox(
                (
                    center.x - half_size_degrees,
                    center.y - half_size_degrees,
                    center.x + half_size_degrees,
                    center.y + half_size_degrees,
                )
            ),
            srid=4326,
        ),
    )


def make_census_2024(area, *, values=None):
    """Store the 2024 figure list's variables and one figure of each for the area.

    values maps a code to the figure fields it overrides.
    """
    values = values or {}
    for definition in FIGURE_LISTS[2024]:
        variable, _ = CensusVariable.objects.get_or_create(
            vintage=2024,
            code=definition.code,
            defaults={
                "table": definition.table,
                "estimate_label": definition.expected_estimate_label,
            },
        )
        figure = {
            name: None if value is None else Decimal(value)
            for name, value in zip(
                _FIGURE_FIELD_NAMES, _DEFAULT_FIGURES[definition.code], strict=True
            )
        }
        figure.update(values.get(definition.code, {}))
        CensusFigure.objects.create(tabulation_area=area, variable=variable, **figure)


def make_provider_run(
    source,
    *,
    finished_at,
    publisher_rows_updated_at=None,
    outcome=IngestionOutcome.SUCCEEDED,
):
    return IngestionRun.objects.create(
        source=source,
        started_at=finished_at - timedelta(minutes=5),
        finished_at=finished_at,
        outcome=outcome,
        publisher_rows_updated_at=publisher_rows_updated_at,
    )


def add_definitions(contact, *, age_groups=(), schedules=()):
    """Record age groups as (name, youngest, oldest) and schedules as (name, days,
    hours), and return both lists.
    """
    return (
        [
            make_observation(
                contact=contact,
                kind=ObservationKind.AGE_GROUP,
                name=name,
                youngest_age_months=youngest,
                oldest_age_months=oldest,
            )
            for name, youngest, oldest in age_groups
        ],
        [
            make_observation(
                contact=contact,
                kind=ObservationKind.SCHEDULE,
                name=name,
                days_per_week=days,
                hours_per_day=None if hours is None else Decimal(hours),
            )
            for name, days, hours in schedules
        ],
    )


def add_price(
    contact, age_group, schedule, amount, period, *, includes_discounts=False
):
    return make_observation(
        contact=contact,
        kind=ObservationKind.PRICE,
        age_group=age_group,
        schedule=schedule,
        amount=Decimal(amount),
        price_period=period,
        includes_discounts=includes_discounts,
    )


def record_every_value(competitor, researcher, contact_date=date(2026, 10, 14)):
    """Record one contact holding a value of every kind but the retirements."""
    contact = make_contact(
        competitor=competitor, researcher=researcher, contact_date=contact_date
    )
    (infants, preschool), (full_time, half_day, part_time) = add_definitions(
        contact,
        age_groups=[("Infants", 6, 18), ("Preschool", 36, None)],
        schedules=[
            ("Full time", 5, "10"),
            ("Half day", 1, "1"),
            ("Part time", 3, None),
        ],
    )
    add_price(contact, infants, full_time, "2450", PricePeriod.MONTHLY)
    add_price(
        contact, infants, part_time, "62.50", PricePeriod.DAILY, includes_discounts=True
    )
    add_price(contact, preschool, half_day, "20", PricePeriod.HOURLY)
    add_price(contact, preschool, part_time, "15", PricePeriod.HOURLY)
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=infants,
        availability_status=AvailabilityStatus.WAITLIST,
        earliest_start_on=date(2027, 1, 5),
        expected_wait_months=3,
    )
    make_observation(
        contact=contact,
        kind=ObservationKind.AVAILABILITY,
        age_group=preschool,
        availability_status=AvailabilityStatus.OPENINGS,
    )
    for discount in (
        {"discount_kind": DiscountKind.SIBLING, "percent": Decimal(10)},
        {
            "discount_kind": DiscountKind.MULTI_DAY,
            "amount": Decimal(50),
            "description": "two or more days",
        },
        {"discount_kind": DiscountKind.UPFRONT_PAYMENT, "is_none_offered": True},
        {"discount_kind": DiscountKind.REFERRAL},
        {"discount_kind": DiscountKind.OTHER, "description": "synthetic loyalty offer"},
    ):
        make_observation(contact=contact, kind=ObservationKind.DISCOUNT, **discount)
    for charge in (
        {
            "charge_kind": ChargeKind.REGISTRATION,
            "amount": Decimal(150),
            "charge_frequency": ChargeFrequency.ONCE,
        },
        {"charge_kind": ChargeKind.DEPOSIT, "months_of_tuition": Decimal(1)},
        {
            "charge_kind": ChargeKind.LATE_PICKUP,
            "amount": Decimal("1.50"),
            "charge_frequency": ChargeFrequency.PER_HOUR,
        },
        {"charge_kind": ChargeKind.OTHER},
    ):
        make_observation(contact=contact, kind=ObservationKind.OTHER_CHARGE, **charge)
    for kind, fact in (
        (
            ObservationKind.WEEKDAY_HOURS,
            {"opens_at": time(7, 30), "closes_at": time(18)},
        ),
        (ObservationKind.WEEKS_OPEN, {"weeks_open_per_year": 50}),
        (ObservationKind.MINIMUM_BOOKING, {"days_per_week": 2}),
        (ObservationKind.LAST_FEE_RISE, {"last_fee_rise_on": date(2026, 3, 1)}),
        (ObservationKind.STATED_PLACES, {"stated_places": 1200}),
        (
            ObservationKind.INSPECTION_STATUS,
            {"inspection_status": "Passed", "inspected_on": date(2026, 6, 2)},
        ),
        (
            ObservationKind.PARKING,
            {"parking": ParkingAvailability.STREET_ONLY, "description": "metered"},
        ),
        (ObservationKind.THREE_K_FOR_ALL, {"yes_no_answer": True}),
        (ObservationKind.PRE_K_FOR_ALL, {"yes_no_answer": False}),
        (ObservationKind.ACCEPTS_VOUCHERS, {"yes_no_answer": True}),
        (ObservationKind.WEBSITE, {"website_url": "https://example.com/synthetic"}),
        (
            ObservationKind.DESCRIPTION,
            {"description": "Synthetic garden & <b>play</b>"},
        ),
        (
            ObservationKind.OPERATING_STATUS,
            {"operating_status": OperatingStatus.OPERATING},
        ),
    ):
        make_observation(contact=contact, kind=kind, **fact)
