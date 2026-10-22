"""Tests for a client report's transit access: the section's rows, the profile texts,
and how the report presents and sources them.
"""

from datetime import UTC, datetime

import pytest
from django.utils import timezone

from axisence.ingestion.models import IngestionRun, IngestionSource
from axisence.reports import transit_access
from axisence.reports.content import build_report
from axisence.reports.models import ReportKind
from axisence.reports.rendering import render_report_html
from axisence.reports.transit_access import TransitRow, TransitTexts
from axisence.research.models import ObservationKind, OperatingStatus
from axisence.transit.models import TransitMode
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.reports.factories import add_job_competitor, make_report_job
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
    point_north_of,
)
from tests.transit.factories import make_transit_line, make_transit_stop

pytestmark = pytest.mark.django_db


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def job(researcher):
    return make_report_job(researcher=researcher)


@pytest.fixture
def transit_network():
    """Stations and bus stops due north of Borough Hall, at whole hundredths of a
    mile.
    """
    lines = {
        (mode, name): make_transit_line(mode, name, position)
        for mode, name, position in (
            (TransitMode.SUBWAY, "A", 1),
            (TransitMode.SUBWAY, "R", 15),
            (TransitMode.SUBWAY, "W", 16),
            (TransitMode.SUBWAY, "2", 21),
            (TransitMode.SUBWAY, "3", 22),
            (TransitMode.SUBWAY, "4", 23),
            (TransitMode.SUBWAY, "5", 24),
            (TransitMode.BUS, "B25", 1),
            (TransitMode.BUS, "B26", 2),
            (TransitMode.BUS, "B103", 3),
        )
    }
    for stop_id, miles, name, line_names, group in (
        ("H1", 0.03, "Synthetic Hall", ("4", "5"), "H1+H2"),
        ("H2", 0.06, "Synthetic Hall", ("2", "3"), "H1+H2"),
        ("C1", 0.12, "Synthetic Court", ("R", "W"), None),
        ("J1", 0.18, "Synthetic Jay", ("A",), None),
    ):
        make_transit_stop(
            mode=TransitMode.SUBWAY,
            published_stop_id=stop_id,
            point=point_north_of(BOROUGH_HALL, miles),
            name=name,
            lines=[lines[(TransitMode.SUBWAY, line)] for line in line_names],
            station_group_key=group,
        )
    for stop_id, miles, line_names in (
        ("900001", 0.05, ("B25", "B26")),
        ("900002", 0.2, ("B103",)),
    ):
        make_transit_stop(
            mode=TransitMode.BUS,
            published_stop_id=stop_id,
            point=point_north_of(BOROUGH_HALL, miles),
            lines=[lines[(TransitMode.BUS, line)] for line in line_names],
        )


def _listed(job, miles, name):
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, miles), name=name)
    add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    return competitor


def _report_closed(competitor, researcher):
    make_observation(
        contact=make_contact(competitor=competitor, researcher=researcher),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.PERMANENTLY_CLOSED,
    )


def _build(job):
    return build_report(job=job, kind=ReportKind.DRAFT, cut_off=timezone.now())


def _texts(subway_entries, bus_entries):
    return TransitTexts(
        subway_entries=subway_entries,
        subway_text="; ".join(subway_entries),
        bus_entries=bus_entries,
    )


def test_the_target_and_each_competitor_get_their_nearby_transit(job, transit_network):
    _listed(job, 0.1, "Synthetic Near Center")
    _listed(job, 3, "Synthetic Far Center")
    unlocated = make_competitor(
        display_name="Synthetic Unlocated Center",
        street_address="100 Example Street",
        location=None,
    )
    make_provider(original_name="Synthetic Unlocated Center", competitor=unlocated)
    add_job_competitor(job, unlocated, distance_meters=None)

    content = _build(job).content

    target, *rows = content.transit.rows
    near = _texts(
        ("Synthetic Court: R, W (0.02 mi)", "Synthetic Hall: 2, 3, 4, 5 (0.04 mi)"),
        ("B25 (0.05 mi)", "B26 (0.05 mi)", "B103 (0.10 mi)"),
    )
    far = _texts(("None within 0.5 mi",), ("None within 0.25 mi",))
    unlocated_texts = _texts(("No location",), ("No location",))
    assert target == TransitRow(
        "",
        "Target location",
        _texts(
            (
                "Synthetic Hall: 2, 3, 4, 5 (0.03 mi)",
                "Synthetic Court: R, W (0.12 mi)",
            ),
            ("B25 (0.05 mi)", "B26 (0.05 mi)", "B103 (0.20 mi)"),
        ),
    )
    assert [row.number for row in rows] == ["1", "2", "3"]
    assert {row.location: row.texts for row in rows} == {
        "Synthetic Near Center": near,
        "Synthetic Far Center": far,
        "Synthetic Unlocated Center": unlocated_texts,
    }
    assert {entry.name: entry.transit for entry in content.competitors} == {
        "Synthetic Near Center": near,
        "Synthetic Far Center": far,
        "Synthetic Unlocated Center": unlocated_texts,
    }


def test_reported_closed_competitors_have_no_row(job, researcher, transit_network):
    open_competitor = _listed(job, 0.1, "Synthetic Open Center")
    _report_closed(_listed(job, 0.15, "Synthetic Closed Center"), researcher)

    with_one_open = _build(job).content.transit.rows
    _report_closed(open_competitor, researcher)
    all_closed = _build(job).content.transit.rows

    assert [row.location for row in with_one_open] == [
        "Target location",
        "Synthetic Open Center",
    ]
    assert [row.location for row in all_closed] == ["Target location"]


def test_the_report_presents_transit_access_and_its_source(job, transit_network):
    _listed(job, 0.1, "Synthetic First Center")
    _listed(job, 0.15, "Synthetic Second Center")

    html = render_report_html(_build(job).content, number=41)

    assert (
        html.index("<h2>Tuition comparison")
        < html.index("<h2>Transit access</h2>")
        < html.index("<h2>Competitor profiles</h2>")
    )
    assert html.count("<h5>Transit</h5>") == 2
    assert "Subway within 0.5 mi: Synthetic Court: R, W (0.02 mi); " in html
    # Each bus entry is kept on one line, so a list wraps only between entries.
    assert (
        '<span class="bus-entry">B25 (0.05 mi)</span>, '
        '<span class="bus-entry">B26 (0.05 mi)</span>'
    ) in html
    assert "MTA static GTFS schedules of the New York City subway" in html
    assert "Transit access lists the two nearest subway stations within 0.5 mi" in html
    assert "Transit access is not covered by this report." not in html


def test_the_sources_line_is_dated_by_the_run_s_start_in_new_york():
    run = IngestionRun(
        source=IngestionSource.MTA_TRANSIT,
        started_at=datetime(2026, 10, 22, 1, 0, tzinfo=UTC),
    )

    assert transit_access.sources_line(run) == (
        "MTA static GTFS schedules of the New York City subway, the Staten Island "
        "Railway, New York City Transit buses, and MTA Bus Company buses, covering "
        "New York City only: as checked on October 21, 2026."
    )
