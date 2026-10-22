"""Tests for finding the station groups and bus lines near points."""

import math

import pytest
from django.contrib.gis.db.models.functions import Distance

from axisence.transit import nearby
from axisence.transit.models import TransitMode, TransitStop
from tests.research.factories import BOROUGH_HALL, point_north_of
from tests.transit.factories import make_transit_line, make_transit_stop

pytestmark = pytest.mark.django_db

FAR_AWAY = point_north_of(BOROUGH_HALL, 3)


def _distance(stop, point=BOROUGH_HALL):
    return (
        TransitStop.objects.annotate(distance=Distance("location", point))
        .get(pk=stop.pk)
        .distance.m
    )


def _station(stop_id, miles, *, lines, name="Synthetic Station", **fields):
    return make_transit_stop(
        mode=TransitMode.SUBWAY,
        published_stop_id=stop_id,
        point=point_north_of(BOROUGH_HALL, miles),
        name=name,
        lines=lines,
        **fields,
    )


def _bus_stop(stop_id, miles, *, lines, origin=BOROUGH_HALL, **fields):
    return make_transit_stop(
        mode=TransitMode.BUS,
        published_stop_id=stop_id,
        point=point_north_of(origin, miles),
        lines=lines,
        **fields,
    )


@pytest.mark.parametrize(
    ("mode", "constant", "miles"),
    [
        (TransitMode.SUBWAY, "SUBWAY_CATCHMENT_METERS", 0.4),
        (TransitMode.BUS, "BUS_CATCHMENT_METERS", 0.2),
    ],
    ids=["subway", "bus"],
)
def test_a_stop_exactly_at_the_catchment_counts_and_one_beyond_does_not(
    monkeypatch, mode, constant, miles
):
    stop = make_transit_stop(
        mode=mode,
        published_stop_id="900001",
        point=point_north_of(BOROUGH_HALL, miles),
        lines=[make_transit_line(mode, "Synthetic Line", 1)],
    )
    distance = _distance(stop)

    monkeypatch.setattr(nearby, constant, distance)
    (at_the_catchment,) = nearby.find_nearby_transit([BOROUGH_HALL])
    monkeypatch.setattr(nearby, constant, math.nextafter(distance, 0))
    (beyond_the_catchment,) = nearby.find_nearby_transit([BOROUGH_HALL])

    assert at_the_catchment.stations or at_the_catchment.bus_lines
    assert beyond_the_catchment == nearby.NearbyTransit(stations=(), bus_lines=())


def test_the_two_nearest_station_groups_with_all_their_members_lines():
    a, c, f, r, w, two = (
        make_transit_line(TransitMode.SUBWAY, name, position)
        for name, position in (
            ("A", 1),
            ("C", 2),
            ("F", 6),
            ("R", 15),
            ("W", 16),
            ("2", 21),
        )
    )
    nearest_member = _station(
        "R29",
        0.1,
        lines=[w, r],
        name="Synthetic Jay St",
        station_group_key="A41+R29",
    )
    # Beyond the catchment, but its group has a member within it.
    _station(
        "A41",
        0.6,
        lines=[f, c, a],
        name="Synthetic Jay St Far Platform",
        station_group_key="A41+R29",
    )
    alpha = _station("901", 0.3, lines=[two], name="Synthetic Alpha")
    _station("902", 0.3, lines=[two], name="Synthetic Beta")
    _station("903", 0.45, lines=[two], name="Synthetic Gamma")
    _station("904", 0.05, lines=[two], name="Synthetic Inactive", is_active=False)
    _station("905", 0.05, lines=[], name="Synthetic Without Lines")

    (found,) = nearby.find_nearby_transit([BOROUGH_HALL])

    assert found.stations == (
        nearby.NearbyStation(
            name="Synthetic Jay St",
            lines=("A", "C", "F", "R", "W"),
            distance_meters=_distance(nearest_member),
        ),
        nearby.NearbyStation(
            name="Synthetic Alpha", lines=("2",), distance_meters=_distance(alpha)
        ),
    )


def test_each_bus_line_once_at_its_nearest_stop():
    b2, b11, b44, b44_sbs, b103, bm1 = (
        make_transit_line(TransitMode.BUS, name, position)
        for position, name in enumerate(
            ("B2", "B11", "B44", "B44-SBS", "B103", "BM1"), start=1
        )
    )
    nearest = _bus_stop("900001", 0.05, lines=[b44, b11])
    _bus_stop("900002", 0.05, lines=[b2])
    farther = _bus_stop("900003", 0.15, lines=[b44, b44_sbs])
    _bus_stop("900004", 0.02, lines=[b103], is_active=False)
    _bus_stop("900005", 0.01, lines=[])
    _bus_stop("900006", 0.3, lines=[bm1])

    (found,) = nearby.find_nearby_transit([BOROUGH_HALL])

    assert found.bus_lines == (
        nearby.NearbyBusLine("B2", _distance(nearest)),
        nearby.NearbyBusLine("B11", _distance(nearest)),
        nearby.NearbyBusLine("B44", _distance(nearest)),
        nearby.NearbyBusLine("B44-SBS", _distance(farther)),
    )


def test_results_follow_the_points_in_two_queries(django_assert_num_queries):
    b1, b2, b3 = (
        make_transit_line(TransitMode.BUS, name, position)
        for position, name in enumerate(("B1", "B2", "B3"), start=1)
    )
    a = make_transit_line(TransitMode.SUBWAY, "A", 1)
    _bus_stop("900001", 0.05, lines=[b1])
    _bus_stop("900002", 0.05, lines=[b2], origin=FAR_AWAY)
    with django_assert_num_queries(2):
        few = nearby.find_nearby_transit([FAR_AWAY, BOROUGH_HALL])

    for index in range(10):
        _bus_stop(f"91000{index}", 0.01 * index, lines=[b3, b1])
        _station(f"92{index}", 0.02 * index, lines=[a])
    third_point = point_north_of(BOROUGH_HALL, 0.1)
    with django_assert_num_queries(2):
        more = nearby.find_nearby_transit([BOROUGH_HALL, FAR_AWAY, third_point])
    with django_assert_num_queries(0):
        none = nearby.find_nearby_transit([])

    assert [[line.name for line in result.bus_lines] for result in few] == [
        ["B2"],
        ["B1"],
    ]
    assert [[line.name for line in result.bus_lines] for result in more] == [
        ["B1", "B3"],
        ["B2"],
        ["B1", "B3"],
    ]
    assert [len(result.stations) for result in more] == [2, 0, 2]
    assert none == ()
