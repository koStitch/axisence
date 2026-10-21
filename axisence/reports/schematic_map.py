"""The report's schematic map, drawn from the database alone: tabulation-area outlines,
the search radius, the target, numbered competitors, and plain dots. It never fetches
tiles or any external image, and its coordinates are relative to the map, never
geographic.

Given the sizes its template draws marks at, the map also keeps them apart: a number
that would cover another mark moves beside its competitor's position, joined to it by a
line, and an area code moves within its area, or is left out, where it would be cut
off or cover another mark.
"""

import dataclasses
import math
from collections.abc import Iterator, Sequence
from decimal import Decimal

from django.contrib.gis.gdal import CoordTransform, SpatialReference
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
# The clear space kept between marks, in map units.
_MARK_GAP = 4.0
# A moved number tries 24 directions, nearest to pointing away from the target first,
# on rings one marker radius apart.
_MOVE_DIRECTION_COUNT = 24
_MOVE_RING_COUNT = 16
# How many times the numbers are placed again, the crowded ones first.
_LAYOUT_RETRY_COUNT = 8
# How much of a moved number's line shows between its position's dot and the number
# on the nearest ring, about 2pt on the teaser.
_VISIBLE_LINE_LENGTH = 10.0
# How much nearer its own position a moved number should lie than any other position,
# so which dot is its own is not in doubt.
_OWNERSHIP_MARGIN = 10.0
# Inter's widest digit is 0.646 of its font size and its capital height 0.728; area
# codes are digits only.
_DIGIT_WIDTH_FRACTION = 0.65
_CAPITAL_HEIGHT_FRACTION = 0.73
# The spacing, in map units, of the spots tried inside an area for its code.
_LABEL_SEARCH_STEP = 16.0
# How much farther than the nearest fitting spot a code may go to stay clear of the
# plain dots and the radius circle.
_LABEL_PREFERENCE_REACH = 3 * _LABEL_SEARCH_STEP


@dataclasses.dataclass(frozen=True, slots=True)
class MarkSizes:
    """The sizes, in map units, at which a map's template draws its marks, so the map
    can keep them apart.
    """

    marker_radius: float
    # The dot that shows a competitor's position when its number moved beside it.
    anchor_radius: float
    # The plain dot of a competitor drawn without a number.
    dot_radius: float
    # Half the side of the target's square, which the template turns by 45 degrees.
    target_half_side: float
    # The font size of the area codes, the numbers, and the scale bar's label.
    text_font_size: float
    # How far above the scale bar its label's baseline sits.
    scale_label_raise: float

    @property
    def target_side(self) -> float:
        return 2 * self.target_half_side

    @property
    def number_baseline_offset(self) -> float:
        """Return how far below a number's center its baseline sits, so the digits
        are centered in their circle.
        """
        return round(_CAPITAL_HEIGHT_FRACTION * self.text_font_size / 2, 1)


@dataclasses.dataclass(frozen=True, slots=True)
class MapPoint:
    x: float
    y: float


@dataclasses.dataclass(frozen=True, slots=True)
class MapMarker:
    number: int
    # Where the number is drawn.
    x: float
    y: float
    # The competitor's position, where the number is drawn unless it moved to stay
    # readable.
    anchor_x: float
    anchor_y: float

    @property
    def position(self) -> MapPoint:
        return MapPoint(x=self.x, y=self.y)

    @property
    def anchor(self) -> MapPoint:
        return MapPoint(x=self.anchor_x, y=self.anchor_y)

    @property
    def is_moved(self) -> bool:
        return self.position != self.anchor


@dataclasses.dataclass(frozen=True, slots=True)
class MapArea:
    # SVG path data, one closed subpath per ring.
    path: str
    code: str
    label_x: float
    label_y: float
    # False when the map keeps its marks apart and this code would not fit.
    is_labelled: bool


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
    # Competitors drawn as plain, unnumbered dots beneath the numbered markers.
    dots: tuple[MapPoint, ...]
    scale_bar: MapScaleBar
    # The sizes the marks were kept apart for; None when they were not.
    mark_sizes: MarkSizes | None


@dataclasses.dataclass(frozen=True, slots=True)
class _Box:
    """A rectangle in map units, y growing downward."""

    left: float
    top: float
    right: float
    bottom: float

    def overlaps(self, other: _Box) -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    def is_inside_map(self) -> bool:
        return (
            self.left >= 0
            and self.top >= 0
            and self.right <= MAP_WIDTH
            and self.bottom <= MAP_HEIGHT
        )

    def distance_to(self, point: MapPoint) -> float:
        across = max(self.left - point.x, 0.0, point.x - self.right)
        down = max(self.top - point.y, 0.0, point.y - self.bottom)
        return math.hypot(across, down)

    def meets_ring(self, center: MapPoint, radius: float, margin: float) -> bool:
        """Return whether any part of the box lies within the margin of the circle's
        line.
        """
        farthest = max(
            math.dist((center.x, center.y), corner)
            for corner in (
                (self.left, self.top),
                (self.right, self.top),
                (self.left, self.bottom),
                (self.right, self.bottom),
            )
        )
        return self.distance_to(center) <= radius + margin and farthest >= (
            radius - margin
        )

    def crosses_segment(self, start: MapPoint, end: MapPoint) -> bool:
        """Return whether any part of the segment lies inside the box."""
        # Clips the segment's parameter range to each of the four sides in turn.
        run = end.x - start.x
        rise = end.y - start.y
        entering, leaving = 0.0, 1.0
        for direction, room in (
            (-run, start.x - self.left),
            (run, self.right - start.x),
            (-rise, start.y - self.top),
            (rise, self.bottom - start.y),
        ):
            if direction == 0:
                if room < 0:
                    return False
                continue
            limit = room / direction
            if direction < 0:
                entering = max(entering, limit)
            else:
                leaving = min(leaving, limit)
            if entering > leaving:
                return False
        return True


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
    target: Point,
    radius_miles: Decimal,
    located: Sequence[tuple[int, Point]],
    unnumbered: Sequence[Point] = (),
    mark_sizes: MarkSizes | None = None,
) -> ReportMap:
    """Draw the target, its radius, the numbered competitors, and a plain dot for each
    unnumbered one, in one query.

    located holds the number and location of every competitor drawn with its number;
    unnumbered holds the location of every competitor drawn as a dot. With mark_sizes,
    a number that would cover another mark moves beside its own position, and an area
    code is drawn only where it fits inside the map clear of the other marks; see
    _markers_kept_apart and _fitting_labels.
    """
    to_map = CoordTransform(
        SpatialReference(_GEOGRAPHIC_SRID), SpatialReference(MAP_SRID)
    )
    target_x, target_y = _project(target, to_map)
    radius = float(radius_miles) * METERS_PER_MILE * _US_SURVEY_FEET_PER_METER
    projected = [(number, *_project(location, to_map)) for number, location in located]
    projected_dots = [_project(location, to_map) for location in unnumbered]
    frame = _frame(
        [
            (target_x - radius, target_y - radius),
            (target_x + radius, target_y + radius),
            *((x, y) for _, x, y in projected),
            *projected_dots,
        ]
    )
    mapped_target = frame.map_point(target_x, target_y)
    anchors = [(number, frame.map_point(x, y)) for number, x, y in projected]
    circle = MapCircle(
        x=mapped_target.x, y=mapped_target.y, radius=round(radius * frame.scale, 1)
    )
    dots = tuple(frame.map_point(x, y) for x, y in projected_dots)
    scale_bar = _scale_bar(frame.scale)
    if mark_sizes is None:
        markers = tuple(
            MapMarker(
                number=number,
                x=anchor.x,
                y=anchor.y,
                anchor_x=anchor.x,
                anchor_y=anchor.y,
            )
            for number, anchor in anchors
        )
        areas = _areas(frame, markers, None)
    else:
        fixed = _FixedMarks(
            target=mapped_target,
            radius=circle,
            scale_bar=_scale_bar_box(scale_bar, mark_sizes),
            dots=dots,
            mark_sizes=mark_sizes,
        )
        markers = _markers_kept_apart(anchors, fixed)
        areas = _areas(frame, markers, fixed)
    return ReportMap(
        width=MAP_WIDTH,
        height=MAP_HEIGHT,
        areas=areas,
        radius=circle,
        radius_label=format_radius(radius_miles),
        target=mapped_target,
        markers=markers,
        dots=dots,
        scale_bar=scale_bar,
        mark_sizes=mark_sizes,
    )


def _project(point: Point, to_map: CoordTransform) -> tuple[float, float]:
    # Geographic points share one prepared transformation, as preparing one per point
    # is slow; a point in any other reference system is transformed from its own.
    projected = point.transform(
        to_map if point.srid == _GEOGRAPHIC_SRID else MAP_SRID, clone=True
    )
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


@dataclasses.dataclass(frozen=True, slots=True)
class _FixedMarks:
    """The marks that never move, and the sizes every mark is drawn at."""

    target: MapPoint
    radius: MapCircle
    scale_bar: _Box
    # The unnumbered competitors' dots.
    dots: tuple[MapPoint, ...]
    mark_sizes: MarkSizes

    @property
    def target_corner_distance(self) -> float:
        # The turned square's corners lie half its diagonal from its center.
        return self.mark_sizes.target_half_side * math.sqrt(2)


def _markers_kept_apart(
    anchors: Sequence[tuple[int, MapPoint]], fixed: _FixedMarks
) -> tuple[MapMarker, ...]:
    """Draw each number on its competitor's position or, where it would cover another
    mark, on the nearest free spot beside it, joined to the position by a line.

    Numbers are placed in their order, so the nearest competitors move least. A number
    stays inside the map, clear of the target, the scale bar, every other numbered
    position, and the numbers and lines placed before it. A moved number's line keeps
    clear of the numbers placed before it and of every other position, except those so
    near its own that the line must start beside them, crosses no other line, and keeps
    clear of the target unless it starts beside it, in which case it heads away. Among
    such spots up to one ring beyond the nearest, one lying clearly nearer the number's
    own position than any other is preferred, and then one that leaves every plain
    dot's center uncovered.

    A number with no such spot is crowded. The numbers are then placed again with the
    crowded ones first, a few times, and the layout with the fewest crowded numbers is
    kept. A crowded number keeps the clearances of the second sentence except those from
    the lines placed before it, and where no spot meets them either, it stays on its
    position.
    """
    best = layout = _NumberLayout(anchors, fixed, order=anchors)
    for _ in range(_LAYOUT_RETRY_COUNT):
        if not layout.crowded_numbers:
            break
        crowded = layout.crowded_numbers
        layout = _NumberLayout(
            anchors,
            fixed,
            order=[item for item in layout.order if item[0] in crowded]
            + [item for item in layout.order if item[0] not in crowded],
        )
        if len(layout.crowded_numbers) < len(best.crowded_numbers):
            best = layout
    return best.markers


class _NumberLayout:
    """The numbers placed in the given order, and the clearances, in map units, each
    keeps from those placed before it.
    """

    def __init__(
        self,
        anchors: Sequence[tuple[int, MapPoint]],
        fixed: _FixedMarks,
        *,
        order: Sequence[tuple[int, MapPoint]],
    ) -> None:
        sizes = fixed.mark_sizes
        self.order = order
        self.crowded_numbers: set[int] = set()
        self._anchors = anchors
        self._fixed = fixed
        self._placed: list[MapMarker] = []
        self._number_from_number = 2 * sizes.marker_radius + _MARK_GAP
        self._number_from_anchor = sizes.marker_radius + sizes.anchor_radius + _MARK_GAP
        self._number_from_target = (
            sizes.marker_radius + fixed.target_corner_distance + _MARK_GAP
        )
        self._number_from_edge = sizes.marker_radius
        self._number_from_dot = sizes.marker_radius + _MARK_GAP
        self._line_from_number = sizes.marker_radius + _MARK_GAP
        # A position this near its line's start lies within its clearance however the
        # line runs, so it is exempt.
        self._line_from_anchor = sizes.anchor_radius + _MARK_GAP
        self._first_ring_distance = self._number_from_anchor + _VISIBLE_LINE_LENGTH
        for number, anchor in order:
            self._place(number, anchor)

    @property
    def markers(self) -> tuple[MapMarker, ...]:
        """Return the markers in the anchors' order, whatever order placed them."""
        by_number = {marker.number: marker for marker in self._placed}
        return tuple(by_number[number] for number, _ in self._anchors)

    def _place(self, number: int, anchor: MapPoint) -> None:
        other_anchors = [
            other for other_number, other in self._anchors if other_number != number
        ]
        candidates = list(
            _candidate_positions(
                anchor,
                self._fixed.target,
                first_distance=self._first_ring_distance,
                step=self._fixed.mark_sizes.marker_radius,
            )
        )
        lines = [
            (other.anchor, other.position) for other in self._placed if other.is_moved
        ]
        distinct_anchors = [
            other
            for other in other_anchors
            if _distance(other, anchor) >= self._line_from_anchor
        ]
        clear: list[MapPoint] = []
        for candidate in candidates:
            # A preferred spot is worth at most one more ring of line.
            if (
                clear
                and _distance(candidate, anchor)
                > _distance(clear[0], anchor) + self._fixed.mark_sizes.marker_radius
            ):
                break
            if self._is_number_clear(
                candidate, other_anchors
            ) and self._is_moved_number_clear(
                anchor, candidate, lines, distinct_anchors
            ):
                clear.append(candidate)
                if candidate == anchor:
                    break
        if clear:
            # A clear position of its own ends the search alone, so only moved spots
            # compete here: the first of the most preferred, so the nearest of them.
            position = max(
                clear,
                key=lambda candidate: (
                    self._is_plainly_owned(anchor, candidate, distinct_anchors),
                    self._leaves_dots_visible(candidate),
                ),
            )
        else:
            self.crowded_numbers.add(number)
            position = next(
                (
                    candidate
                    for candidate in candidates
                    if self._is_number_clear(candidate, other_anchors)
                ),
                anchor,
            )
        self._placed.append(
            MapMarker(
                number=number,
                x=position.x,
                y=position.y,
                anchor_x=anchor.x,
                anchor_y=anchor.y,
            )
        )

    def _is_number_clear(
        self, candidate: MapPoint, other_anchors: Sequence[MapPoint]
    ) -> bool:
        edge = self._number_from_edge
        return (
            edge <= candidate.x <= MAP_WIDTH - edge
            and edge <= candidate.y <= MAP_HEIGHT - edge
            and _distance(candidate, self._fixed.target) >= self._number_from_target
            and self._fixed.scale_bar.distance_to(candidate)
            >= self._fixed.mark_sizes.marker_radius + _MARK_GAP
            and all(
                _distance(candidate, other.position) >= self._number_from_number
                for other in self._placed
            )
            and all(
                _distance(candidate, other) >= self._number_from_anchor
                for other in other_anchors
            )
        )

    def _is_moved_number_clear(
        self,
        anchor: MapPoint,
        candidate: MapPoint,
        lines: Sequence[tuple[MapPoint, MapPoint]],
        distinct_anchors: Sequence[MapPoint],
    ) -> bool:
        """Return whether the number at the candidate stays off the lines placed
        before it and, when it moved, whether its own line stays clear.

        lines holds the lines placed before it; distinct_anchors the other positions,
        without those whose dots touch its own.
        """
        if any(
            _distance_to_segment(candidate, start, end) < self._line_from_number
            for start, end in lines
        ):
            return False
        if candidate == anchor:
            return True
        target = self._fixed.target
        return (
            all(
                _distance_to_segment(other.position, anchor, candidate)
                >= self._line_from_number
                for other in self._placed
            )
            and all(
                _distance_to_segment(other, anchor, candidate) >= self._line_from_anchor
                for other in distinct_anchors
            )
            and not any(
                _segments_cross(anchor, candidate, start, end) for start, end in lines
            )
            and _distance_to_segment(target, anchor, candidate)
            >= min(
                self._fixed.target_corner_distance + _MARK_GAP,
                _distance(anchor, target),
            )
        )

    def _is_plainly_owned(
        self,
        anchor: MapPoint,
        candidate: MapPoint,
        distinct_anchors: Sequence[MapPoint],
    ) -> bool:
        """Return whether a moved number lies clearly nearer its own position than
        any distinct other, so which dot is its own is never in doubt.
        """
        own_distance = _distance(candidate, anchor)
        return all(
            _distance(candidate, other) >= own_distance + _OWNERSHIP_MARGIN
            for other in distinct_anchors
        )

    def _leaves_dots_visible(self, candidate: MapPoint) -> bool:
        """Return whether a moved number leaves every plain dot's center uncovered."""
        return all(
            _distance(candidate, dot) >= self._number_from_dot
            for dot in self._fixed.dots
        )


def _candidate_positions(
    anchor: MapPoint, target: MapPoint, *, first_distance: float, step: float
) -> Iterator[MapPoint]:
    """Yield the anchor itself, then spots on rings around it, each ring's spots
    nearest to pointing away from the target first.
    """
    yield anchor
    if _distance(anchor, target) > 0:
        outward = math.atan2(anchor.y - target.y, anchor.x - target.x)
    else:
        # Straight up, as the map's y grows downward.
        outward = -math.pi / 2
    step_angle = 2 * math.pi / _MOVE_DIRECTION_COUNT
    offsets = [0.0]
    for index in range(1, _MOVE_DIRECTION_COUNT // 2):
        offsets.extend((index * step_angle, -index * step_angle))
    offsets.append(math.pi)
    for ring in range(_MOVE_RING_COUNT):
        distance = first_distance + ring * step
        for offset in offsets:
            angle = outward + offset
            yield MapPoint(
                x=round(anchor.x + distance * math.cos(angle), 1),
                y=round(anchor.y + distance * math.sin(angle), 1),
            )


def _distance(first: MapPoint, second: MapPoint) -> float:
    return math.dist((first.x, first.y), (second.x, second.y))


def _distance_to_segment(point: MapPoint, start: MapPoint, end: MapPoint) -> float:
    run = end.x - start.x
    rise = end.y - start.y
    length_squared = run * run + rise * rise
    if length_squared == 0:
        return _distance(point, start)
    along = ((point.x - start.x) * run + (point.y - start.y) * rise) / length_squared
    along = min(max(along, 0.0), 1.0)
    return math.dist(
        (point.x, point.y), (start.x + along * run, start.y + along * rise)
    )


def _segments_cross(
    first_start: MapPoint,
    first_end: MapPoint,
    second_start: MapPoint,
    second_end: MapPoint,
) -> bool:
    """Return whether two segments cross at a point inside both; segments that only
    touch, such as two lines from one position, do not cross.
    """
    return (
        _turn(first_start, first_end, second_start)
        * _turn(first_start, first_end, second_end)
        < 0
        and _turn(second_start, second_end, first_start)
        * _turn(second_start, second_end, first_end)
        < 0
    )


def _turn(start: MapPoint, end: MapPoint, point: MapPoint) -> float:
    """Return a value whose sign tells on which side of the line through start and end
    the point lies, and zero when it lies on it.
    """
    return (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (
        point.x - start.x
    )


def _scale_bar_box(scale_bar: MapScaleBar, mark_sizes: MarkSizes) -> _Box:
    """Return the box from the map's bottom-left corner around the scale bar and its
    label, which no number or code may cover.
    """
    font_size = mark_sizes.text_font_size
    # Digits are the label's widest characters, so this is an upper bound.
    label_width = len(scale_bar.label) * _DIGIT_WIDTH_FRACTION * font_size
    return _Box(
        left=0.0,
        top=scale_bar.y
        - mark_sizes.scale_label_raise
        - _CAPITAL_HEIGHT_FRACTION * font_size
        - _MARK_GAP,
        right=scale_bar.x + max(scale_bar.length, label_width) + _MARK_GAP,
        bottom=MAP_HEIGHT,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _VisibleArea:
    path: str
    code: str
    # Its part inside the map, in the map's projected reference system.
    outline: MultiPolygon
    label: MapPoint


def _areas(
    frame: _Frame, markers: Sequence[MapMarker], fixed: _FixedMarks | None
) -> tuple[MapArea, ...]:
    visible = _visible_areas(frame)
    if fixed is None:
        labels = {area.code: area.label for area in visible}
    else:
        labels = _fitting_labels(visible, frame, markers, fixed)
    return tuple(
        MapArea(
            path=area.path,
            code=area.code,
            label_x=labels.get(area.code, area.label).x,
            label_y=labels.get(area.code, area.label).y,
            is_labelled=area.code in labels,
        )
        for area in visible
    )


def _fitting_labels(
    areas: Sequence[_VisibleArea],
    frame: _Frame,
    markers: Sequence[MapMarker],
    fixed: _FixedMarks,
) -> dict[str, MapPoint]:
    """Return where each code that fits is drawn: its area's own label point, or else
    the nearest spot inside its area, so the code lies inside the map, clear of the
    target, the scale bar, the numbers, their lines and the positions they moved from,
    and the codes placed before it. Among such spots up to three search steps farther
    than the nearest, one also clear of the plain dots and the radius circle is
    preferred. Areas with a larger part inside the map are placed first. A code with no
    such spot is left out.
    """
    sizes = fixed.mark_sizes
    taken = [
        _box_around(fixed.target, fixed.target_corner_distance + _MARK_GAP),
        fixed.scale_bar,
    ]
    lines = []
    for marker in markers:
        taken.append(_box_around(marker.position, sizes.marker_radius + _MARK_GAP))
        if marker.is_moved:
            taken.append(_box_around(marker.anchor, sizes.anchor_radius + _MARK_GAP))
            lines.append((marker.anchor, marker.position))
    dot_boxes = [_box_around(dot, sizes.dot_radius) for dot in fixed.dots]
    radius = fixed.radius
    circle_center = MapPoint(x=radius.x, y=radius.y)
    font_size = sizes.text_font_size
    labels = {}
    for area in sorted(areas, key=lambda area: (-area.outline.area, area.code)):
        # The template centers a code on its label point, with its baseline there.
        half_width = len(area.code) * _DIGIT_WIDTH_FRACTION * font_size / 2 + _MARK_GAP
        first_fitting = None
        for spot in _label_spots(area, frame, font_size):
            # Staying clear of the dots and the circle is worth a short move only.
            if (
                first_fitting is not None
                and _distance(spot, area.label)
                > _distance(first_fitting[0], area.label) + _LABEL_PREFERENCE_REACH
            ):
                break
            box = _Box(
                left=spot.x - half_width,
                top=spot.y - _CAPITAL_HEIGHT_FRACTION * font_size - _MARK_GAP,
                right=spot.x + half_width,
                bottom=spot.y + _MARK_GAP,
            )
            if not (
                box.is_inside_map()
                and not any(box.overlaps(other) for other in taken)
                and not any(box.crosses_segment(start, end) for start, end in lines)
            ):
                continue
            if not any(box.overlaps(dot) for dot in dot_boxes) and not box.meets_ring(
                circle_center, radius.radius, _MARK_GAP
            ):
                first_fitting = (spot, box)
                break
            if first_fitting is None:
                first_fitting = (spot, box)
        if first_fitting is not None:
            spot, box = first_fitting
            labels[area.code] = spot
            taken.append(box)
    return labels


def _box_around(center: MapPoint, half_side: float) -> _Box:
    return _Box(
        left=center.x - half_side,
        top=center.y - half_side,
        right=center.x + half_side,
        bottom=center.y + half_side,
    )


def _label_spots(
    area: _VisibleArea, frame: _Frame, font_size: float
) -> Iterator[MapPoint]:
    """Yield the area's own label point, then the label points of codes centered on
    spots inside the area, nearest to its own first.
    """
    yield area.label
    half_height = _CAPITAL_HEIGHT_FRACTION * font_size / 2
    step = _LABEL_SEARCH_STEP / frame.scale
    prepared = area.outline.prepared
    minimum_x, minimum_y, maximum_x, maximum_y = area.outline.extent
    centers = []
    for column in range(int((maximum_x - minimum_x) / step)):
        x = minimum_x + (column + 0.5) * step
        for row in range(int((maximum_y - minimum_y) / step)):
            y = minimum_y + (row + 0.5) * step
            if prepared.contains(Point(x, y, srid=MAP_SRID)):
                centers.append(frame.map_point(x, y))
    centers.sort(key=lambda center: _distance(center, area.label))
    for center in centers:
        yield MapPoint(x=center.x, y=round(center.y + half_height, 1))


def _visible_areas(frame: _Frame) -> list[_VisibleArea]:
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
        areas.append(
            _VisibleArea(
                path=path,
                code=area.code,
                outline=outline,
                label=frame.map_point(label_point.x, label_point.y),
            )
        )
    return areas


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
