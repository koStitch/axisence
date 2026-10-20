"""Tests for geocoding providers through the US Census Geocoder batch endpoint."""

from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from axisence.ingestion import http_session
from axisence.ingestion.models import IngestionSnapshot, SnapshotKind
from axisence.providers import geocoding
from axisence.providers.models import (
    Borough,
    GeocodingOutcome,
    LocationSource,
    Provider,
    ProviderSource,
)
from axisence.providers.parsing import Coordinates
from tests.fixture_files import fixture_bytes
from tests.providers.factories import make_competitor, make_provider, make_run
from tests.providers.geocoder_fakes import (
    recorded_result_for,
    uploaded_address_rows,
    uploaded_form_parts,
)

PUBLISHED_POINT = Point(-73.9754, 40.6826, srid=4326)


def _candidate_identifiers():
    return [
        provider.source_identifier for provider in geocoding.find_geocoding_candidates()
    ]


@pytest.mark.django_db
def test_candidates_need_a_published_address_and_no_publisher_coordinates():
    make_provider(source_identifier="DC9100001")
    make_provider(source_identifier="DC9100002", location=PUBLISHED_POINT)
    make_provider(source_identifier="DC9100003", street_address="EXAMPLE STREET")
    make_provider(source=ProviderSource.NY_STATE, source_identifier="9100004")
    make_provider(
        source=ProviderSource.NY_STATE, source_identifier="9100005", street_address=""
    )
    make_provider(
        source=ProviderSource.NY_STATE,
        source_identifier="9100006",
        ny_state_street_number="",
    )

    assert _candidate_identifiers() == ["DC9100001", "9100004"]


@pytest.mark.django_db
def test_a_matched_provider_is_a_candidate_again_only_after_its_address_changes():
    matched = make_provider(source_identifier="DC9100001")
    failed = make_provider(source_identifier="DC9100002")
    Provider.objects.filter(pk=matched.pk).update(
        location=PUBLISHED_POINT,
        location_source=LocationSource.GEOCODER,
        geocoding_request_address=geocoding.geocoding_request_address(matched),
        geocoding_outcome=GeocodingOutcome.MATCHED,
    )
    Provider.objects.filter(pk=failed.pk).update(
        geocoding_request_address=geocoding.geocoding_request_address(failed),
        geocoding_outcome=GeocodingOutcome.NO_MATCH,
    )

    before = _candidate_identifiers()
    Provider.objects.filter(pk=matched.pk).update(street_address="200 Example Street")

    assert before == ["DC9100002"]
    assert _candidate_identifiers() == ["DC9100001", "DC9100002"]


def test_batch_file_sends_street_without_unit_text_city_state_and_zip_key():
    ny_state = Provider(
        pk=1,
        source=ProviderSource.NY_STATE,
        street_address="401 West 49th Street, Suite 3",
        ny_state_city=" New York ",
        zip_key="10019",
    )
    nyc_health = Provider(
        pk=2,
        source=ProviderSource.NYC_HEALTH,
        street_address="50 WEST 97 STREET, SUITE 1D",
        borough=Borough.MANHATTAN,
        zip_key="10025",
    )

    batch_file = geocoding.build_batch_file([ny_state, nyc_health])

    assert batch_file == (
        b"1,401 West 49th Street,New York,NY,10019\n"
        b"2,50 WEST 97 STREET,Manhattan,NY,10025\n"
    )


def test_batch_result_parsing_of_the_recorded_response():
    results = geocoding.parse_batch_result(
        fixture_bytes("geocoder/addressbatch-result.csv").decode("utf-8")
    )

    assert set(results) == {"1", "2", "3", "10"}
    assert results["1"] == geocoding.GeocodingResult(
        GeocodingOutcome.MATCHED,
        matched_address="209 JORALEMON ST, BROOKLYN, NY, 11201",
        coordinates=Coordinates(
            longitude=Decimal("-73.990738135081"),
            latitude=Decimal("40.692568162618"),
        ),
    )
    assert results["2"].outcome == GeocodingOutcome.MATCHED
    assert results["3"].outcome == GeocodingOutcome.NO_MATCH
    assert results["10"].outcome == GeocodingOutcome.NO_MATCH


def test_tie_and_out_of_state_matches_are_failures():
    results = geocoding.parse_batch_result(
        '"5","1 Example St, Brooklyn, NY, 11217","Tie"\n'
        '"6","1 Example St, Brooklyn, NY, 11217","Match","Exact",'
        '"1 EXAMPLE ST, LOS ANGELES, CA, 90012","-118.243683,34.052235","1","L"\n'
        '"7","1 Example St, Brooklyn, NY, 11217","Match","Exact",'
        '"1 EXAMPLE ST, BROOKLYN, NY, 11217","not a point","1","L"\n'
        '"8","1 Example St, Brooklyn, NY, 11217","Unexpected"\n'
    )

    assert {identifier: result.outcome for identifier, result in results.items()} == {
        "5": GeocodingOutcome.TIE,
        "6": GeocodingOutcome.OUTSIDE_NEW_YORK,
        "7": GeocodingOutcome.REQUEST_FAILED,
        "8": GeocodingOutcome.REQUEST_FAILED,
    }


@pytest.mark.django_db
def test_geocoding_stores_matches_and_failures_and_records_the_snapshot(
    fake_transport, raw_data_directory
):
    competitor = make_competitor()
    providers = [
        make_provider(source_identifier=f"DC910000{index}", competitor=competitor)
        for index in range(1, 4)
    ]
    fake_transport.add("POST", geocoding.GEOCODER_BATCH_URL, recorded_result_for)
    run = make_run()

    counts = geocoding.geocode_candidates(http_session.build_session(), run)

    (request,) = fake_transport.requests
    first, second, third = (
        Provider.objects.get(pk=provider.pk) for provider in providers
    )
    snapshot = IngestionSnapshot.objects.get()
    competitor.refresh_from_db()
    assert uploaded_form_parts(request)["benchmark"] == b"Public_AR_Current"
    assert [row[0] for row in uploaded_address_rows(request)] == [
        str(provider.pk) for provider in providers
    ]
    assert (counts.geocoded, counts.failures) == (2, 1)
    assert first.location_source == LocationSource.GEOCODER
    assert first.location == Point(-73.990738135081, 40.692568162618, srid=4326)
    assert first.geocoding_matched_address == "209 JORALEMON ST, BROOKLYN, NY, 11201"
    assert first.geocoding_request_address == "100 Example Street, Brooklyn, NY, 11217"
    assert second.geocoding_outcome == GeocodingOutcome.MATCHED
    assert third.geocoding_outcome == GeocodingOutcome.NO_MATCH
    assert third.location is None
    assert third.geocoding_attempted_at is not None
    assert snapshot.kind == SnapshotKind.GEOCODER_RESULT
    assert snapshot.run == run
    assert (raw_data_directory / snapshot.path).is_file()
    assert competitor.location is not None


@pytest.mark.django_db
def test_a_failed_geocoding_request_marks_every_candidate_failed_without_failing(
    fake_transport,
):
    started = timezone.now()
    providers = [
        make_provider(
            source_identifier=f"DC910000{index}", competitor=make_competitor()
        )
        for index in range(1, 3)
    ]

    counts = geocoding.geocode_candidates(http_session.build_session(), make_run())

    assert len(fake_transport.requests) == 1
    assert (counts.geocoded, counts.failures) == (0, 2)
    assert not IngestionSnapshot.objects.exists()
    for provider in providers:
        provider.refresh_from_db()
        assert provider.geocoding_outcome == GeocodingOutcome.REQUEST_FAILED
        assert provider.location_source == LocationSource.NONE
        assert provider.geocoding_attempted_at >= started
