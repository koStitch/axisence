"""Run one transit ingestion: take every feed, parse each with the run's local date,
combine them, and store the stops in one transaction.
"""

import logging

from django.core.management.base import OutputWrapper
from django.db import transaction
from django.utils import timezone

from axisence.ingestion import http_session, runs
from axisence.ingestion.models import IngestionRun, IngestionSource, SnapshotKind
from axisence.transit import combining, feeds, parsing, storing

logger = logging.getLogger(__name__)


def run_transit_ingestion(
    *, from_latest_snapshots: bool, allow_mass_disappearance: bool
) -> IngestionRun:
    """Ingest the MTA feeds, downloaded when republished or from the latest saved
    snapshots.
    """
    run = runs.start_run(
        IngestionSource.MTA_TRANSIT, is_reingestion=from_latest_snapshots
    )
    counts = runs.RunCounts()
    return runs.execute_run(
        run,
        counts,
        lambda: _ingest(
            run,
            counts,
            from_latest_snapshots=from_latest_snapshots,
            allow_mass_disappearance=allow_mass_disappearance,
        ),
    )


def write_feed_outcomes(stdout: OutputWrapper, run: IngestionRun) -> None:
    """Write how the run took each feed, from its snapshot records."""
    snapshots = list(run.snapshots.filter(kind=SnapshotKind.GTFS_FEED))
    for feed in feeds.FEEDS:
        snapshot = next(
            snapshot
            for snapshot in snapshots
            if snapshot.path.endswith(f"-{feed.file_name}")
        )
        if snapshot.fetched_at is not None:
            outcome = "downloaded"
        elif run.is_reingestion:
            outcome = "read from the latest snapshot"
        else:
            outcome = "unchanged"
        stdout.write(f"Feed {feed.file_name}: {outcome}")


def _ingest(
    run: IngestionRun,
    counts: runs.RunCounts,
    *,
    from_latest_snapshots: bool,
    allow_mass_disappearance: bool,
) -> None:
    with http_session.build_session() as session:
        taken_feeds = feeds.take_feeds(
            session, run, from_latest_snapshots=from_latest_snapshots
        )
    # A service counts while it runs on or after the day the run started, in New York.
    service_date = timezone.localdate(run.started_at)
    parsed_feeds = [
        parsing.read_feed(taken.path, taken.feed, service_date) for taken in taken_feeds
    ]
    warnings = [warning for parsed in parsed_feeds for warning in parsed.field_warnings]
    for warning in warnings:
        logger.warning(
            "Transit field warning: %s stop %s, stop_lat and stop_lon",
            warning.feed_file_name,
            warning.published_stop_id,
        )
    counts.field_warning_count = len(warnings)
    combined = combining.combine_feeds(parsed_feeds)
    with transaction.atomic():
        store_counts = storing.store_transit_stops(
            run, combined, allow_mass_disappearance=allow_mass_disappearance
        )
    counts.created_count = store_counts.created
    counts.updated_count = store_counts.updated
    counts.unchanged_count = store_counts.unchanged
    # The shared count and its "Marked missing" label count stops marked inactive.
    counts.marked_missing_count = store_counts.marked_inactive
    counts.reactivated_count = store_counts.reactivated
    run.guard_override_used = store_counts.guard_override_used
