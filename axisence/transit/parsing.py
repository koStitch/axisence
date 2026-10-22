"""Read one MTA static GTFS feed into its stops and the lines scheduled to stop at each
on weekday daytime service.

Parsing is pure: it reads one zip in place, streaming each member, and never touches
the database. Columns are found by header name, because their order differs between
feeds, and every cell is trimmed before use.
"""

import collections
import csv
import dataclasses
import functools
import io
import re
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from axisence.ingestion.coordinates import Coordinates, is_inside_new_york_state
from axisence.ingestion.errors import IngestionError
from axisence.transit.feeds import TransitFeed
from axisence.transit.models import (
    LINE_NAME_MAXIMUM_LENGTH,
    PUBLISHED_STOP_ID_MAXIMUM_LENGTH,
    STOP_NAME_MAXIMUM_LENGTH,
    TransitMode,
)

_STOPS = "stops.txt"
_ROUTES = "routes.txt"
_TRIPS = "trips.txt"
_CALENDAR = "calendar.txt"
_STOP_TIMES = "stop_times.txt"
_TRANSFERS = "transfers.txt"

_WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday")

# The members every feed needs, with the columns each must hold. Extra members and
# columns are ignored, and pickup_type and drop_off_type are read when present.
_REQUIRED_COLUMNS = {
    _STOPS: ("stop_id", "stop_name", "stop_lat", "stop_lon"),
    _ROUTES: ("route_id", "route_short_name", "route_type"),
    _TRIPS: ("route_id", "service_id", "trip_id"),
    _CALENDAR: ("service_id", *_WEEKDAY_COLUMNS, "end_date"),
    _STOP_TIMES: ("trip_id", "stop_id", "arrival_time", "departure_time"),
}
_SUBWAY_REQUIRED_COLUMNS = {
    _STOPS: ("location_type", "parent_station"),
    _ROUTES: ("route_sort_order",),
    _TRANSFERS: ("from_stop_id", "to_stop_id"),
}

# A shuttle bus that replaces trains, not a line of the subway.
_SHUTTLE_BUS_ROUTE_TYPE = "711"
_STATION_LOCATION_TYPE = "1"
_PLATFORM_LOCATION_TYPES = frozenset({"", "0"})
# A stop time whose pickup_type and drop_off_type both read 1 passes the stop without
# letting anyone on or off.
_NO_PICKUP_OR_DROP_OFF = "1"
_SERVICE_DAY_VALUE = "1"
# Weekday daytime service: scheduled to stop from 07:00:00 to before 19:00:00.
_SERVICE_WINDOW_START_SECONDS = 7 * 3600
_SERVICE_WINDOW_END_SECONDS = 19 * 3600
_GTFS_TIME = re.compile(r"([0-9]+):([0-5][0-9]):([0-5][0-9])")
_GTFS_DATE = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")
_NON_NEGATIVE_INTEGER = re.compile(r"[0-9]+")
_FOLDED_VARIANT_SUFFIX = "X"


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedStop:
    published_stop_id: str
    name: str
    coordinates: Coordinates


@dataclasses.dataclass(frozen=True, slots=True)
class StopFieldWarning:
    """A stop row skipped for its stop_lat and stop_lon."""

    feed_file_name: str
    published_stop_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedFeed:
    feed: TransitFeed
    # Usable stop rows, the first of each ID; for the subway, stations only.
    stops: tuple[ParsedStop, ...]
    # The IDs of rows skipped for their coordinates.
    skipped_stop_ids: frozenset[str]
    # Every listed stop ID, usable or skipped, with at least one qualifying line.
    line_names_by_stop_id: Mapping[str, frozenset[str]]
    # Subway: each line name's smallest route_sort_order; empty for bus feeds.
    subway_line_sort_orders: Mapping[str, int]
    # Subway: the unordered pairs of different stations joined by a transfer; empty
    # for bus feeds.
    station_transfers: frozenset[frozenset[str]]
    field_warnings: tuple[StopFieldWarning, ...]


@dataclasses.dataclass(slots=True)
class _Stops:
    usable: dict[str, ParsedStop] = dataclasses.field(default_factory=dict)
    skipped_ids: set[str] = dataclasses.field(default_factory=set)
    # Subway stations or bus stops, usable or skipped.
    listed_ids: set[str] = dataclasses.field(default_factory=set)
    parent_station_by_platform_id: dict[str, str] = dataclasses.field(
        default_factory=dict
    )
    warnings: list[StopFieldWarning] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class _Routes:
    # Routes in service only: every route but shuttle buses.
    line_name_by_route_id: Mapping[str, str]
    subway_line_sort_orders: Mapping[str, int]


def read_feed(path: Path, feed: TransitFeed, service_date: date) -> ParsedFeed:
    """Read a feed's stops and the lines that stop at each on weekday daytime service
    running on or after the service date.

    Every required member and column is checked before any data row is read.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            return _read_archive(archive, feed, service_date)
    except zipfile.BadZipFile:
        raise IngestionError(
            f"The feed {feed.file_name} is not a readable zip file."
        ) from None


def _read_archive(
    archive: zipfile.ZipFile, feed: TransitFeed, service_date: date
) -> ParsedFeed:
    _check_members(archive, feed)
    is_subway = feed.mode == TransitMode.SUBWAY
    stops = _read_stops(archive, feed)
    routes = _read_routes(archive, feed)
    services = _read_qualifying_services(archive, feed, service_date)
    line_name_by_trip_id = _read_trip_line_names(archive, routes, services)
    return ParsedFeed(
        feed=feed,
        stops=tuple(stops.usable.values()),
        skipped_stop_ids=frozenset(stops.skipped_ids),
        line_names_by_stop_id=_read_line_names_by_stop_id(
            archive, stops, line_name_by_trip_id
        ),
        subway_line_sort_orders=routes.subway_line_sort_orders,
        station_transfers=_read_station_transfers(archive)
        if is_subway
        else frozenset(),
        field_warnings=tuple(stops.warnings),
    )


def _check_members(archive: zipfile.ZipFile, feed: TransitFeed) -> None:
    member_names = set(archive.namelist())
    for member, columns in _required_columns(feed.mode).items():
        if member not in member_names:
            raise IngestionError(f"The feed {feed.file_name} has no {member}.")
        with _open_member(archive, member) as file:
            header = _column_indexes(next(csv.reader(file), []))
        missing = [column for column in columns if column not in header]
        if missing:
            raise IngestionError(
                f"{member} of the feed {feed.file_name} is missing expected columns: "
                f"{', '.join(missing)}."
            )


def _required_columns(mode: TransitMode) -> dict[str, tuple[str, ...]]:
    """Return the members a feed of the mode must hold, with their columns."""
    columns = dict(_REQUIRED_COLUMNS)
    if mode == TransitMode.SUBWAY:
        for member, extra_columns in _SUBWAY_REQUIRED_COLUMNS.items():
            columns[member] = (*columns.get(member, ()), *extra_columns)
    return columns


def _open_member(archive: zipfile.ZipFile, member: str) -> io.TextIOWrapper:
    # A byte that is not UTF-8 becomes a replacement character instead of failing the
    # whole feed.
    return io.TextIOWrapper(
        archive.open(member), encoding="utf-8-sig", errors="replace", newline=""
    )


def _column_indexes(header: Sequence[str]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for index, name in enumerate(header):
        indexes.setdefault(name.strip(), index)
    return indexes


def _read_rows(
    archive: zipfile.ZipFile, member: str, columns: Sequence[str]
) -> Iterator[list[str]]:
    """Yield the trimmed cells of the named columns of every data row, in the order
    named. A column the header lacks, or a cell a short row lacks, reads as "".
    """
    with _open_member(archive, member) as file:
        reader = csv.reader(file)
        indexes = _column_indexes(next(reader, []))
        positions = [indexes.get(column) for column in columns]
        present = [position for position in positions if position is not None]
        minimum_length = max(present, default=-1) + 1
        for row in reader:
            if len(row) < minimum_length:
                row.extend([""] * (minimum_length - len(row)))
            yield [
                "" if position is None else row[position].strip()
                for position in positions
            ]


def _read_stops(archive: zipfile.ZipFile, feed: TransitFeed) -> _Stops:
    """Read the subway's stations and their platforms, or every bus stop row."""
    is_subway = feed.mode == TransitMode.SUBWAY
    stops = _Stops()
    for stop_id, name, latitude, longitude, location_type, parent_station in _read_rows(
        archive,
        _STOPS,
        (
            "stop_id",
            "stop_name",
            "stop_lat",
            "stop_lon",
            "location_type",
            "parent_station",
        ),
    ):
        if not stop_id:
            continue
        if is_subway and location_type != _STATION_LOCATION_TYPE:
            # A platform's coordinates are never read: it counts for its station.
            if location_type in _PLATFORM_LOCATION_TYPES and parent_station:
                stops.parent_station_by_platform_id.setdefault(stop_id, parent_station)
            continue
        if (
            len(stop_id) > PUBLISHED_STOP_ID_MAXIMUM_LENGTH
            or len(name) > STOP_NAME_MAXIMUM_LENGTH
        ):
            raise IngestionError(
                f"Stop {stop_id} of the feed {feed.file_name} has a value longer than "
                "its column."
            )
        stops.listed_ids.add(stop_id)
        coordinates = _coordinates(latitude, longitude)
        if coordinates is None:
            stops.skipped_ids.add(stop_id)
            stops.warnings.append(StopFieldWarning(feed.file_name, stop_id))
            continue
        stops.usable.setdefault(stop_id, ParsedStop(stop_id, name, coordinates))
    return stops


def _coordinates(latitude: str, longitude: str) -> Coordinates | None:
    """Return the point, or None when blank, unparseable, or outside the state."""
    try:
        coordinates = Coordinates(
            longitude=Decimal(longitude), latitude=Decimal(latitude)
        )
    except InvalidOperation:
        return None
    if not (
        coordinates.longitude.is_finite()
        and coordinates.latitude.is_finite()
        and is_inside_new_york_state(coordinates)
    ):
        return None
    return coordinates


def _read_routes(archive: zipfile.ZipFile, feed: TransitFeed) -> _Routes:
    is_subway = feed.mode == TransitMode.SUBWAY
    routes: list[tuple[str, str, str, int]] = []
    for route_id, short_name, route_type, sort_order in _read_rows(
        archive,
        _ROUTES,
        ("route_id", "route_short_name", "route_type", "route_sort_order"),
    ):
        if not route_id:
            continue
        if not short_name:
            raise IngestionError(
                f"Route {route_id} of the feed {feed.file_name} has no "
                "route_short_name."
            )
        if len(short_name) > LINE_NAME_MAXIMUM_LENGTH:
            raise IngestionError(
                f"Route {route_id} of the feed {feed.file_name} has a "
                f"route_short_name longer than {LINE_NAME_MAXIMUM_LENGTH} characters."
            )
        if is_subway and not _NON_NEGATIVE_INTEGER.fullmatch(sort_order):
            raise IngestionError(
                f"Route {route_id} of the feed {feed.file_name} has no usable "
                "route_sort_order."
            )
        routes.append(
            (route_id, short_name, route_type, int(sort_order) if is_subway else 0)
        )
    short_names = frozenset(short_name for _, short_name, _, _ in routes)
    line_name_by_route_id: dict[str, str] = {}
    subway_line_sort_orders: dict[str, int] = {}
    for route_id, short_name, route_type, route_sort_order in routes:
        line_name = (
            _subway_line_name(short_name, short_names) if is_subway else short_name
        )
        if is_subway:
            subway_line_sort_orders[line_name] = min(
                route_sort_order,
                subway_line_sort_orders.get(line_name, route_sort_order),
            )
        if route_type != _SHUTTLE_BUS_ROUTE_TYPE:
            line_name_by_route_id[route_id] = line_name
    return _Routes(line_name_by_route_id, subway_line_sort_orders)


def _subway_line_name(short_name: str, short_names: frozenset[str]) -> str:
    """Return the rider-facing line of a route: an express variant such as FX or 6X
    is its line, F or 6; every other route, shuttles included, keeps its short name.
    """
    remainder = short_name.removesuffix(_FOLDED_VARIANT_SUFFIX)
    if remainder and remainder != short_name and remainder in short_names:
        return remainder
    return short_name


def _read_qualifying_services(
    archive: zipfile.ZipFile, feed: TransitFeed, service_date: date
) -> frozenset[str]:
    """Return the services running on a weekday and ending on or after the date.

    calendar_dates.txt is never read: its additions and exceptions are holidays and
    one-off changes, not the regular weekday service.
    """
    services: set[str] = set()
    for service_id, *weekdays, end_date_text in _read_rows(
        archive, _CALENDAR, ("service_id", *_WEEKDAY_COLUMNS, "end_date")
    ):
        end_date = _gtfs_date(end_date_text)
        if (
            service_id
            and _SERVICE_DAY_VALUE in weekdays
            and end_date is not None
            and end_date >= service_date
        ):
            services.add(service_id)
    if not services:
        raise IngestionError(
            f"The feed {feed.file_name} has no weekday service running on or after "
            f"{service_date:%Y-%m-%d}."
        )
    return frozenset(services)


def _gtfs_date(text: str) -> date | None:
    match = _GTFS_DATE.fullmatch(text)
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _read_trip_line_names(
    archive: zipfile.ZipFile, routes: _Routes, services: frozenset[str]
) -> dict[str, str]:
    """Return the line of every trip of a qualifying service on a route in service."""
    line_name_by_trip_id: dict[str, str] = {}
    for route_id, service_id, trip_id in _read_rows(
        archive, _TRIPS, ("route_id", "service_id", "trip_id")
    ):
        line_name = routes.line_name_by_route_id.get(route_id)
        if trip_id and service_id in services and line_name is not None:
            line_name_by_trip_id[trip_id] = line_name
    return line_name_by_trip_id


def _read_line_names_by_stop_id(
    archive: zipfile.ZipFile, stops: _Stops, line_name_by_trip_id: Mapping[str, str]
) -> dict[str, frozenset[str]]:
    """Add the line of every qualifying stop time to its stop, a platform's to its
    station. Stop times of unknown trips or stops are ignored.
    """
    line_names: dict[str, set[str]] = collections.defaultdict(set)
    for (
        trip_id,
        stop_id,
        arrival_time,
        departure_time,
        pickup_type,
        drop_off_type,
    ) in _read_rows(
        archive,
        _STOP_TIMES,
        (
            "trip_id",
            "stop_id",
            "arrival_time",
            "departure_time",
            "pickup_type",
            "drop_off_type",
        ),
    ):
        line_name = line_name_by_trip_id.get(trip_id)
        if line_name is None:
            continue
        if (
            pickup_type == _NO_PICKUP_OR_DROP_OFF
            and drop_off_type == _NO_PICKUP_OR_DROP_OFF
        ):
            continue
        if not _is_in_service_window(departure_time or arrival_time):
            continue
        listed_id = (
            stop_id
            if stop_id in stops.listed_ids
            else stops.parent_station_by_platform_id.get(stop_id)
        )
        if listed_id in stops.listed_ids:
            line_names[listed_id].add(line_name)
    return {stop_id: frozenset(names) for stop_id, names in line_names.items()}


# Stop times repeat a few thousand distinct times millions of times.
@functools.cache
def _is_in_service_window(time_text: str) -> bool:
    """Return whether a GTFS time lies in the weekday daytime window. Times of
    24:00:00 and later belong to the next day's early hours, so they never do.
    """
    match = _GTFS_TIME.fullmatch(time_text)
    if match is None:
        return False
    hours, minutes, seconds = (int(group) for group in match.groups())
    return (
        _SERVICE_WINDOW_START_SECONDS
        <= hours * 3600 + minutes * 60 + seconds
        < _SERVICE_WINDOW_END_SECONDS
    )


def _read_station_transfers(archive: zipfile.ZipFile) -> frozenset[frozenset[str]]:
    """Return the pairs of different stations joined by a transfer; a row from a
    station to itself joins nothing.
    """
    return frozenset(
        frozenset({from_stop_id, to_stop_id})
        for from_stop_id, to_stop_id in _read_rows(
            archive, _TRANSFERS, ("from_stop_id", "to_stop_id")
        )
        if from_stop_id and to_stop_id and from_stop_id != to_stop_id
    )
