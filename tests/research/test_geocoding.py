"""Tests for the lookup of research target addresses."""

import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from axisence.ingestion import http_session
from axisence.research.geocoding import (
    GEOCODER_ONE_LINE_ADDRESS_URL,
    look_up_address,
    parse_lookup_response,
)
from axisence.research.models import TargetLookupOutcome
from tests.fixture_files import fixture_bytes
from tests.research.geocoder_fakes import RECORDED_RESPONSE, one_line_address_body

SYNTHETIC_ADDRESS = "100 Example Street, Brooklyn, NY 11217"
IN_STATE_MATCH = ("100 EXAMPLE ST, BROOKLYN, NY, 11217", -73.9754, 40.6826)
OTHER_IN_STATE_MATCH = ("100 EXAMPLE ST, QUEENS, NY, 11375", -73.8448, 40.7210)
LOS_ANGELES_MATCH = ("100 EXAMPLE ST, LOS ANGELES, CA, 90012", -118.2428, 34.0537)


def _look_up():
    with http_session.build_lookup_session() as session:
        return look_up_address(session, SYNTHETIC_ADDRESS)


def _parse(matches):
    return parse_lookup_response(
        json.loads(one_line_address_body(matches), parse_float=Decimal)
    )


def test_the_recorded_borough_hall_response_proposes_its_single_match():
    payload = json.loads(fixture_bytes(RECORDED_RESPONSE), parse_float=Decimal)

    result = parse_lookup_response(payload)

    assert result.outcome == TargetLookupOutcome.MATCHED
    [match] = result.matches
    assert match.matched_address == "209 JORALEMON ST, BROOKLYN, NY, 11201"
    assert match.coordinates.longitude == Decimal("-73.990738135081")
    assert match.coordinates.latitude == Decimal("40.692568162618")


def test_a_lookup_sends_one_get_with_the_address_benchmark_format_user_agent_and_timeout(  # noqa: E501
    fake_transport,
):
    fake_transport.add(
        "GET", GEOCODER_ONE_LINE_ADDRESS_URL, fixture_bytes(RECORDED_RESPONSE)
    )

    result = _look_up()

    [request] = fake_transport.requests
    url = urlsplit(request.url)
    assert result.outcome == TargetLookupOutcome.MATCHED
    assert request.method == "GET"
    assert f"{url.scheme}://{url.netloc}{url.path}" == GEOCODER_ONE_LINE_ADDRESS_URL
    assert parse_qs(url.query) == {
        "address": [SYNTHETIC_ADDRESS],
        "benchmark": ["Public_AR_Current"],
        "format": ["json"],
    }
    assert (
        request.headers["User-Agent"]
        == "Axisence-Research-Tool (target address lookup)"
    )
    assert fake_transport.timeouts == [(5, 10)]


def test_several_matches_list_only_those_inside_new_york_state():
    result = _parse([IN_STATE_MATCH, LOS_ANGELES_MATCH, OTHER_IN_STATE_MATCH])

    assert result.outcome == TargetLookupOutcome.SEVERAL_MATCHES
    assert [match.matched_address for match in result.matches] == [
        IN_STATE_MATCH[0],
        OTHER_IN_STATE_MATCH[0],
    ]


def test_one_match_inside_new_york_state_among_several_is_proposed():
    result = _parse([LOS_ANGELES_MATCH, IN_STATE_MATCH])

    assert result.outcome == TargetLookupOutcome.MATCHED
    assert [match.matched_address for match in result.matches] == [IN_STATE_MATCH[0]]


@pytest.mark.parametrize(
    ("matches", "outcome"),
    [
        ([], TargetLookupOutcome.NO_MATCH),
        ([LOS_ANGELES_MATCH], TargetLookupOutcome.OUTSIDE_NEW_YORK),
    ],
    ids=["no match", "outside the state"],
)
def test_no_match_or_only_matches_outside_new_york_state_propose_nothing(
    matches, outcome
):
    result = _parse(matches)

    assert result.outcome == outcome
    assert result.matches == ()


def _raise_read_timeout(request):
    raise requests.ReadTimeout("Synthetic timeout.")


@pytest.mark.parametrize(
    "route",
    [
        {"body": b"Server error", "status": 500},
        {"body": _raise_read_timeout},
        None,
        {"body": b"<html>Not JSON</html>"},
        {"body": b'{"errors": ["Synthetic error"]}'},
    ],
    ids=["HTTP 500", "timeout", "no route", "not JSON", "no result"],
)
def test_failed_requests_propose_nothing_after_a_single_attempt(fake_transport, route):
    if route is not None:
        fake_transport.add("GET", GEOCODER_ONE_LINE_ADDRESS_URL, **route)

    result = _look_up()

    assert result.outcome == TargetLookupOutcome.REQUEST_FAILED
    assert result.matches == ()
    assert len(fake_transport.requests) == 1
