"""Run one provider ingestion: fetch or take a snapshot, parse, store, group, geocode.

Storing, missing marks, changed-keys flags, grouping, and the summary refresh share
one transaction. Geocoding follows in transactions of its own, so a geocoding failure
never undoes stored providers.
"""

import dataclasses
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import requests
from django.db import transaction
from django.utils import timezone

from axisence.ingestion import http_session, runs, snapshots, socrata
from axisence.ingestion.models import IngestionRun, IngestionSource, SnapshotKind
from axisence.ingestion.socrata import SocrataDataset
from axisence.providers import geocoding, grouping, parsing, storing
from axisence.providers.models import ProviderSource
from axisence.providers.ny_state import (
    NY_STATE_DATASET,
    NY_STATE_REQUIRED_HEADERS,
    parse_ny_state_rows,
)
from axisence.providers.nyc_health import (
    NYC_HEALTH_DATASET,
    NYC_HEALTH_REQUIRED_HEADERS,
    parse_nyc_health_rows,
)
from axisence.providers.parsing import ParseResult

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderSourceDefinition:
    source: ProviderSource
    ingestion_source: IngestionSource
    dataset: SocrataDataset
    required_headers: tuple[str, ...]
    parse_rows: Callable[[Sequence[Mapping[str, str]]], ParseResult]


NY_STATE = ProviderSourceDefinition(
    source=ProviderSource.NY_STATE,
    ingestion_source=IngestionSource.NY_STATE_PROVIDERS,
    dataset=NY_STATE_DATASET,
    required_headers=NY_STATE_REQUIRED_HEADERS,
    parse_rows=parse_ny_state_rows,
)

NYC_HEALTH = ProviderSourceDefinition(
    source=ProviderSource.NYC_HEALTH,
    ingestion_source=IngestionSource.NYC_HEALTH_PROVIDERS,
    dataset=NYC_HEALTH_DATASET,
    required_headers=NYC_HEALTH_REQUIRED_HEADERS,
    parse_rows=parse_nyc_health_rows,
)


def run_provider_ingestion(
    definition: ProviderSourceDefinition,
    *,
    snapshot_argument: str | None,
    allow_mass_disappearance: bool,
) -> IngestionRun:
    """Ingest one source, from a fresh download or from a saved export snapshot."""
    run = runs.start_run(
        definition.ingestion_source, is_reingestion=snapshot_argument is not None
    )
    counts = runs.RunCounts()
    return runs.execute_run(
        run,
        counts,
        lambda: _ingest(
            definition, run, counts, snapshot_argument, allow_mass_disappearance
        ),
    )


def _ingest(
    definition: ProviderSourceDefinition,
    run: IngestionRun,
    counts: runs.RunCounts,
    snapshot_argument: str | None,
    allow_mass_disappearance: bool,
) -> None:
    with http_session.build_session() as session:
        export_path = _take_export(definition, run, session, snapshot_argument)
        rows = parsing.read_export(export_path, definition.required_headers)
        parse_result = definition.parse_rows(rows)
        source_label = definition.source.label
        for warning in parse_result.field_warnings:
            logger.warning(
                "Field warning: %s %s, field %s",
                source_label,
                warning.source_identifier,
                warning.field,
            )
        for skipped_row in parse_result.skipped_rows:
            logger.warning(
                "Skipped row %d of the %s export: %s",
                skipped_row.row_number,
                source_label,
                skipped_row.reason,
            )
        counts.rows_read_count = len(rows)
        counts.skipped_count = len(parse_result.skipped_rows)
        counts.field_warning_count = len(parse_result.field_warnings)

        with transaction.atomic():
            store_counts = storing.store_provider_records(
                run,
                definition.source,
                parse_result,
                allow_mass_disappearance=allow_mass_disappearance,
            )
            grouping_counts = grouping.assign_competitors(run)
            grouping.refresh_competitor_summaries()
        counts.created_count = store_counts.created
        counts.updated_count = store_counts.updated
        counts.unchanged_count = store_counts.unchanged
        counts.marked_missing_count = store_counts.marked_missing
        counts.reactivated_count = store_counts.reactivated
        counts.competitors_created_count = grouping_counts.competitors_created
        counts.review_flags_raised_count = (
            store_counts.review_flags_raised + grouping_counts.review_flags_raised
        )
        run.guard_override_used = store_counts.guard_override_used

        geocoding_counts = geocoding.geocode_candidates(session, run)
        counts.geocoded_count = geocoding_counts.geocoded
        counts.geocoding_failure_count = geocoding_counts.failures


def _take_export(
    definition: ProviderSourceDefinition,
    run: IngestionRun,
    session: requests.Session,
    snapshot_argument: str | None,
) -> Path:
    if snapshot_argument is not None:
        path = snapshots.resolve_reingestion_path(
            snapshot_argument, definition.dataset.raw_directory_name
        )
        # The publisher time of the data loaded is the re-ingested export's own, as
        # its saved metadata records it, never the time of this run.
        metadata = socrata.read_saved_metadata(definition.dataset, path)
        if metadata is None:
            logger.warning(
                "The %s export snapshot has no saved metadata beside it",
                definition.dataset.identifier,
            )
        else:
            runs.record_snapshot(run, SnapshotKind.PROVIDER_METADATA, metadata.snapshot)
            run.publisher_rows_updated_at = metadata.rows_updated_at
        runs.record_snapshot(
            run, SnapshotKind.PROVIDER_EXPORT, snapshots.describe_existing(path)
        )
        return path
    fetched = socrata.fetch_dataset(session, definition.dataset, timezone.now(), run.pk)
    runs.record_snapshot(run, SnapshotKind.PROVIDER_METADATA, fetched.metadata)
    runs.record_snapshot(run, SnapshotKind.PROVIDER_EXPORT, fetched.export)
    run.publisher_rows_updated_at = fetched.rows_updated_at
    return fetched.export.absolute_path
