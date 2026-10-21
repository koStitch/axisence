"""Tests for the report's schematic map."""

import itertools
import math
import random
import re
from decimal import Decimal

import pytest
from django.contrib.gis.geos import MultiPoint, MultiPolygon, Point, Polygon
from django.utils import timezone

from axisence.demographics.models import TabulationArea
from axisence.reports.content import build_report
from axisence.reports.models import ReportKind
from axisence.reports.schematic_map import (
    MAP_HEIGHT,
    MAP_SRID,
    MAP_WIDTH,
    MapPoint,
    MarkSizes,
    build_report_map,
)
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
# Map units per US survey foot, on a map framed around a 1-mile circle alone.
MAP_UNITS_PER_FOOT = MAP_HEIGHT / (2 * METERS_PER_MILE * 3937 / 1200 * (1 + 2 * 0.08))
MARK_SIZES = MarkSizes(
    marker_radius=27.0,
    anchor_radius=7.0,
    dot_radius=9.0,
    target_half_side=20.0,
    text_font_size=33.0,
    scale_label_raise=16.0,
)
TARGET_CORNER = 20.0 * math.sqrt(2)
CODE_HALF_WIDTH = 5 * 0.65 * 33.0 / 2
CODE_HEIGHT = 0.73 * 33.0
# A cluster in which plain clearances let a number's line run under another number.
CROWDED = (
    (473.4, 436.5),
    (511.1, 373.2),
    (497.4, 498.7),
    (562.6, 483.1),
    (579.1, 470.5),
    (562.2, 503.2),
    (602.9, 454.4),
    (571.3, 511.7),
)


def _at_map(x, y):
    """Return the point drawn at (x, y) on a 1-mile map around Borough Hall whose
    competitors all lie inside its circle.
    """
    center = Point(BOROUGH_HALL.x, BOROUGH_HALL.y, srid=4326)
    center.transform(MAP_SRID)
    return Point(
        center.x + (x - MAP_WIDTH / 2) / MAP_UNITS_PER_FOOT,
        center.y - (y - MAP_HEIGHT / 2) / MAP_UNITS_PER_FOOT,
        srid=MAP_SRID,
    ).transform(4326, clone=True)


def _all_at_map(map_points):
    """Return the points drawn at the given map coordinates, as _at_map does, in one
    transformation.
    """
    center = Point(BOROUGH_HALL.x, BOROUGH_HALL.y, srid=4326)
    center.transform(MAP_SRID)
    points = MultiPoint(
        [
            Point(
                center.x + (x - MAP_WIDTH / 2) / MAP_UNITS_PER_FOOT,
                center.y - (y - MAP_HEIGHT / 2) / MAP_UNITS_PER_FOOT,
            )
            for x, y in map_points
        ],
        srid=MAP_SRID,
    )
    points.transform(4326)
    return list(points)


def _make_area_on_map(code, left, top, right, bottom):
    corners = [
        _at_map(x, y)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
    ]
    ring = [(corner.x, corner.y) for corner in (*corners, corners[0])]
    TabulationArea.objects.create(
        code=code, boundary=MultiPolygon(Polygon(ring), srid=4326)
    )


def _build(located_on_map, mark_sizes=None):
    return build_report_map(
        BOROUGH_HALL,
        Decimal("1.00"),
        [
            (number, _at_map(x, y))
            for number, (x, y) in enumerate(located_on_map, start=1)
        ],
        mark_sizes=mark_sizes,
    )


def _distance(first, second):
    return math.dist((first.x, first.y), (second.x, second.y))


def _distance_to_line(point, start, end):
    run, rise = end.x - start.x, end.y - start.y
    along = ((point.x - start.x) * run + (point.y - start.y) * rise) / (
        run * run + rise * rise
    )
    along = min(max(along, 0.0), 1.0)
    return math.dist(
        (point.x, point.y), (start.x + along * run, start.y + along * rise)
    )


def _lines_cross(first, second):
    def turn(start, end, point):
        return (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (
            point.x - start.x
        )

    return (
        turn(first.anchor, first.position, second.anchor)
        * turn(first.anchor, first.position, second.position)
        < 0
        and turn(second.anchor, second.position, first.anchor)
        * turn(second.anchor, second.position, first.position)
        < 0
    )


def _code_box(area):
    return (
        area.label_x - CODE_HALF_WIDTH,
        area.label_y - CODE_HEIGHT,
        area.label_x + CODE_HALF_WIDTH,
        area.label_y,
    )


def _distance_to_box(point, box):
    left, top, right, bottom = box
    return math.hypot(
        max(left - point.x, 0.0, point.x - right),
        max(top - point.y, 0.0, point.y - bottom),
    )


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
    # The client report's map keeps no marks apart: every number sits on its
    # position and every code at its area's own label point.
    assert report_map.mark_sizes is None
    assert not any(marker.is_moved for marker in report_map.markers)
    assert all(area.is_labelled for area in report_map.areas)
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


def test_crowded_numbers_move_and_their_lines_stay_clear():
    plain = _build(CROWDED)
    kept_apart = _build(CROWDED, MARK_SIZES)

    assert not any(marker.is_moved for marker in plain.markers)
    assert kept_apart.mark_sizes == MARK_SIZES
    markers = kept_apart.markers
    # Every competitor keeps its true position, whether or not its number moved.
    assert [marker.anchor for marker in markers] == [
        marker.position for marker in plain.markers
    ]
    assert any(marker.is_moved for marker in markers)
    target = kept_apart.target
    for marker in markers:
        assert 27.0 <= marker.x <= MAP_WIDTH - 27.0
        assert 27.0 <= marker.y <= MAP_HEIGHT - 27.0
        assert _distance(marker.position, target) >= 27.0 + TARGET_CORNER
        for other in markers:
            if other.number == marker.number:
                continue
            assert _distance(marker.position, other.position) >= 2 * 27.0
            assert _distance(marker.position, other.anchor) >= 27.0 + 7.0
            if marker.is_moved:
                # A line never runs under another number or through another
                # position, so no position seems to belong to the wrong number.
                assert (
                    _distance_to_line(other.position, marker.anchor, marker.position)
                    >= 27.0
                )
                assert (
                    _distance_to_line(other.anchor, marker.anchor, marker.position)
                    >= 7.0
                )
    for first, second in itertools.combinations(markers, 2):
        if first.is_moved and second.is_moved:
            assert not _lines_cross(first, second)


def test_a_number_moves_to_the_nearest_free_spot_away_from_the_target():
    # Two competitors at one spot, 130 map units above the target.
    kept_apart = _build([(500.0, 300.0), (500.0, 300.0)], MARK_SIZES)

    first, second = kept_apart.markers
    # The nearest ring lies 48 map units out: the number's 27 and the dot's 7, a gap of
    # 4, and 10 of visible line. The first takes the spot on it straight away from the
    # target; the second the nearest spot on the same ring clear of the first and its
    # line.
    assert (first.x, first.y) == pytest.approx((500.0, 252.0), abs=0.2)
    assert _distance(second.position, second.anchor) == pytest.approx(48.0, abs=0.2)
    assert _distance(second.position, first.position) >= 2 * 27.0


def test_a_number_beside_another_position_moves_off_its_own():
    # Two competitors 25 map units apart, far from the target.
    kept_apart = _build([(700.0, 300.0), (725.0, 300.0)], MARK_SIZES)

    first, second = kept_apart.markers
    assert first.is_moved
    assert second.is_moved
    assert _distance(first.position, second.anchor) >= 27.0 + 7.0
    assert _distance(second.position, first.anchor) >= 27.0 + 7.0


def test_numbers_beside_the_map_edge_stay_inside_it():
    # Two competitors at one spot just inside the circle's top, where the spot
    # straight away from the target lies off the map.
    kept_apart = _build([(500.0, 62.0), (500.0, 62.0)], MARK_SIZES)

    for marker in kept_apart.markers:
        assert marker.is_moved
        assert 27.0 <= marker.x <= MAP_WIDTH - 27.0
        assert 27.0 <= marker.y <= MAP_HEIGHT - 27.0


def test_area_codes_show_only_where_they_fit():
    # Around the target, whose square covers the area's own label point.
    _make_area_on_map("11201", 400.0, 330.0, 600.0, 530.0)
    # East of the target, with two competitors at the area's own label point.
    _make_area_on_map("11217", 650.0, 330.0, 820.0, 530.0)
    # A sliver at the map's west edge: its code would be cut off wherever it went.
    _make_area_on_map("10004", -100.0, 380.0, 30.0, 480.0)
    # A small area beside a larger one, too small to hold its code clear of the
    # larger one's: only the larger one is labelled.
    _make_area_on_map("11231", 500.0, 700.0, 530.0, 725.0)
    _make_area_on_map("11232", 535.0, 680.0, 680.0, 760.0)
    # The map's bottom-left corner, where the scale bar covers its own label point.
    _make_area_on_map("11220", -100.0, 776.0, 400.0, 1000.0)
    located = [(735.0, 430.0), (735.0, 430.0)]

    plain = _build(located)
    kept_apart = _build(located, MARK_SIZES)

    assert {area.code: area.is_labelled for area in plain.areas} == {
        "10004": True,
        "11201": True,
        "11217": True,
        "11220": True,
        "11231": True,
        "11232": True,
    }
    assert {area.code: area.is_labelled for area in kept_apart.areas} == {
        "10004": False,
        "11201": True,
        "11217": True,
        "11220": True,
        "11231": False,
        "11232": True,
    }
    plain_areas = {area.code: area for area in plain.areas}
    areas = {area.code: area for area in kept_apart.areas}
    for code in ("11201", "11217", "11220"):
        assert (areas[code].label_x, areas[code].label_y) != (
            plain_areas[code].label_x,
            plain_areas[code].label_y,
        )
    assert (areas["11232"].label_x, areas["11232"].label_y) == (
        plain_areas["11232"].label_x,
        plain_areas["11232"].label_y,
    )
    assert _distance_to_box(kept_apart.target, _code_box(areas["11201"])) > (
        TARGET_CORNER
    )
    # 11217's code keeps clear of both numbers, their position, and their lines.
    box = _code_box(areas["11217"])
    for marker in kept_apart.markers:
        assert _distance_to_box(marker.position, box) >= 27.0
        assert _distance_to_box(marker.anchor, box) >= 7.0
        for step in range(11):
            share = step / 10
            assert (
                _distance_to_box(
                    Point(
                        marker.anchor.x + share * (marker.x - marker.anchor.x),
                        marker.anchor.y + share * (marker.y - marker.anchor.y),
                    ),
                    box,
                )
                > 0
            )
    # 11220's code moved beside the scale bar and its label.
    scale_bar = kept_apart.scale_bar
    scale_bar_right = scale_bar.x + max(scale_bar.length, 6 * 0.65 * 33.0)
    scale_label_top = scale_bar.y - MARK_SIZES.scale_label_raise - CODE_HEIGHT
    code_left, _, _, code_bottom = _code_box(areas["11220"])
    assert code_left > scale_bar_right or code_bottom < scale_label_top


def _boxes_overlap(first, second):
    return (
        first[0] < second[2]
        and second[0] < first[2]
        and first[1] < second[3]
        and second[1] < first[3]
    )


def _assert_marks_kept_apart(report_map):
    """Assert every clearance the map keeps between its numbers, their lines, the
    target, the scale bar, and the area codes.
    """
    target = report_map.target
    markers = report_map.markers
    scale_bar = report_map.scale_bar
    scale_box = (
        0.0,
        scale_bar.y - MARK_SIZES.scale_label_raise - CODE_HEIGHT,
        scale_bar.x + max(scale_bar.length, 6 * 0.65 * 33.0),
        MAP_HEIGHT,
    )
    for marker in markers:
        assert 27.0 <= marker.x <= MAP_WIDTH - 27.0
        assert 27.0 <= marker.y <= MAP_HEIGHT - 27.0
        assert _distance(marker.position, target) >= 27.0 + TARGET_CORNER + 4.0
        assert _distance_to_box(marker.position, scale_box) >= 27.0
        for other in markers:
            if other.number == marker.number:
                continue
            assert _distance(marker.position, other.position) >= 2 * 27.0 + 4.0
            assert _distance(marker.position, other.anchor) >= 27.0 + 7.0 + 4.0
            if other.is_moved:
                assert (
                    _distance_to_line(marker.position, other.anchor, other.position)
                    >= 27.0 + 4.0
                )
            if marker.is_moved:
                if _distance(other.anchor, marker.anchor) >= 7.0 + 4.0:
                    assert (
                        _distance_to_line(other.anchor, marker.anchor, marker.position)
                        >= 7.0 + 4.0
                    )
                if other.is_moved:
                    assert not _lines_cross(marker, other)
        if marker.is_moved:
            assert _distance_to_line(target, marker.anchor, marker.position) >= min(
                TARGET_CORNER + 4.0, _distance(marker.anchor, target)
            )
    code_boxes = [_code_box(area) for area in report_map.areas if area.is_labelled]
    for box in code_boxes:
        left, top, right, bottom = box
        assert left >= 0
        assert top >= 0
        assert right <= MAP_WIDTH
        assert bottom <= MAP_HEIGHT
        assert _distance_to_box(target, box) > TARGET_CORNER
        assert not _boxes_overlap(box, scale_box)
        for marker in markers:
            assert _distance_to_box(marker.position, box) >= 27.0
            if marker.is_moved:
                assert _distance_to_box(marker.anchor, box) >= 7.0
                for step in range(41):
                    share = step / 40
                    assert (
                        _distance_to_box(
                            MapPoint(
                                x=marker.anchor.x
                                + share * (marker.x - marker.anchor.x),
                                y=marker.anchor.y
                                + share * (marker.y - marker.anchor.y),
                            ),
                            box,
                        )
                        > 0
                    )
    for first, second in itertools.combinations(code_boxes, 2):
        assert not _boxes_overlap(first, second)


MARKET_CENTER = (MAP_WIDTH / 2, MAP_HEIGHT / 2)
# Inside the search circle, clear of its line.
MARKET_RADIUS = 0.97 * CIRCLE_RADIUS_IN_MAP_UNITS
# Seeded markets that each make one rule bind, which plain spread markets rarely do:
# a number off earlier lines and lines crossing (spread 97 and 3), codes off moved
# positions (spread 35), a crowded number's fallback (cluster 9), lines off the target
# and codes off lines (cluster 19), and numbers off the scale bar (edge cluster 77).
HARD_MARKETS = (
    ("spread", 3),
    ("spread", 35),
    ("spread", 97),
    ("cluster", 9),
    ("cluster", 19),
    ("edge cluster", 77),
)


def _make_area_tiles():
    """Tile synthetic areas over the whole map, so codes compete for room."""
    for column in range(5):
        for row in range(4):
            _make_area_on_map(
                f"1{column}{row}00",
                -100.0 + column * 240.0,
                -100.0 + row * 265.0,
                140.0 + column * 240.0,
                165.0 + row * 265.0,
            )


def _point_in(seeded_random, center, radius):
    """Return a random point within the radius of the center that also lies inside
    the market's circle.
    """
    while True:
        distance = radius * math.sqrt(seeded_random.random())
        angle = seeded_random.random() * 2 * math.pi
        point = (
            center[0] + distance * math.cos(angle),
            center[1] + distance * math.sin(angle),
        )
        if math.dist(point, MARKET_CENTER) <= MARKET_RADIUS:
            return point


def _market(seeded_random, kind):
    """Return a synthetic market's map points: the first 8 are numbered, the rest drawn
    as dots.
    """
    if kind == "spread":
        return sorted(
            (_point_in(seeded_random, MARKET_CENTER, MARKET_RADIUS) for _ in range(74)),
            key=lambda point: math.dist(point, MARKET_CENTER),
        )
    if kind == "cluster":
        spot = _point_in(seeded_random, MARKET_CENTER, 0.8 * MARKET_RADIUS)
        return [_point_in(seeded_random, spot, 30.0) for _ in range(8)] + [
            _point_in(seeded_random, MARKET_CENTER, MARKET_RADIUS) for _ in range(40)
        ]
    # An edge cluster: 8 within 40 map units of a spot near the circle's lower left.
    angle = seeded_random.uniform(1.9, 2.8)
    spot = (
        MARKET_CENTER[0] + 0.93 * MARKET_RADIUS * math.cos(angle),
        MARKET_CENTER[1] + 0.93 * MARKET_RADIUS * math.sin(angle),
    )
    return [_point_in(seeded_random, spot, 40.0) for _ in range(8)] + [
        _point_in(seeded_random, MARKET_CENTER, MARKET_RADIUS) for _ in range(20)
    ]


def _build_market(points):
    located = _all_at_map(points)
    return build_report_map(
        BOROUGH_HALL,
        Decimal("1.00"),
        list(enumerate(located[:8], start=1)),
        located[8:],
        mark_sizes=MARK_SIZES,
    )


def test_random_markets_keep_every_mark_clear():
    _make_area_tiles()
    # Seeded, so every run checks the same synthetic markets.
    seeded_random = random.Random(20261021)  # noqa: S311
    for _ in range(40):
        # 54 competitors spread over the circle, as around Borough Hall.
        points = sorted(
            (_point_in(seeded_random, MARKET_CENTER, MARKET_RADIUS) for _ in range(54)),
            key=lambda point: math.dist(point, MARKET_CENTER),
        )

        _assert_marks_kept_apart(_build_market(points))


def test_hard_markets_keep_every_mark_clear():
    _make_area_tiles()
    for kind, index in HARD_MARKETS:
        seeded_random = random.Random(7)  # noqa: S311
        for _ in range(index):
            _market(seeded_random, kind)
        points = _market(seeded_random, kind)

        _assert_marks_kept_apart(_build_market(points))


def test_a_packed_cluster_still_keeps_its_numbers_apart():
    # Eight competitors within 25 map units of one spot, too packed for every line to
    # stay clear: the numbers still keep clear of each other, every position, the
    # target, and the map's edge.
    packed = [
        (700.0 + 25.0 * math.cos(index * 2.4), 300.0 + 25.0 * math.sin(index * 2.4))
        for index in range(8)
    ]

    markers = _build(packed, MARK_SIZES).markers

    target = MapPoint(x=MAP_WIDTH / 2, y=MAP_HEIGHT / 2)
    for marker in markers:
        assert 27.0 <= marker.x <= MAP_WIDTH - 27.0
        assert 27.0 <= marker.y <= MAP_HEIGHT - 27.0
        assert _distance(marker.position, target) >= 27.0 + TARGET_CORNER + 4.0
        for other in markers:
            if other.number != marker.number:
                assert _distance(marker.position, other.position) >= 2 * 27.0 + 4.0
                assert _distance(marker.position, other.anchor) >= 27.0 + 7.0 + 4.0
