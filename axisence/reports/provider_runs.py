"""The provider runs a generated document reads as of its cut-off, and how documents
word the date of the data each run loaded.
"""

from datetime import date, datetime

from django.utils import timezone

from axisence.ingestion.models import IngestionOutcome, IngestionRun, IngestionSource
from axisence.reports.formatting import format_short_date


def load_latest_provider_runs(cut_off: datetime) -> dict[str, IngestionRun]:
    """Return the latest succeeded run of each provider source finished by the cut-off,
    by source, in one query.
    """
    return {
        run.source: run
        for run in IngestionRun.objects.filter(
            source__in=(
                IngestionSource.NY_STATE_PROVIDERS,
                IngestionSource.NYC_HEALTH_PROVIDERS,
            ),
            outcome=IngestionOutcome.SUCCEEDED,
            finished_at__lte=cut_off,
        )
        .order_by("source", "-finished_at", "-pk")
        .distinct("source")
    }


def as_of_date(run: IngestionRun | None) -> date | None:
    """Return the local date of the data the run loaded: the publisher's last update,
    else the load itself, or None without a run.
    """
    if run is None:
        return None
    if run.publisher_rows_updated_at is not None:
        return timezone.localdate(run.publisher_rows_updated_at)
    if run.finished_at is not None:
        return timezone.localdate(run.finished_at)
    return None


def as_of_text(run: IngestionRun | None) -> str:
    """Return a source's as-of wording: "published data as of Jan 12, 2026", "loaded on
    Jan 14, 2026", or "not loaded".
    """
    if run is not None and run.publisher_rows_updated_at is not None:
        published_on = timezone.localdate(run.publisher_rows_updated_at)
        return f"published data as of {format_short_date(published_on)}"
    if run is not None and run.finished_at is not None:
        return f"loaded on {format_short_date(timezone.localdate(run.finished_at))}"
    return "not loaded"
