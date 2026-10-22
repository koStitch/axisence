"""Factories for synthetic transit runs, lines, and stops."""

from datetime import timedelta

from django.utils import timezone

from axisence.ingestion.models import IngestionOutcome, IngestionRun, IngestionSource
from axisence.transit.models import TransitLine, TransitMode, TransitStop


def make_transit_run(
    *, finished_at, started_at=None, outcome=IngestionOutcome.SUCCEEDED
):
    return IngestionRun.objects.create(
        source=IngestionSource.MTA_TRANSIT,
        started_at=started_at or finished_at - timedelta(minutes=5),
        finished_at=finished_at,
        outcome=outcome,
    )


def ensure_transit_run():
    """Return the latest succeeded transit run, or one finished 30 days ago."""
    run = (
        IngestionRun.objects.filter(
            source=IngestionSource.MTA_TRANSIT, outcome=IngestionOutcome.SUCCEEDED
        )
        .order_by("-finished_at", "-pk")
        .first()
    )
    return run or make_transit_run(finished_at=timezone.now() - timedelta(days=30))


def make_transit_line(mode, name, sort_position):
    return TransitLine.objects.create(mode=mode, name=name, sort_position=sort_position)


def make_transit_stop(
    *,
    mode,
    published_stop_id,
    point,
    name="Synthetic Stop",
    lines=(),
    station_group_key=None,
    is_active=True,
):
    """Store a stop; a subway station's group is its own ID unless given."""
    if station_group_key is None:
        station_group_key = published_stop_id if mode == TransitMode.SUBWAY else ""
    run = ensure_transit_run()
    stop = TransitStop.objects.create(
        mode=mode,
        published_stop_id=published_stop_id,
        name=name,
        location=point,
        station_group_key=station_group_key,
        is_active=is_active,
        first_seen_run=run,
        last_seen_run=run,
    )
    stop.lines.set(lines)
    return stop
