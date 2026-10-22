"""The subway stations and bus lines near points, in two queries whatever the number
of points and stops.

Distances are straight lines on the WGS 84 ellipsoid, measured in PostGIS on the
geography column.
"""

import collections
import dataclasses
import functools
import operator
from collections.abc import Sequence

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db.models import Q

from axisence.transit.models import TransitMode, TransitStop

# The standard walking catchments of transit planning.
SUBWAY_CATCHMENT_METERS = D(mi=0.5).m
BUS_CATCHMENT_METERS = D(mi=0.25).m
STATION_GROUP_LIMIT = 2
# The spatial prefilter reaches a little beyond each catchment, so the inclusive limit
# is applied to the measured distance alone.
_PREFILTER_MARGIN_METERS = 1.0


@dataclasses.dataclass(frozen=True, slots=True)
class NearbyStation:
    name: str
    lines: tuple[str, ...]
    distance_meters: float


@dataclasses.dataclass(frozen=True, slots=True)
class NearbyBusLine:
    name: str
    distance_meters: float


@dataclasses.dataclass(frozen=True, slots=True)
class NearbyTransit:
    stations: tuple[NearbyStation, ...]
    bus_lines: tuple[NearbyBusLine, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _FoundStop:
    mode: str
    published_stop_id: str
    name: str
    station_group_key: str
    distances: tuple[float, ...]
    # (sort position, name) of each line.
    lines: tuple[tuple[int, str], ...]


def find_nearby_transit(points: Sequence[Point]) -> tuple[NearbyTransit, ...]:
    """Return, for each point in order, the nearest station groups within the subway
    catchment and every bus line with a stop within the bus catchment.

    Only active stops with at least one line count, and both limits are inclusive.
    """
    if not points:
        return ()
    subway_catchment = SUBWAY_CATCHMENT_METERS
    bus_catchment = BUS_CATCHMENT_METERS
    subway_in_range = TransitStop.objects.filter(
        _within_any(points, subway_catchment),
        is_active=True,
        mode=TransitMode.SUBWAY,
    ).values("station_group_key")
    # Every active member of a group with a member in range, because a group's lines
    # are those of all its members.
    nearby_stops = TransitStop.objects.filter(is_active=True).filter(
        (Q(mode=TransitMode.BUS) & _within_any(points, bus_catchment))
        | Q(mode=TransitMode.SUBWAY, station_group_key__in=subway_in_range)
    )
    distance_names = [f"distance_{index}" for index in range(len(points))]
    rows = list(
        nearby_stops.annotate(
            **{
                name: Distance("location", point)
                for name, point in zip(distance_names, points, strict=True)
            }
        ).values_list(
            "pk",
            "mode",
            "published_stop_id",
            "name",
            "station_group_key",
            *distance_names,
        )
    )
    lines_by_stop_id: dict[int, list[tuple[int, str]]] = collections.defaultdict(list)
    for stop_id, line_name, sort_position in TransitStop.lines.through.objects.filter(
        transitstop_id__in=nearby_stops.values("pk")
    ).values_list("transitstop_id", "transitline__name", "transitline__sort_position"):
        lines_by_stop_id[stop_id].append((sort_position, line_name))
    found = [
        _FoundStop(
            mode=mode,
            published_stop_id=published_stop_id,
            name=name,
            station_group_key=station_group_key,
            distances=tuple(distance.m for distance in distances),
            lines=tuple(sorted(lines_by_stop_id[pk])),
        )
        for pk, mode, published_stop_id, name, station_group_key, *distances in rows
        if lines_by_stop_id[pk]
    ]
    return tuple(
        NearbyTransit(
            stations=_nearby_stations(found, index, subway_catchment),
            bus_lines=_nearby_bus_lines(found, index, bus_catchment),
        )
        for index in range(len(points))
    )


def _within_any(points: Sequence[Point], catchment_meters: float) -> Q:
    return functools.reduce(
        operator.or_,
        (
            Q(
                location__dwithin=(
                    point,
                    D(m=catchment_meters + _PREFILTER_MARGIN_METERS),
                )
            )
            for point in points
        ),
    )


def _nearby_stations(
    found: Sequence[_FoundStop], index: int, catchment_meters: float
) -> tuple[NearbyStation, ...]:
    """Return the nearest station groups in range of the point, each at its nearest
    member's distance and name, with the lines of all its members.
    """
    members_by_group: dict[str, list[_FoundStop]] = collections.defaultdict(list)
    for stop in found:
        if stop.mode == TransitMode.SUBWAY:
            members_by_group[stop.station_group_key].append(stop)
    stations: list[tuple[float, str, str, NearbyStation]] = []
    for group_key, members in members_by_group.items():
        nearest = min(
            members,
            key=lambda member: (
                member.distances[index],
                member.name,
                member.published_stop_id,
            ),
        )
        distance = nearest.distances[index]
        if distance > catchment_meters:
            continue
        lines = sorted({line for member in members for line in member.lines})
        station = NearbyStation(
            name=nearest.name,
            lines=tuple(dict.fromkeys(name for _, name in lines)),
            distance_meters=distance,
        )
        stations.append((distance, nearest.name, group_key, station))
    stations.sort(key=operator.itemgetter(0, 1, 2))
    return tuple(station for *_, station in stations[:STATION_GROUP_LIMIT])


def _nearby_bus_lines(
    found: Sequence[_FoundStop], index: int, catchment_meters: float
) -> tuple[NearbyBusLine, ...]:
    """Return every bus line with a stop in range, once, at its nearest stop."""
    nearest_by_line: dict[tuple[int, str], float] = {}
    for stop in found:
        distance = stop.distances[index]
        if stop.mode != TransitMode.BUS or distance > catchment_meters:
            continue
        for line in stop.lines:
            nearest_by_line[line] = min(distance, nearest_by_line.get(line, distance))
    ordered = sorted(
        nearest_by_line.items(),
        key=lambda item: (item[1], item[0]),
    )
    return tuple(
        NearbyBusLine(name=name, distance_meters=distance)
        for (_, name), distance in ordered
    )
