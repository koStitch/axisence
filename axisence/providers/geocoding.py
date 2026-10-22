"""Geocode providers that publish an address but no coordinates.

Uses the US Census Geocoder batch endpoint, which needs no key. A provider whose
regulator omits its address is never geocoded, and publisher coordinates always win.
Addresses are never logged.
"""

import csv
import dataclasses
import io
import itertools
import logging
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from axisence.ingestion import http_session, runs, snapshots
from axisence.ingestion.coordinates import Coordinates, is_inside_new_york_state
from axisence.ingestion.models import IngestionRun, SnapshotKind
from axisence.ingestion.redaction import redact_secrets
from axisence.providers import grouping, normalization
from axisence.providers.models import (
    LOCATION_FIELD_NAMES,
    GeocodingOutcome,
    LocationSource,
    Provider,
    ProviderSource,
)

logger = logging.getLogger(__name__)

GEOCODER_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
GEOCODER_BENCHMARK = "Public_AR_Current"
GEOCODER_BATCH_LIMIT = 10_000
GEOCODER_RAW_DIRECTORY_NAME = "census-geocoder"

WRITE_BATCH_SIZE = 1000
STATE = "NY"

# A matched line: identifier, input address, Match, match type, matched address,
# "longitude,latitude", TIGER line identifier, side.
_MATCH_FIELD_COUNT = 6


@dataclasses.dataclass(frozen=True, slots=True)
class GeocodingResult:
    outcome: GeocodingOutcome
    matched_address: str = ""
    coordinates: Coordinates | None = None


@dataclasses.dataclass(slots=True)
class GeocodingCounts:
    geocoded: int
    failures: int


def geocoding_city(provider: Provider) -> str:
    """Return the published city, or the borough name for NYC Health."""
    if provider.source == ProviderSource.NY_STATE:
        return provider.ny_state_city.strip()
    return provider.get_borough_display() if provider.borough else ""


def geocoding_request_address(provider: Provider) -> str:
    """Return the address the geocoder receives, or "" when it cannot be geocoded."""
    if provider.is_address_omitted or not provider.has_house_number_and_street():
        return ""
    parts = (
        normalization.street_for_geocoding(provider.street_address),
        geocoding_city(provider),
        STATE,
        provider.zip_key,
    )
    return ", ".join(part for part in parts if part)


def find_geocoding_candidates() -> list[Provider]:
    """Return the providers to geocode, in primary key order.

    A provider is a candidate when it has a geocodable address and no publisher
    coordinates, unless its current address already matched. Failed attempts are
    retried on every run.
    """
    providers = (
        Provider.objects.exclude(location_source=LocationSource.PUBLISHER)
        .exclude(ny_state_is_address_omitted=True)
        .order_by("pk")
    )
    candidates = []
    for provider in providers:
        request_address = geocoding_request_address(provider)
        already_matched = (
            provider.geocoding_outcome == GeocodingOutcome.MATCHED
            and provider.geocoding_request_address == request_address
        )
        if request_address and not already_matched:
            candidates.append(provider)
    return candidates


def build_batch_file(candidates: Sequence[Provider]) -> bytes:
    """Return the geocoder's input: identifier, street, city, state, zip, no header."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for provider in candidates:
        writer.writerow(
            [
                str(provider.pk),
                normalization.street_for_geocoding(provider.street_address),
                geocoding_city(provider),
                STATE,
                provider.zip_key,
            ]
        )
    return buffer.getvalue().encode("utf-8")


def parse_batch_result(text: str) -> dict[str, GeocodingResult]:
    """Return each result line by its identifier, never by its position."""
    results = {}
    for fields in csv.reader(io.StringIO(text)):
        if fields:
            results[fields[0].strip()] = _parse_result_line(fields)
    return results


def geocode_candidates(session: requests.Session, run: IngestionRun) -> GeocodingCounts:
    """Geocode every candidate in batches, each stored in its own transaction.

    A request that fails after retries gives each candidate of its batch the outcome
    request failed; the run still succeeds.
    """
    candidates = find_geocoding_candidates()
    counts = GeocodingCounts(geocoded=0, failures=0)
    if not candidates:
        return counts
    batches = itertools.batched(candidates, GEOCODER_BATCH_LIMIT, strict=False)
    for batch_number, batch in enumerate(batches, start=1):
        attempted_at = timezone.now()
        results = _request_batch(session, run, batch, batch_number, attempted_at)
        with transaction.atomic():
            for provider in batch:
                result = results.get(
                    str(provider.pk), GeocodingResult(GeocodingOutcome.REQUEST_FAILED)
                )
                _apply_result(provider, result, attempted_at)
                if result.outcome == GeocodingOutcome.MATCHED:
                    counts.geocoded += 1
                else:
                    counts.failures += 1
            Provider.objects.bulk_update(
                batch, LOCATION_FIELD_NAMES, batch_size=WRITE_BATCH_SIZE
            )
            grouping.refresh_competitor_summaries(
                {
                    provider.competitor_id
                    for provider in batch
                    if provider.competitor_id is not None
                }
            )
    logger.info(
        "Geocoding attempted %d providers: %d matched",
        len(candidates),
        counts.geocoded,
    )
    return counts


def _request_batch(
    session: requests.Session,
    run: IngestionRun,
    batch: Sequence[Provider],
    batch_number: int,
    attempted_at: datetime,
) -> dict[str, GeocodingResult]:
    address_file = ("addresses.csv", build_batch_file(batch), "text/csv")
    try:
        with session.post(
            GEOCODER_BATCH_URL,
            data={"benchmark": GEOCODER_BENCHMARK},
            files={"addressFile": address_file},
            stream=True,
            timeout=http_session.TIMEOUT,
        ) as response:
            response.raise_for_status()
            snapshot = snapshots.save_response(
                response,
                GEOCODER_RAW_DIRECTORY_NAME,
                f"addressbatch-result-{batch_number}.csv",
                attempted_at,
                run.pk,
            )
    except requests.RequestException as error:
        logger.warning("Geocoding request failed: %s", redact_secrets(str(error)))
        return {}
    runs.record_snapshot(run, SnapshotKind.GEOCODER_RESULT, snapshot)
    return parse_batch_result(
        snapshot.absolute_path.read_text(encoding="utf-8", errors="replace")
    )


def _parse_result_line(fields: Sequence[str]) -> GeocodingResult:
    status = fields[2].strip() if len(fields) > 2 else ""
    if status == "Match" and len(fields) >= _MATCH_FIELD_COUNT:
        return _parse_match(fields)
    if status == "No_Match":
        return GeocodingResult(GeocodingOutcome.NO_MATCH)
    if status == "Tie":
        return GeocodingResult(GeocodingOutcome.TIE)
    return GeocodingResult(GeocodingOutcome.REQUEST_FAILED)


def _parse_match(fields: Sequence[str]) -> GeocodingResult:
    parts = fields[5].split(",")
    if len(parts) != 2:
        return GeocodingResult(GeocodingOutcome.REQUEST_FAILED)
    try:
        coordinates = Coordinates(
            longitude=Decimal(parts[0].strip()), latitude=Decimal(parts[1].strip())
        )
    except InvalidOperation:
        return GeocodingResult(GeocodingOutcome.REQUEST_FAILED)
    if not (coordinates.longitude.is_finite() and coordinates.latitude.is_finite()):
        return GeocodingResult(GeocodingOutcome.REQUEST_FAILED)
    if not is_inside_new_york_state(coordinates):
        return GeocodingResult(GeocodingOutcome.OUTSIDE_NEW_YORK)
    return GeocodingResult(
        GeocodingOutcome.MATCHED, matched_address=fields[4], coordinates=coordinates
    )


def _apply_result(
    provider: Provider, result: GeocodingResult, attempted_at: datetime
) -> None:
    provider.geocoding_request_address = geocoding_request_address(provider)
    provider.geocoding_attempted_at = attempted_at
    provider.geocoding_outcome = result.outcome
    if result.outcome == GeocodingOutcome.MATCHED and result.coordinates is not None:
        provider.location = Point(
            float(result.coordinates.longitude),
            float(result.coordinates.latitude),
            srid=4326,
        )
        provider.location_source = LocationSource.GEOCODER
        provider.geocoding_matched_address = result.matched_address
    else:
        provider.location = None
        provider.location_source = LocationSource.NONE
        provider.geocoding_matched_address = ""
