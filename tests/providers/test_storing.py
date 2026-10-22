"""Tests for upserting provider records and tracking activity."""

from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from axisence.ingestion.coordinates import Coordinates
from axisence.ingestion.errors import IngestionError
from axisence.providers.geocoding import geocoding_request_address
from axisence.providers.models import (
    GeocodingOutcome,
    LocationSource,
    Provider,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
    ReviewFlagState,
)
from axisence.providers.parsing import ParseResult
from axisence.providers.storing import store_provider_records
from tests.providers.factories import make_competitor, make_run, provider_record

BARCLAYS_CENTER = Coordinates(
    longitude=Decimal("-73.9754"), latitude=Decimal("40.6826")
)
BARCLAYS_CENTER_POINT = Point(-73.9754, 40.6826, srid=4326)
GEOCODED_POINT = Point(-73.9903, 40.6929, srid=4326)


def _store(records, *, source=ProviderSource.NYC_HEALTH, repeated=(), allow=False):
    run = make_run()
    counts = store_provider_records(
        run,
        source,
        ParseResult(
            records=tuple(records),
            skipped_rows=(),
            field_warnings=(),
            repeated_identifiers=frozenset(repeated),
        ),
        allow_mass_disappearance=allow,
    )
    return run, counts


def _records(count):
    return [
        provider_record(source_identifier=f"DC90000{index:02d}")
        for index in range(count)
    ]


def _geocode(provider):
    # Stands in for a successful geocoding stage.
    Provider.objects.filter(pk=provider.pk).update(
        location=GEOCODED_POINT,
        location_source=LocationSource.GEOCODER,
        geocoding_request_address=geocoding_request_address(provider),
        geocoding_matched_address="100 EXAMPLE ST, BROOKLYN, NY, 11217",
        geocoding_attempted_at=timezone.now(),
        geocoding_outcome=GeocodingOutcome.MATCHED,
    )


@pytest.mark.django_db
def test_first_store_creates_every_provider():
    records = [*_records(2), provider_record(coordinates=BARCLAYS_CENTER)]

    run, counts = _store(records)

    assert (counts.created, counts.updated, counts.unchanged) == (3, 0, 0)
    assert (counts.marked_missing, counts.reactivated) == (0, 0)
    assert Provider.objects.count() == 3
    for provider in Provider.objects.all():
        assert provider.first_seen_at == run.started_at
        assert provider.last_seen_at == run.started_at
        assert provider.missing_since is None
        assert provider.competitor is None
    located = Provider.objects.get(location__isnull=False)
    assert located.location == BARCLAYS_CENTER_POINT
    assert located.location_source == LocationSource.PUBLISHER


@pytest.mark.django_db
def test_storing_the_same_records_again_counts_everything_unchanged():
    records = [*_records(2), provider_record(coordinates=BARCLAYS_CENTER)]
    first_run, _ = _store(records)

    second_run, counts = _store(records)

    assert (counts.created, counts.updated, counts.unchanged) == (0, 0, 3)
    assert (counts.marked_missing, counts.review_flags_raised) == (0, 0)
    for provider in Provider.objects.all():
        assert provider.first_seen_at == first_run.started_at
        assert provider.last_seen_at == second_run.started_at
    assert second_run.started_at > first_run.started_at


@pytest.mark.django_db
def test_a_changed_field_counts_as_updated():
    records = _records(3)
    _store(records)
    records[0] = provider_record(
        source_identifier=records[0].source_identifier, total_capacity=42
    )

    _, counts = _store(records)

    assert (counts.created, counts.updated, counts.unchanged) == (0, 1, 2)
    stored = Provider.objects.get(source_identifier=records[0].source_identifier)
    assert stored.total_capacity == 42


@pytest.mark.django_db
def test_absent_active_provider_is_marked_missing_and_never_deleted():
    records = _records(10)
    _store(records)

    run, counts = _store(records[1:])

    assert counts.marked_missing == 1
    assert Provider.objects.count() == 10
    absent = Provider.objects.get(source_identifier=records[0].source_identifier)
    assert absent.missing_since == run.started_at


@pytest.mark.django_db
def test_reappearing_provider_is_reactivated():
    records = _records(10)
    _store(records)
    _store(records[1:])

    _, counts = _store(records)

    assert (counts.reactivated, counts.updated, counts.unchanged) == (1, 0, 10)
    assert not Provider.objects.filter(missing_since__isnull=False).exists()


@pytest.mark.django_db
def test_a_run_of_one_source_never_changes_the_other_sources_activity():
    ny_state_run, _ = _store(
        [provider_record(source=ProviderSource.NY_STATE)],
        source=ProviderSource.NY_STATE,
    )
    nyc_health_records = _records(2)
    _store(nyc_health_records)

    _, counts = _store(nyc_health_records[1:], allow=True)

    ny_state_provider = Provider.objects.get(source=ProviderSource.NY_STATE)
    assert counts.marked_missing == 1
    assert ny_state_provider.missing_since is None
    assert ny_state_provider.last_seen_at == ny_state_run.started_at


@pytest.mark.django_db
def test_guard_refuses_to_mark_more_than_ten_percent_missing_before_any_write():
    records = _records(10)
    _store(records)
    changed = provider_record(
        source_identifier=records[0].source_identifier, total_capacity=42
    )

    with pytest.raises(IngestionError, match="--allow-mass-disappearance"):
        _store([changed, *records[1:8], provider_record()])

    assert Provider.objects.count() == 10
    assert not Provider.objects.filter(missing_since__isnull=False).exists()
    assert not Provider.objects.filter(total_capacity=42).exists()


@pytest.mark.django_db
def test_guard_override_lets_the_run_proceed_and_is_recorded():
    records = _records(10)
    _store(records)

    _, counts = _store(records[:8], allow=True)

    assert counts.guard_override_used
    assert counts.marked_missing == 2


@pytest.mark.django_db
def test_guard_does_not_apply_without_active_providers():
    records = _records(2)
    _, first_counts = _store(records)
    _store([], allow=True)

    _, counts = _store([])

    assert first_counts.created == 2
    assert not counts.guard_override_used
    assert counts.marked_missing == 0


@pytest.mark.django_db
def test_repeated_identifier_is_neither_updated_nor_marked_missing():
    records = _records(2)
    first_run, _ = _store(records)
    repeated_identifier = records[0].source_identifier

    _, counts = _store(records[1:], repeated=(repeated_identifier,))

    repeated = Provider.objects.get(source_identifier=repeated_identifier)
    assert counts.marked_missing == 0
    assert repeated.missing_since is None
    assert repeated.last_seen_at == first_run.started_at


@pytest.mark.django_db
def test_publisher_coordinates_replace_a_geocoded_location():
    record = provider_record()
    _store([record])
    _geocode(Provider.objects.get())
    published = provider_record(
        source_identifier=record.source_identifier, coordinates=BARCLAYS_CENTER
    )

    _, counts = _store([published])

    provider = Provider.objects.get()
    assert counts.updated == 1
    assert provider.location_source == LocationSource.PUBLISHER
    assert provider.location == BARCLAYS_CENTER_POINT
    assert provider.geocoding_outcome == ""
    assert provider.geocoding_request_address == ""


@pytest.mark.django_db
def test_geocoded_location_survives_while_the_address_is_unchanged():
    record = provider_record()
    _store([record])
    _geocode(Provider.objects.get())

    _, counts = _store([record])

    provider = Provider.objects.get()
    assert (counts.updated, counts.unchanged) == (0, 1)
    assert provider.location_source == LocationSource.GEOCODER
    assert provider.location == GEOCODED_POINT


@pytest.mark.django_db
def test_geocoded_location_is_dropped_when_the_address_changes():
    record = provider_record()
    _store([record])
    _geocode(Provider.objects.get())
    moved = provider_record(
        source_identifier=record.source_identifier,
        street_address="200 Example Street",
    )

    _, counts = _store([moved])

    provider = Provider.objects.get()
    assert counts.updated == 1
    assert provider.location is None
    assert provider.location_source == LocationSource.NONE
    # The earlier attempt stays recorded, so geocoding sees the address changed.
    assert provider.geocoding_outcome == GeocodingOutcome.MATCHED
    assert (
        provider.geocoding_request_address == "100 Example Street, Brooklyn, NY, 11217"
    )


@pytest.mark.django_db
def test_changed_published_name_keeps_the_competitor_and_raises_a_changed_keys_flag():
    record = provider_record()
    _store([record])
    competitor = make_competitor(display_name="Synthetic Day Care")
    Provider.objects.update(competitor=competitor)
    renamed = provider_record(
        source_identifier=record.source_identifier, original_name="Other Day Care"
    )

    run, counts = _store([renamed])
    _, renamed_again_counts = _store(
        [
            provider_record(
                source_identifier=record.source_identifier,
                original_name="Third Day Care",
            )
        ]
    )

    provider = Provider.objects.get()
    flag = ReviewFlag.objects.get()
    assert provider.competitor == competitor
    assert provider.name_key == "THIRD DAY CARE"
    assert counts.review_flags_raised == 1
    assert renamed_again_counts.review_flags_raised == 0
    assert flag.reason == ReviewFlagReason.CHANGED_KEYS
    assert flag.state == ReviewFlagState.OPEN
    assert flag.provider == provider
    assert flag.flagged_competitor == competitor
    assert flag.proposed_competitor is None
    assert flag.raised_by_run == run


@pytest.mark.django_db
def test_key_change_without_a_published_change_raises_no_flag():
    record = provider_record()
    _store([record])
    Provider.objects.update(
        competitor=make_competitor(), name_key="KEY OF OLDER NORMALIZATION"
    )

    _, counts = _store([record])

    assert counts.updated == 1
    assert counts.review_flags_raised == 0
    assert not ReviewFlag.objects.exists()
    assert Provider.objects.get().name_key == record.name_key
