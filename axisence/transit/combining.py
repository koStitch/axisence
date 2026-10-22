"""Combine the parsed feeds into the stops to store: one per mode and published stop ID,
subway stations grouped into station groups, and the lines that serve them.

Combining is pure: it never touches the database.
"""

import collections
import dataclasses
import re
from collections.abc import Iterable, Mapping, Sequence

from axisence.ingestion.coordinates import Coordinates
from axisence.ingestion.errors import IngestionError
from axisence.transit.feeds import SUBWAY_FEED
from axisence.transit.models import STATION_GROUP_KEY_MAXIMUM_LENGTH, TransitMode
from axisence.transit.parsing import ParsedFeed, ParsedStop

STATION_GROUP_KEY_SEPARATOR = "+"

# With a capturing group, re.split puts every digit run at an odd index.
_DIGIT_RUNS = re.compile(r"([0-9]+)")
_STATIONS_PER_TRANSFER = 2


@dataclasses.dataclass(frozen=True, slots=True)
class DesiredStop:
    mode: TransitMode
    published_stop_id: str
    name: str
    coordinates: Coordinates
    line_names: frozenset[str]
    # Subway only; "" for bus stops.
    station_group_key: str


@dataclasses.dataclass(frozen=True, slots=True)
class CombinedFeeds:
    stops: tuple[DesiredStop, ...]
    # Mode values and stop IDs of stops whose rows every feed listing them skipped;
    # they stay as stored.
    untouched_stop_keys: frozenset[tuple[str, str]]
    # Each subway line serving a stored station, with its smallest route_sort_order.
    subway_line_sort_positions: Mapping[str, int]
    bus_line_names: frozenset[str]


def natural_order_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """Return a key ordering names by their text, ignoring case, and by the value of
    their numbers: B2 before B11, B44 before B44-SBS, B103 before BM1, B1 before Bx1.
    """
    return tuple(
        (1, int(chunk), "") if index % 2 else (0, 0, chunk.casefold())
        for index, chunk in enumerate(_DIGIT_RUNS.split(name))
        if chunk
    )


def combine_feeds(parsed_feeds: Sequence[ParsedFeed]) -> CombinedFeeds:
    """Combine the feeds, given in the order of feeds.FEEDS."""
    subway_feeds = [
        parsed for parsed in parsed_feeds if parsed.feed.mode == TransitMode.SUBWAY
    ]
    bus_feeds = [
        parsed for parsed in parsed_feeds if parsed.feed.mode == TransitMode.BUS
    ]
    stations = _merge_stops(TransitMode.SUBWAY, subway_feeds)
    group_keys = _station_group_keys(
        stations, (pair for parsed in subway_feeds for pair in parsed.station_transfers)
    )
    stations = [
        dataclasses.replace(
            station, station_group_key=group_keys[station.published_stop_id]
        )
        for station in stations
    ]
    bus_stops = _merge_stops(TransitMode.BUS, bus_feeds)
    served_subway_line_names = {
        name for station in stations for name in station.line_names
    }
    return CombinedFeeds(
        stops=(*stations, *bus_stops),
        untouched_stop_keys=_untouched_stop_keys(TransitMode.SUBWAY, subway_feeds)
        | _untouched_stop_keys(TransitMode.BUS, bus_feeds),
        subway_line_sort_positions={
            name: sort_order
            for parsed in subway_feeds
            for name, sort_order in parsed.subway_line_sort_orders.items()
            if name in served_subway_line_names
        },
        bus_line_names=frozenset(
            name for stop in bus_stops for name in stop.line_names
        ),
    )


def _merge_stops(
    mode: TransitMode, parsed_feeds: Sequence[ParsedFeed]
) -> list[DesiredStop]:
    """Return one stop per ID usable in any feed: its name and position from the first
    feed holding it, its lines from every feed.
    """
    first_by_id: dict[str, ParsedStop] = {}
    for parsed in parsed_feeds:
        for stop in parsed.stops:
            first_by_id.setdefault(stop.published_stop_id, stop)
    return [
        DesiredStop(
            mode=mode,
            published_stop_id=stop_id,
            name=stop.name,
            coordinates=stop.coordinates,
            line_names=frozenset(
                name
                for parsed in parsed_feeds
                for name in parsed.line_names_by_stop_id.get(stop_id, frozenset())
            ),
            station_group_key="",
        )
        for stop_id, stop in first_by_id.items()
    ]


def _untouched_stop_keys(
    mode: TransitMode, parsed_feeds: Sequence[ParsedFeed]
) -> frozenset[tuple[str, str]]:
    usable = {
        stop.published_stop_id for parsed in parsed_feeds for stop in parsed.stops
    }
    return frozenset(
        (mode.value, stop_id)
        for parsed in parsed_feeds
        for stop_id in parsed.skipped_stop_ids
        if stop_id not in usable
    )


def _station_group_keys(
    stations: Sequence[DesiredStop], transfers: Iterable[frozenset[str]]
) -> dict[str, str]:
    """Group stations of one name joined by a transfer, transitively, and return each
    station's group key: its members' IDs sorted as text and joined by "+".
    """
    names = {station.published_stop_id: station.name for station in stations}
    parents = {stop_id: stop_id for stop_id in names}

    def root(stop_id: str) -> str:
        while parents[stop_id] != stop_id:
            parents[stop_id] = parents[parents[stop_id]]
            stop_id = parents[stop_id]
        return stop_id

    for pair in transfers:
        # A station's transfer to itself is a pair of one, which joins nothing.
        if len(pair) != _STATIONS_PER_TRANSFER:
            continue
        first, second = sorted(pair)
        if first in names and second in names and names[first] == names[second]:
            parents[root(first)] = root(second)
    members: dict[str, list[str]] = collections.defaultdict(list)
    for stop_id in names:
        members[root(stop_id)].append(stop_id)
    keys: dict[str, str] = {}
    for group in members.values():
        key = STATION_GROUP_KEY_SEPARATOR.join(sorted(group))
        if len(key) > STATION_GROUP_KEY_MAXIMUM_LENGTH:
            raise IngestionError(
                f"A station group of the feed {SUBWAY_FEED.file_name} has a key longer "
                f"than {STATION_GROUP_KEY_MAXIMUM_LENGTH} characters."
            )
        for stop_id in group:
            keys[stop_id] = key
    return keys
