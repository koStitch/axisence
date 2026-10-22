"""Tests for combining parsed feeds into stops, station groups, and lines."""

from decimal import Decimal

from axisence.ingestion.coordinates import Coordinates
from axisence.transit.combining import combine_feeds, natural_order_key
from axisence.transit.feeds import BUS_FEEDS, FEEDS, SUBWAY_FEED
from axisence.transit.models import TransitMode
from axisence.transit.parsing import ParsedFeed, ParsedStop

BUS_FEED_B = BUS_FEEDS[0]
BUS_FEED_BUSCO = BUS_FEEDS[-1]


def _stop(stop_id, name, longitude="-73.9907", latitude="40.6926"):
    return ParsedStop(
        stop_id,
        name,
        Coordinates(longitude=Decimal(longitude), latitude=Decimal(latitude)),
    )


def _parsed(feed, *, stops=(), skipped=(), lines=None, sort_orders=None, transfers=()):
    return ParsedFeed(
        feed=feed,
        stops=tuple(stops),
        skipped_stop_ids=frozenset(skipped),
        line_names_by_stop_id={
            stop_id: frozenset(names) for stop_id, names in (lines or {}).items()
        },
        subway_line_sort_orders=sort_orders or {},
        station_transfers=frozenset(frozenset(pair) for pair in transfers),
        field_warnings=(),
    )


def _feeds(*parsed_feeds):
    """Return every feed in FEEDS order, empty where none is given."""
    given = {parsed.feed: parsed for parsed in parsed_feeds}
    return [given.get(feed) or _parsed(feed) for feed in FEEDS]


def test_same_name_stations_joined_by_transfers_form_one_group():
    subway = _parsed(
        SUBWAY_FEED,
        stops=[
            _stop("232", "Borough Hall"),
            _stop("423", "Borough Hall"),
            _stop("R28", "Court St"),
            _stop("A41", "Jay St-MetroTech"),
            _stop("R29", "Jay St-MetroTech"),
            _stop("X3", "Synthetic Hub"),
            _stop("X1", "Synthetic Hub"),
            _stop("X2", "Synthetic Hub"),
        ],
        lines={"232": {"2"}, "R28": {"R"}},
        sort_orders={"2": 21, "R": 15, "Z": 11},
        transfers=[
            ("232", "423"),
            ("232", "R28"),
            ("423", "R28"),
            ("A41", "R29"),
            ("232", "232"),
            ("X1", "X2"),
            ("X2", "X3"),
        ],
    )

    combined = combine_feeds(_feeds(subway))

    assert {
        stop.published_stop_id: stop.station_group_key for stop in combined.stops
    } == {
        "232": "232+423",
        "423": "232+423",
        "R28": "R28",
        "A41": "A41+R29",
        "R29": "A41+R29",
        "X1": "X1+X2+X3",
        "X2": "X1+X2+X3",
        "X3": "X1+X2+X3",
    }
    # Only the lines that serve a stored station are ranked.
    assert combined.subway_line_sort_positions == {"2": 21, "R": 15}


def test_a_shared_bus_stop_takes_the_first_feed_and_every_feed_s_lines():
    first = _parsed(
        BUS_FEED_B,
        stops=[_stop("306533", "SYNTHETIC FIRST NAME", "-73.990408", "40.693578")],
        skipped=["307408", "308000"],
        lines={"306533": {"B52"}, "307408": {"B61"}},
    )
    last = _parsed(
        BUS_FEED_BUSCO,
        stops=[
            _stop("306533", "SYNTHETIC LAST NAME", "-73.990373", "40.693473"),
            _stop("307408", "SYNTHETIC USABLE IN THE LAST FEED"),
        ],
        skipped=["308000"],
        lines={"306533": {"B103"}, "307408": {"B103"}},
    )

    combined = combine_feeds(_feeds(first, last))

    stops = {stop.published_stop_id: stop for stop in combined.stops}
    assert set(stops) == {"306533", "307408"}
    assert stops["306533"].mode == TransitMode.BUS
    assert stops["306533"].name == "SYNTHETIC FIRST NAME"
    assert stops["306533"].coordinates == Coordinates(
        longitude=Decimal("-73.990408"), latitude=Decimal("40.693578")
    )
    assert stops["306533"].line_names == frozenset({"B52", "B103"})
    assert stops["306533"].station_group_key == ""
    assert stops["307408"].name == "SYNTHETIC USABLE IN THE LAST FEED"
    assert stops["307408"].line_names == frozenset({"B61", "B103"})
    assert combined.untouched_stop_keys == frozenset({("bus", "308000")})
    assert combined.bus_line_names == frozenset({"B52", "B61", "B103"})


def test_natural_order_compares_numbers_by_value_and_text_ignoring_case():
    names = ["Bx1", "B44-SBS", "B103", "B11", "BM1", "B44", "B2"]

    assert sorted(names, key=natural_order_key) == [
        "B2",
        "B11",
        "B44",
        "B44-SBS",
        "B103",
        "BM1",
        "Bx1",
    ]
