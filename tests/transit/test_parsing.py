"""Tests for reading one GTFS feed into its stops and the lines that stop at each on
weekday daytime service.
"""

from datetime import date
from decimal import Decimal

import pytest

from axisence.ingestion.coordinates import Coordinates
from axisence.ingestion.errors import IngestionError
from axisence.transit.feeds import BUS_FEEDS, SUBWAY_FEED
from axisence.transit.parsing import ParsedStop, StopFieldWarning, read_feed
from tests.transit.gtfs_archives import (
    FAR_END_DATE,
    WEEKDAYS,
    fixture_tables,
    minimal_bus_tables,
    minimal_subway_tables,
    write_feed,
)

SERVICE_DATE = date(2026, 10, 22)
BUS_FEED = BUS_FEEDS[0]
STATION = ("901", "Synthetic Station", "40.6926", "-73.9907", "1", "")
PLATFORM = ("901N", "Synthetic Station", "40.6926", "-73.9907", "", "901")


def _read(directory, feed, tables):
    return read_feed(write_feed(directory, feed.file_name, tables), feed, SERVICE_DATE)


def _without_member(member):
    def change(tables):
        del tables[member]

    return change


def _renamed_columns(member, *names):
    def change(tables):
        tables[member][0] = [
            f"synthetic_{name}" if name in names else name for name in tables[member][0]
        ]

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            _without_member("transfers.txt"),
            "The feed gtfs_subway.zip has no transfers.txt.",
        ),
        (
            _renamed_columns("routes.txt", "route_short_name", "route_sort_order"),
            "routes.txt of the feed gtfs_subway.zip is missing expected columns: "
            "route_short_name, route_sort_order.",
        ),
    ],
    ids=["missing member", "missing columns"],
)
def test_a_missing_member_or_column_fails_the_feed(tmp_path, change, message):
    tables = fixture_tables(SUBWAY_FEED.file_name)
    change(tables)

    with pytest.raises(IngestionError) as raised:
        _read(tmp_path, SUBWAY_FEED, tables)

    assert str(raised.value) == message


def test_a_file_that_is_not_a_zip_fails_the_feed(tmp_path):
    path = tmp_path / SUBWAY_FEED.file_name
    path.write_bytes(b"synthetic text, not a zip")

    with pytest.raises(IngestionError) as raised:
        read_feed(path, SUBWAY_FEED, SERVICE_DATE)

    assert str(raised.value) == "The feed gtfs_subway.zip is not a readable zip file."


def test_extra_members_and_columns_change_nothing(tmp_path):
    tables = fixture_tables(SUBWAY_FEED.file_name)
    plain = _read(tmp_path / "plain", SUBWAY_FEED, tables)
    tables["agency.txt"] = [["agency_id", "agency_name"], ["MTA NYCT", "Synthetic"]]
    # The extra column comes first, so every other column moves.
    for rows in tables.values():
        rows[0].insert(0, "synthetic_extra")
        for row in rows[1:]:
            row.insert(0, "synthetic value")

    extended = _read(tmp_path / "extended", SUBWAY_FEED, tables)

    assert extended == plain


def test_coordinates_with_leading_spaces_are_read(tmp_path):
    parsed = _read(tmp_path, BUS_FEED, fixture_tables(BUS_FEED.file_name))

    stop = next(stop for stop in parsed.stops if stop.published_stop_id == "306533")
    assert stop == ParsedStop(
        "306533",
        "CADMAN PLAZA WEST/MONTAGUE ST",
        Coordinates(longitude=Decimal("-73.990408"), latitude=Decimal("40.693578")),
    )
    assert parsed.field_warnings == ()


def test_unusable_coordinates_skip_the_stop_with_one_warning_each(tmp_path):
    tables = minimal_bus_tables(
        stops=[
            ("900001", "SYNTHETIC USABLE", "40.6926", "-73.9907"),
            ("900002", "SYNTHETIC UNPARSEABLE", "forty", "-73.9907"),
            ("900003", "SYNTHETIC BLANK", "", ""),
            # Philadelphia, outside the NY State bounding box.
            ("900004", "SYNTHETIC OUT OF STATE", "39.9526", "-75.1652"),
            ("900005", "SYNTHETIC NOT FINITE", "NaN", "-73.9907"),
        ],
        stop_times=[
            ("TRIP-1", "900001", "12:00:00", "12:00:00"),
            ("TRIP-1", "900002", "12:05:00", "12:05:00"),
        ],
    )

    parsed = _read(tmp_path, BUS_FEED, tables)

    skipped = ["900002", "900003", "900004", "900005"]
    assert [stop.published_stop_id for stop in parsed.stops] == ["900001"]
    assert parsed.skipped_stop_ids == frozenset(skipped)
    assert parsed.field_warnings == tuple(
        StopFieldWarning("gtfs_b.zip", stop_id) for stop_id in skipped
    )
    # A skipped stop's lines are still read.
    assert parsed.line_names_by_stop_id == {
        "900001": frozenset({"B900"}),
        "900002": frozenset({"B900"}),
    }


def test_platforms_count_for_their_station_and_other_stops_are_ignored(tmp_path):
    tables = minimal_subway_tables(
        stops=[
            STATION,
            # A platform's coordinates are never read.
            ("901N", "Synthetic Station", "", "", "", "901"),
            ("901S", "Synthetic Station", "40.6926", "-73.9907", "0", "901"),
            ("E901", "Synthetic Entrance", "40.6927", "-73.9908", "2", "901"),
        ],
        routes=[("A", "A", "1", "1"), ("C", "C", "1", "2"), ("E", "E", "1", "3")],
        trips=[
            ("A", "WEEKDAY", "TRIP-A"),
            ("C", "WEEKDAY", "TRIP-C"),
            ("E", "WEEKDAY", "TRIP-E"),
        ],
        stop_times=[
            ("TRIP-A", "901N", "12:00:00", "12:00:00"),
            ("TRIP-C", "901S", "12:00:00", "12:00:00"),
            ("TRIP-E", "E901", "12:00:00", "12:00:00"),
            ("TRIP-E", "UNLISTED", "12:00:00", "12:00:00"),
        ],
    )

    parsed = _read(tmp_path, SUBWAY_FEED, tables)

    assert [stop.published_stop_id for stop in parsed.stops] == ["901"]
    assert parsed.field_warnings == ()
    assert parsed.line_names_by_stop_id == {"901": frozenset({"A", "C"})}


def test_only_weekday_daytime_stops_add_a_line(tmp_path):
    # One stop, trip, and line per case; a stop whose case qualifies gets its line.
    cases = [
        # (stop, service, route, arrival, departure, pickup, drop-off)
        ("WEEKEND", "WEEKEND", "B1", "12:00:00", "12:00:00", "", ""),
        ("ENDED", "ENDED", "B1", "12:00:00", "12:00:00", "", ""),
        ("ENDS_TODAY", "ENDS_TODAY", "B1", "12:00:00", "12:00:00", "", ""),
        ("DATES_ONLY", "DATES_ONLY", "B1", "12:00:00", "12:00:00", "", ""),
        ("AT_065959", "WEEKDAY", "B1", "06:59:59", "06:59:59", "", ""),
        ("AT_070000", "WEEKDAY", "B1", "07:00:00", "07:00:00", "", ""),
        ("AT_185959", "WEEKDAY", "B1", "18:59:59", "18:59:59", "", ""),
        ("AT_190000", "WEEKDAY", "B1", "19:00:00", "19:00:00", "", ""),
        ("AT_243000", "WEEKDAY", "B1", "24:30:00", "24:30:00", "", ""),
        ("ARRIVAL_ONLY", "WEEKDAY", "B1", "12:00:00", "", "", ""),
        ("DEPARTURE_FIRST", "WEEKDAY", "B1", "06:00:00", "12:00:00", "", ""),
        ("NO_TIME", "WEEKDAY", "B1", "", "", "", ""),
        ("UNPARSEABLE_TIME", "WEEKDAY", "B1", "noon", "noon", "", ""),
        ("PASS_THROUGH", "WEEKDAY", "B1", "12:00:00", "12:00:00", "1", "1"),
        ("DROP_OFF_ONLY", "WEEKDAY", "B1", "12:00:00", "12:00:00", "1", "0"),
        ("SHUTTLE", "WEEKDAY", "SHUTTLE", "12:00:00", "12:00:00", "", ""),
    ]
    tables = minimal_bus_tables(
        stops=[(stop, "SYNTHETIC STOP", "40.6926", "-73.9907") for stop, *_ in cases],
        routes=[("B1", "B1", "3"), ("SHUTTLE", "SHUTTLE", "711")],
        trips=[(route, service, stop) for stop, service, route, *_ in cases],
        calendar=[
            ("WEEKDAY", *WEEKDAYS, FAR_END_DATE),
            ("WEEKEND", "0", "0", "0", "0", "0", FAR_END_DATE),
            ("ENDED", *WEEKDAYS, "20261021"),
            ("ENDS_TODAY", "0", "0", "1", "0", "0", "20261022"),
        ],
        stop_times=[(stop, stop, *times) for stop, _, _, *times in cases],
    )
    # calendar_dates.txt is never read.
    tables["calendar_dates.txt"] = [
        ["service_id", "date", "exception_type"],
        ["DATES_ONLY", "20261022", "1"],
    ]

    parsed = _read(tmp_path, BUS_FEED, tables)

    assert set(parsed.line_names_by_stop_id) == {
        "ENDS_TODAY",
        "AT_070000",
        "AT_185959",
        "ARRIVAL_ONLY",
        "DEPARTURE_FIRST",
        "DROP_OFF_ONLY",
    }


def test_a_feed_without_current_weekday_service_fails_naming_the_feed(tmp_path):
    tables = minimal_bus_tables(calendar=[("WEEKDAY", *WEEKDAYS, "20261021")])

    with pytest.raises(IngestionError) as raised:
        _read(tmp_path, BUS_FEED, tables)

    assert str(raised.value) == (
        "The feed gtfs_b.zip has no weekday service running on or after 2026-10-22."
    )


@pytest.mark.parametrize(
    ("feed", "tables", "message"),
    [
        (
            BUS_FEED,
            minimal_bus_tables(
                stops=[("9" * 51, "SYNTHETIC STOP", "40.6926", "-73.9907")]
            ),
            f"Stop {'9' * 51} of the feed gtfs_b.zip has a value longer than its "
            "column.",
        ),
        (
            BUS_FEED,
            minimal_bus_tables(stops=[("900001", "S" * 256, "40.6926", "-73.9907")]),
            "Stop 900001 of the feed gtfs_b.zip has a value longer than its column.",
        ),
        (
            BUS_FEED,
            minimal_bus_tables(routes=[("B900", "", "3")]),
            "Route B900 of the feed gtfs_b.zip has no route_short_name.",
        ),
        (
            BUS_FEED,
            minimal_bus_tables(routes=[("B900", "B" * 51, "3")]),
            "Route B900 of the feed gtfs_b.zip has a route_short_name longer than 50 "
            "characters.",
        ),
        (
            SUBWAY_FEED,
            minimal_subway_tables(routes=[("A", "A", "1", "")]),
            "Route A of the feed gtfs_subway.zip has no usable route_sort_order.",
        ),
        (
            SUBWAY_FEED,
            minimal_subway_tables(routes=[("A", "A", "1", "-1")]),
            "Route A of the feed gtfs_subway.zip has no usable route_sort_order.",
        ),
    ],
    ids=[
        "long stop ID",
        "long stop name",
        "blank short name",
        "long short name",
        "blank sort order",
        "negative sort order",
    ],
)
def test_an_unstorable_value_fails_the_feed(tmp_path, feed, tables, message):
    with pytest.raises(IngestionError) as raised:
        _read(tmp_path, feed, tables)

    assert str(raised.value) == message


def test_express_variants_fold_into_their_line_and_shuttles_keep_s(tmp_path):
    routes = [
        ("FX", "FX", "1", "7"),
        ("F", "F", "1", "6"),
        ("FS", "S", "1", "18"),
        ("GS", "S", "1", "17"),
        # No route is named 6, so 6X stays a line of its own.
        ("6X", "6X", "1", "26"),
    ]
    tables = minimal_subway_tables(
        stops=[STATION, PLATFORM],
        routes=routes,
        trips=[(route_id, "WEEKDAY", route_id) for route_id, *_ in routes],
        stop_times=[
            (route_id, "901N", "12:00:00", "12:00:00") for route_id, *_ in routes
        ],
    )

    parsed = _read(tmp_path, SUBWAY_FEED, tables)

    assert parsed.line_names_by_stop_id == {"901": frozenset({"F", "S", "6X"})}
    assert parsed.subway_line_sort_orders == {"F": 6, "S": 17, "6X": 26}
