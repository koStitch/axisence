"""The report's schematic map, drawn from the database alone: tabulation-area outlines,
the search radius, the target, and numbered competitors. It never fetches tiles or any
external image, and its coordinates are relative to the map, never geographic.
"""

import dataclasses
from collections.abc import Sequence
from decimal import Decimal

from django.contrib.gis.geos import (
    GeometryCollection,
    GEOSGeometry,
    MultiPolygon,
    Point,
    Polygon,
)

from axisence.demographics.models import TabulationArea
from axisence.reports.formatting import format_plain_number, format_radius
from axisence.research.distances import METERS_PER_MILE

# New York Long Island state plane, in US survey feet, which keeps the circle round
# and the scale true at Brooklyn's latitude.
MAP_SRID = 2263
MAP_WIDTH = 1000.0
MAP_HEIGHT = 860.0

_GEOGRAPHIC_SRID = 4326
_US_SURVEY_FEET_PER_METER = 3937 / 1200
_MARGIN_FRACTION = 0.08
_SCALE_BAR_MILES = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0)
_SCALE_BAR_MAXIMUM_FRACTION = 0.25
_EDGE_PADDING = 20.0
# Half a map unit, so simplifying never moves an outline visibly.
_SIMPLIFICATION_MAP_UNITS = 0.5


@dataclasses.dataclass(frozen=True, slots=True)
class MapPoint:
    x: float
    y: float


@dataclasses.dataclass(frozen=True, slots=True)
class MapMarker:
    number: int
    x: float
    y: float


@dataclasses.dataclass(frozen=True, slots=True)
class MapArea:
    # SVG path data, one closed subpath per ring.
    path: str
    code: str
    label_x: float
    label_y: float


@dataclasses.dataclass(frozen=True, slots=True)
class MapCircle:
    x: float
    y: float
    radius: float


@dataclasses.dataclass(frozen=True, slots=True)
class MapScaleBar:
    x: float
    y: float
    length: float
    label: str


@dataclasses.dataclass(frozen=True, slots=True)
class ReportMap:
    width: float
    height: float
    areas: tuple[MapArea, ...]
    radius: MapCircle
    radius_label: str
    target: MapPoint
    markers: tuple[MapMarker, ...]
    scale_bar: MapScaleBar


@dataclasses.dataclass(frozen=True, slots=True)
class _Frame:
    """The projected rectangle the map shows, and its scale to map units."""

    minimum_x: float
    minimum_y: float
    maximum_x: float
    maximum_y: float
    scale: float

    def map_point(self, x: float, y: float) -> MapPoint:
        return MapPoint(
            x=round((x - self.minimum_x) * self.scale, 1),
            y=round((self.maximum_y - y) * self.scale, 1),
        )


def build_report_map(
    target: Point, radius_miles: Decimal, located: Sequence[tuple[int, Point]]
) -> ReportMap:
    """Draw the target, its radius, and the numbered located competitors, in one query.

    located holds the number and location of every main-list competitor that has a
    location.
    """
    target_x, target_y = _project(target)
    radius = float(radius_miles) * METERS_PER_MILE * _US_SURVEY_FEET_PER_METER
    projected = [(number, *_project(location)) for number, location in located]
    frame = _frame(
        [
            (target_x - radius, target_y - radius),
            (target_x + radius, target_y + radius),
            *((x, y) for _, x, y in projected),
        ]
    )
    mapped_target = frame.map_point(target_x, target_y)
    markers = []
    for number, x, y in projected:
        mapped = frame.map_point(x, y)
        markers.append(MapMarker(number=number, x=mapped.x, y=mapped.y))
    return ReportMap(
        width=MAP_WIDTH,
        height=MAP_HEIGHT,
        areas=_areas(frame),
        radius=MapCircle(
            x=mapped_target.x,
            y=mapped_target.y,
            radius=round(radius * frame.scale, 1),
        ),
        radius_label=format_radius(radius_miles),
        target=mapped_target,
        markers=tuple(markers),
        scale_bar=_scale_bar(frame.scale),
    )


def _project(point: Point) -> tuple[float, float]:
    projected = point.transform(MAP_SRID, clone=True)
    if not isinstance(projected, Point):
        raise TypeError("Transforming a point gives a point.")
    return projected.x, projected.y


def _frame(points: Sequence[tuple[float, float]]) -> _Frame:
    """Return the box around the points, with margins, in the map's proportions."""
    minimum_x = min(x for x, _ in points)
    maximum_x = max(x for x, _ in points)
    minimum_y = min(y for _, y in points)
    maximum_y = max(y for _, y in points)
    margin = _MARGIN_FRACTION * max(maximum_x - minimum_x, maximum_y - minimum_y)
    minimum_x -= margin
    maximum_x += margin
    minimum_y -= margin
    maximum_y += margin
    width = maximum_x - minimum_x
    height = maximum_y - minimum_y
    proportion = MAP_WIDTH / MAP_HEIGHT
    if width / height < proportion:
        growth = (height * proportion - width) / 2
        minimum_x -= growth
        maximum_x += growth
    else:
        growth = (width / proportion - height) / 2
        minimum_y -= growth
        maximum_y += growth
    return _Frame(
        minimum_x=minimum_x,
        minimum_y=minimum_y,
        maximum_x=maximum_x,
        maximum_y=maximum_y,
        scale=MAP_WIDTH / (maximum_x - minimum_x),
    )


def _areas(frame: _Frame) -> tuple[MapArea, ...]:
    extent = Polygon.from_bbox(
        (frame.minimum_x, frame.minimum_y, frame.maximum_x, frame.maximum_y)
    )
    extent.srid = MAP_SRID
    areas = []
    for area in TabulationArea.objects.filter(
        boundary__intersects=extent.transform(_GEOGRAPHIC_SRID, clone=True)
    ).order_by("code"):
        if area.boundary is None:
            continue
        clipped = area.boundary.transform(MAP_SRID, clone=True).intersection(extent)
        # Where boundaries touch the extent, the intersection can also hold lines
        # and points; only its polygons are drawn.
        polygons = _polygons(clipped)
        if not polygons:
            continue
        outline = MultiPolygon(polygons, srid=MAP_SRID)
        simplified = outline.simplify(
            _SIMPLIFICATION_MAP_UNITS / frame.scale, preserve_topology=True
        )
        path = _path(_polygons(simplified), frame)
        if not path:
            continue
        label_point = outline.point_on_surface
        label = frame.map_point(label_point.x, label_point.y)
        areas.append(
            MapArea(path=path, code=area.code, label_x=label.x, label_y=label.y)
        )
    return tuple(areas)


def _polygons(geometry: GEOSGeometry) -> list[Polygon]:
    if geometry.empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, GeometryCollection):
        return [polygon for member in geometry for polygon in _polygons(member)]
    return []


def _path(polygons: Sequence[Polygon], frame: _Frame) -> str:
    subpaths = []
    for polygon in polygons:
        for ring in polygon:
            # The stubs type a ring's coordinates as a one-element tuple.
            coordinates: Sequence[Sequence[float]] = ring.coords
            # A ring repeats its first coordinate last; Z closes the subpath instead.
            mapped = [
                frame.map_point(coordinate[0], coordinate[1])
                for coordinate in coordinates[:-1]
            ]
            if len(mapped) < 3:
                continue
            first, *rest = mapped
            subpaths.append(
                " ".join(
                    (
                        f"M {first.x:.1f} {first.y:.1f}",
                        *(f"L {point.x:.1f} {point.y:.1f}" for point in rest),
                        "Z",
                    )
                )
            )
    return " ".join(subpaths)


def _scale_bar(scale: float) -> MapScaleBar:
    """Return the longest round distance that fits a quarter of the map's width."""

    def mapped_length(miles: float) -> float:
        return miles * METERS_PER_MILE * _US_SURVEY_FEET_PER_METER * scale

    fitting = [
        miles
        for miles in _SCALE_BAR_MILES
        if mapped_length(miles) <= _SCALE_BAR_MAXIMUM_FRACTION * MAP_WIDTH
    ]
    miles = max(fitting) if fitting else min(_SCALE_BAR_MILES)
    return MapScaleBar(
        x=_EDGE_PADDING,
        y=MAP_HEIGHT - _EDGE_PADDING,
        length=round(mapped_length(miles), 1),
        label=f"{format_plain_number(Decimal(str(miles)))} mi",
    )
