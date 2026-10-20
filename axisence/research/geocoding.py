"""Look up a research target address with the Census Geocoder single-address endpoint.

One request per lookup, with a short timeout and no retry, because a researcher waits
on the page. No raw response is stored. The address is never logged, and neither is a
request error's message, because it holds the request URL and with it the address.
"""

import dataclasses
import logging
from decimal import Decimal

import requests

from axisence.ingestion import http_session
from axisence.providers.geocoding import GEOCODER_BENCHMARK
from axisence.providers.parsing import Coordinates, is_inside_new_york_state
from axisence.research.models import ADDRESS_MAXIMUM_LENGTH, TargetLookupOutcome

logger = logging.getLogger(__name__)

GEOCODER_ONE_LINE_ADDRESS_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
)


@dataclasses.dataclass(frozen=True, slots=True)
class AddressMatch:
    matched_address: str
    coordinates: Coordinates


@dataclasses.dataclass(frozen=True, slots=True)
class TargetLookupResult:
    outcome: TargetLookupOutcome
    # In-state matches only, in response order.
    matches: tuple[AddressMatch, ...] = ()


def look_up_address(session: requests.Session, address: str) -> TargetLookupResult:
    """Send one lookup and return what it proposes; a failure proposes nothing."""
    try:
        response = session.get(
            GEOCODER_ONE_LINE_ADDRESS_URL,
            params={
                "address": address,
                "benchmark": GEOCODER_BENCHMARK,
                "format": "json",
            },
            timeout=http_session.LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json(parse_float=Decimal)
    except requests.RequestException as error:
        logger.warning("Target address lookup failed: %s", type(error).__name__)
        return TargetLookupResult(TargetLookupOutcome.REQUEST_FAILED)
    return parse_lookup_response(payload)


def parse_lookup_response(payload: object) -> TargetLookupResult:
    """Return the outcome and the in-state matches of a decoded response.

    A response of an unexpected shape counts as a failed request as a whole.
    """
    failed = TargetLookupResult(TargetLookupOutcome.REQUEST_FAILED)
    if not isinstance(payload, dict):
        return failed
    result = payload.get("result")
    if not isinstance(result, dict):
        return failed
    raw_matches = result.get("addressMatches")
    if not isinstance(raw_matches, list):
        return failed
    matches = []
    for raw_match in raw_matches:
        match = _parse_match(raw_match)
        if match is None:
            return failed
        matches.append(match)
    if not matches:
        return TargetLookupResult(TargetLookupOutcome.NO_MATCH)
    in_state_matches = tuple(
        match for match in matches if is_inside_new_york_state(match.coordinates)
    )
    if not in_state_matches:
        return TargetLookupResult(TargetLookupOutcome.OUTSIDE_NEW_YORK)
    if len(in_state_matches) == 1:
        return TargetLookupResult(TargetLookupOutcome.MATCHED, in_state_matches)
    return TargetLookupResult(TargetLookupOutcome.SEVERAL_MATCHES, in_state_matches)


def _parse_match(raw_match: object) -> AddressMatch | None:
    if not isinstance(raw_match, dict):
        return None
    matched_address = raw_match.get("matchedAddress")
    if (
        not isinstance(matched_address, str)
        or not matched_address.strip()
        or len(matched_address) > ADDRESS_MAXIMUM_LENGTH
    ):
        return None
    coordinates = raw_match.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    longitude = _coordinate(coordinates.get("x"))
    latitude = _coordinate(coordinates.get("y"))
    if longitude is None or latitude is None:
        return None
    return AddressMatch(
        matched_address=matched_address,
        coordinates=Coordinates(longitude=longitude, latitude=latitude),
    )


def _coordinate(value: object) -> Decimal | None:
    # A JSON true or false decodes to a bool, which is an int too.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    return None
