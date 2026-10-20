"""Start, complete, and fail ingestion runs, and report them to the user.

Run records are saved outside any transaction, so a run that fails inside one still
leaves its record behind.
"""

import dataclasses
from collections.abc import Callable

from django.core.management.base import CommandError, OutputWrapper
from django.utils import timezone

from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import (
    IngestionOutcome,
    IngestionRun,
    IngestionSnapshot,
    IngestionSource,
    SnapshotKind,
)
from axisence.ingestion.redaction import redact_secrets
from axisence.ingestion.snapshots import SavedSnapshot


@dataclasses.dataclass(slots=True)
class RunCounts:
    """The counts of a run, named exactly as the IngestionRun fields they fill."""

    rows_read_count: int | None = None
    created_count: int | None = None
    updated_count: int | None = None
    unchanged_count: int | None = None
    marked_missing_count: int | None = None
    reactivated_count: int | None = None
    skipped_count: int | None = None
    field_warning_count: int | None = None
    geocoded_count: int | None = None
    geocoding_failure_count: int | None = None
    competitors_created_count: int | None = None
    review_flags_raised_count: int | None = None


_SUMMARY_LABELS = (
    ("rows_read_count", "Rows read"),
    ("created_count", "Created"),
    ("updated_count", "Updated"),
    ("unchanged_count", "Unchanged"),
    ("marked_missing_count", "Marked missing"),
    ("reactivated_count", "Reactivated"),
    ("skipped_count", "Skipped rows"),
    ("field_warning_count", "Field warnings"),
    ("competitors_created_count", "Competitors created"),
    ("review_flags_raised_count", "Review flags raised"),
    ("geocoded_count", "Geocoded"),
    ("geocoding_failure_count", "Geocoding failures"),
)


def start_run(
    source: IngestionSource,
    *,
    is_reingestion: bool = False,
    census_vintage: int | None = None,
) -> IngestionRun:
    """Create the record of a run that starts now."""
    return IngestionRun.objects.create(
        source=source,
        started_at=timezone.now(),
        is_reingestion=is_reingestion,
        census_vintage=census_vintage,
    )


def record_snapshot(
    run: IngestionRun, kind: SnapshotKind, snapshot: SavedSnapshot
) -> IngestionSnapshot:
    """Record a snapshot the run saved or re-ingested."""
    return IngestionSnapshot.objects.create(
        run=run,
        kind=kind,
        path=snapshot.relative_path,
        size_bytes=snapshot.size_bytes,
        sha256=snapshot.sha256,
        fetched_at=snapshot.fetched_at,
    )


def complete_run(run: IngestionRun, counts: RunCounts) -> None:
    """Record the run's counts and its success."""
    _copy_counts(run, counts)
    run.outcome = IngestionOutcome.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()


def fail_run(run: IngestionRun, message: str, counts: RunCounts) -> None:
    """Record the counts measured so far and the failure, without any secret."""
    _copy_counts(run, counts)
    run.outcome = IngestionOutcome.FAILED
    run.failure_message = redact_secrets(message)
    run.finished_at = timezone.now()
    run.save()


def execute_run(
    run: IngestionRun, counts: RunCounts, work: Callable[[], None]
) -> IngestionRun:
    """Do the run's work, then record its success or failure.

    An IngestionError fails the run with its message. Any other exception fails the
    run with only its class name, because its message may hold row values, and is
    raised again.
    """
    try:
        work()
    except IngestionError as error:
        fail_run(run, str(error), counts)
        return run
    except Exception as error:
        fail_run(run, f"Unexpected {type(error).__name__}.", counts)
        raise
    complete_run(run, counts)
    return run


def report_run(stdout: OutputWrapper, run: IngestionRun) -> None:
    """Write a succeeded run's summary, or raise CommandError for a failed run."""
    if run.outcome == IngestionOutcome.FAILED:
        raise CommandError(
            f"{run.get_source_display()} run {run.pk} failed: {run.failure_message}"
        )
    write_run_summary(stdout, run)


def write_run_summary(stdout: OutputWrapper, run: IngestionRun) -> None:
    """Write the fixed-format summary of a succeeded run."""
    stdout.write(f"{run.get_source_display()} run {run.pk} succeeded.")
    for snapshot in run.snapshots.order_by("pk"):
        stdout.write(f"Snapshot: {snapshot.path} ({snapshot.size_bytes} bytes)")
    if run.publisher_rows_updated_at is not None:
        updated_at = timezone.localtime(run.publisher_rows_updated_at)
        stdout.write(f"Publisher last updated: {updated_at:%Y-%m-%d %H:%M:%S %Z}")
    if run.census_vintage is not None:
        stdout.write(f"Vintage: {run.census_vintage}")
    for field_name, label in _SUMMARY_LABELS:
        value = getattr(run, field_name)
        if value is not None:
            stdout.write(f"{label}: {value}")
    if run.is_reingestion:
        stdout.write("Re-ingested an existing snapshot.")
    if run.guard_override_used:
        stdout.write("The mass-disappearance guard was overridden.")


def _copy_counts(run: IngestionRun, counts: RunCounts) -> None:
    for field in dataclasses.fields(counts):
        setattr(run, field.name, getattr(counts, field.name))
