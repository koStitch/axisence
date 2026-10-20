"""Upsert parsed provider records, track activity, and raise changed-keys flags.

Only the provider's own source changes its activity: a run marks missing the active
providers of its source that its snapshot no longer lists, and never deletes them.
"""

import dataclasses
import itertools

from django.contrib.gis.geos import Point

from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import IngestionRun
from axisence.providers import geocoding
from axisence.providers.models import (
    INGESTED_FIELD_NAMES,
    LOCATION_FIELD_NAMES,
    LocationSource,
    Provider,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
    ReviewFlagState,
)
from axisence.providers.parsing import ParseResult, ProviderRecord

WRITE_BATCH_SIZE = 1000

# A run may mark at most one tenth of its source's active providers missing.
MASS_DISAPPEARANCE_DIVISOR = 10

COMPARED_FIELD_NAMES = INGESTED_FIELD_NAMES + LOCATION_FIELD_NAMES
UPSERTED_FIELD_NAMES = (*COMPARED_FIELD_NAMES, "last_seen_at", "missing_since")

_GEOCODING_ATTEMPT_FIELD_NAMES = (
    "geocoding_request_address",
    "geocoding_matched_address",
    "geocoding_attempted_at",
    "geocoding_outcome",
)


@dataclasses.dataclass(slots=True)
class StoreCounts:
    created: int
    updated: int
    unchanged: int
    marked_missing: int
    reactivated: int
    review_flags_raised: int
    guard_override_used: bool


def provider_from_record(record: ProviderRecord) -> Provider:
    """Return an unsaved provider holding the record's values and publisher location."""
    values = {name: getattr(record, name) for name in INGESTED_FIELD_NAMES}
    provider = Provider(**values)
    if record.coordinates is None:
        provider.location = None
        provider.location_source = LocationSource.NONE
    else:
        provider.location = Point(
            float(record.coordinates.longitude),
            float(record.coordinates.latitude),
            srid=4326,
        )
        provider.location_source = LocationSource.PUBLISHER
    return provider


def merge_location(current: Provider, desired: Provider) -> None:
    """Carry the stored location state over to the desired provider where it applies.

    Publisher coordinates win. Otherwise a geocoded location survives only while the
    geocoding request address is unchanged, and the latest attempt's fields are kept
    so the geocoding stage can decide whether to try again.
    """
    if desired.location_source == LocationSource.PUBLISHER:
        return
    if desired.is_address_omitted:
        return
    if (
        current.location_source == LocationSource.GEOCODER
        and current.geocoding_request_address
        == geocoding.geocoding_request_address(desired)
    ):
        for name in LOCATION_FIELD_NAMES:
            setattr(desired, name, getattr(current, name))
        return
    desired.location = None
    desired.location_source = LocationSource.NONE
    for name in _GEOCODING_ATTEMPT_FIELD_NAMES:
        setattr(desired, name, getattr(current, name))


def store_provider_records(
    run: IngestionRun,
    source: ProviderSource,
    parse_result: ParseResult,
    *,
    allow_mass_disappearance: bool,
) -> StoreCounts:
    """Upsert one source's records and mark its absent providers missing.

    Runs inside the caller's transaction. The mass-disappearance guard raises before
    any write.
    """
    now = run.started_at
    existing = {
        provider.source_identifier: provider
        for provider in Provider.objects.filter(source=source)
    }
    providers_with_open_changed_keys_flag = set(
        ReviewFlag.objects.filter(
            state=ReviewFlagState.OPEN,
            reason=ReviewFlagReason.CHANGED_KEYS,
            provider__source=source,
        ).values_list("provider_id", flat=True)
    )
    # A repeated identifier is still listed by the publisher, so it is never missing.
    present = {
        record.source_identifier for record in parse_result.records
    } | parse_result.repeated_identifiers
    active = [provider for provider in existing.values() if provider.is_active]
    to_mark_missing = [
        provider.pk for provider in active if provider.source_identifier not in present
    ]
    guard_override_used = False
    if active and len(to_mark_missing) * MASS_DISAPPEARANCE_DIVISOR > len(active):
        if not allow_mass_disappearance:
            raise IngestionError(
                f"Refusing to mark {len(to_mark_missing)} of {len(active)} active "
                f"{ProviderSource(source).label} providers missing, which is more "
                "than 10%. Run the command again with --allow-mass-disappearance if "
                "the publisher really removed them."
            )
        guard_override_used = True

    counts = StoreCounts(
        created=0,
        updated=0,
        unchanged=0,
        marked_missing=len(to_mark_missing),
        reactivated=0,
        review_flags_raised=0,
        guard_override_used=guard_override_used,
    )
    upserts: list[Provider] = []
    unchanged_ids: list[int] = []
    queued_flags: list[ReviewFlag] = []
    for record in parse_result.records:
        desired = provider_from_record(record)
        current = existing.get(record.source_identifier)
        if current is None:
            desired.first_seen_at = now
            desired.last_seen_at = now
            desired.missing_since = None
            upserts.append(desired)
            counts.created += 1
            continue
        merge_location(current, desired)
        changed = any(
            getattr(current, name) != getattr(desired, name)
            for name in COMPARED_FIELD_NAMES
        )
        reactivated = current.missing_since is not None
        if (
            _published_keys_changed(current, desired)
            and current.competitor_id is not None
            and current.pk not in providers_with_open_changed_keys_flag
        ):
            queued_flags.append(
                ReviewFlag(
                    reason=ReviewFlagReason.CHANGED_KEYS,
                    provider_id=current.pk,
                    flagged_competitor_id=current.competitor_id,
                    raised_by_run=run,
                )
            )
        if changed or reactivated:
            # The primary key stays unset: the conflict target finds the stored row.
            desired.first_seen_at = current.first_seen_at
            desired.last_seen_at = now
            desired.missing_since = None
            upserts.append(desired)
        else:
            unchanged_ids.append(current.pk)
        if changed:
            counts.updated += 1
        else:
            counts.unchanged += 1
        if reactivated:
            counts.reactivated += 1

    Provider.objects.bulk_create(
        upserts,
        batch_size=WRITE_BATCH_SIZE,
        update_conflicts=True,
        unique_fields=("source", "source_identifier"),
        update_fields=UPSERTED_FIELD_NAMES,
    )
    for batch in itertools.batched(unchanged_ids, WRITE_BATCH_SIZE, strict=False):
        Provider.objects.filter(pk__in=batch).update(last_seen_at=now)
    for batch in itertools.batched(to_mark_missing, WRITE_BATCH_SIZE, strict=False):
        Provider.objects.filter(pk__in=batch).update(missing_since=now)
    ReviewFlag.objects.bulk_create(queued_flags, batch_size=WRITE_BATCH_SIZE)
    counts.review_flags_raised = len(queued_flags)
    return counts


def _published_keys_changed(current: Provider, desired: Provider) -> bool:
    # A key change caused only by new normalization code raises no flag: the
    # published values must have changed too.
    keys_changed = (current.name_key, current.address_key, current.zip_key) != (
        desired.name_key,
        desired.address_key,
        desired.zip_key,
    )
    published_changed = (
        current.original_name,
        current.street_address,
        current.published_zip_code,
    ) != (desired.original_name, desired.street_address, desired.published_zip_code)
    return keys_changed and published_changed
