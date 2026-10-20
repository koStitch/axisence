"""Tests for the report's schematic map."""

import re

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from axisence.reports.content import build_report
from axisence.reports.models import ReportKind
from axisence.reports.schematic_map import MAP_HEIGHT, MAP_WIDTH
from axisence.research.models import ObservationKind, OperatingStatus
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.reports.factories import add_job_competitor, make_area, make_report_job
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
    point_north_of,
)

pytestmark = pytest.mark.django_db

# The circle's box, a margin of 8% of its side on every side, and the map's height.
CIRCLE_RADIUS_IN_MAP_UNITS = round(MAP_HEIGHT / (2 * (1 + 2 * 0.08)), 1)


def test_the_map_draws_the_main_list_and_its_surroundings():
    researcher = make_researcher()
    job = make_report_job(researcher=researcher)
    for miles in (0.2, 0.4):
        competitor = make_located_competitor(
            point_north_of(BOROUGH_HALL, miles), name=f"Synthetic {miles}"
        )
        add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    closed = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.3), name="Synthetic Closed"
    )
    add_job_competitor(job, closed, distance_meters=0.3 * METERS_PER_MILE)
    make_observation(
        contact=make_contact(competitor=closed, researcher=researcher),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.PERMANENTLY_CLOSED,
    )
    unlocated = make_competitor(display_name="Synthetic Unlocated")
    make_provider(original_name="Synthetic Unlocated", competitor=unlocated)
    add_job_competitor(job, unlocated, distance_meters=None)
    make_area("11201", BOROUGH_HALL)
    make_area("11217", Point(BOROUGH_HALL.x + 0.02, BOROUGH_HALL.y, srid=4326))
    make_area("10001", Point(BOROUGH_HALL.x + 0.5, BOROUGH_HALL.y, srid=4326))

    report_map = build_report(
        job=job, kind=ReportKind.DRAFT, cut_off=timezone.now()
    ).content.report_map

    assert [marker.number for marker in report_map.markers] == [1, 2]
    assert [area.code for area in report_map.areas] == ["11201", "11217"]
    assert all(area.path.startswith("M ") for area in report_map.areas)
    assert report_map.radius.radius == CIRCLE_RADIUS_IN_MAP_UNITS
    assert (report_map.target.x, report_map.target.y) == (
        MAP_WIDTH / 2,
        MAP_HEIGHT / 2,
    )
    assert report_map.radius_label == "1.00 mi"
    xs = [
        report_map.target.x,
        report_map.scale_bar.x,
        report_map.scale_bar.x + report_map.scale_bar.length,
        *(marker.x for marker in report_map.markers),
        *(area.label_x for area in report_map.areas),
    ]
    ys = [
        report_map.target.y,
        report_map.scale_bar.y,
        *(marker.y for marker in report_map.markers),
        *(area.label_y for area in report_map.areas),
    ]
    for area in report_map.areas:
        numbers = [float(number) for number in re.findall(r"-?\d+\.\d", area.path)]
        xs.extend(numbers[0::2])
        ys.extend(numbers[1::2])
    assert all(0 <= x <= MAP_WIDTH for x in xs)
    assert all(0 <= y <= MAP_HEIGHT for y in ys)
