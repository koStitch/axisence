"""The seven MTA static GTFS feeds, and how a run takes each one: downloaded when the
MTA has republished it, else its latest saved snapshot.
"""

import dataclasses
import logging
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

from axisence.ingestion import http_session, runs, snapshots
from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import (
    RESPONSE_HEADER_MAXIMUM_LENGTH,
    IngestionRun,
    IngestionSnapshot,
    SnapshotKind,
)
from axisence.ingestion.redaction import redact_secrets
from axisence.transit.models import TransitMode

logger = logging.getLogger(__name__)

FEED_BASE_URL = "https://rrgtfsfeeds.s3.amazonaws.com/"
RAW_DIRECTORY_NAME = "mta-gtfs"


@dataclasses.dataclass(frozen=True, slots=True)
class TransitFeed:
    file_name: str
    mode: TransitMode

    @property
    def url(self) -> str:
        return FEED_BASE_URL + self.file_name


SUBWAY_FEED = TransitFeed("gtfs_subway.zip", TransitMode.SUBWAY)
# In the order of precedence for a bus stop that several feeds list: the first feed
# holding it gives its name and position.
BUS_FEEDS = (
    TransitFeed("gtfs_b.zip", TransitMode.BUS),
    TransitFeed("gtfs_bx.zip", TransitMode.BUS),
    TransitFeed("gtfs_m.zip", TransitMode.BUS),
    TransitFeed("gtfs_q.zip", TransitMode.BUS),
    TransitFeed("gtfs_si.zip", TransitMode.BUS),
    TransitFeed("gtfs_busco.zip", TransitMode.BUS),
)
FEEDS = (SUBWAY_FEED, *BUS_FEEDS)


@dataclasses.dataclass(frozen=True, slots=True)
class TakenFeed:
    feed: TransitFeed
    path: Path


def take_feeds(
    session: requests.Session, run: IngestionRun, *, from_latest_snapshots: bool
) -> tuple[TakenFeed, ...]:
    """Take every feed before any is parsed, recording one snapshot of each on the run.

    A feed is requested with the ETag of its latest saved snapshot, so an unchanged
    feed is answered with HTTP 304 and its latest snapshot is recorded again. With
    from_latest_snapshots, nothing is requested.
    """
    # One time for the whole run, so the files saved together share one prefix.
    fetched_at = timezone.now()
    return tuple(
        _take_feed(
            session,
            run,
            feed,
            fetched_at,
            from_latest_snapshots=from_latest_snapshots,
        )
        for feed in FEEDS
    )


def _take_feed(
    session: requests.Session,
    run: IngestionRun,
    feed: TransitFeed,
    fetched_at: datetime,
    *,
    from_latest_snapshots: bool,
) -> TakenFeed:
    latest = _latest_saved_snapshot(feed)
    latest_path = None if latest is None else _snapshot_path(latest)
    if from_latest_snapshots:
        if latest_path is None:
            raise IngestionError(
                f"No saved snapshot of {feed.file_name} is on disk; run "
                "ingest_mta_transit without --from-latest-snapshots first."
            )
        runs.record_snapshot(
            run, SnapshotKind.GTFS_FEED, snapshots.describe_existing(latest_path)
        )
        logger.info("Transit feed %s read from the latest snapshot", feed.file_name)
        return TakenFeed(feed, latest_path)
    etag = "" if latest is None or latest_path is None else latest.response_etag
    # Only a snapshot on disk with a recorded ETag can answer a 304.
    unchanged_path = latest_path if etag else None
    headers = {"If-None-Match": etag} if etag else {}
    try:
        with session.get(
            feed.url, headers=headers, stream=True, timeout=http_session.TIMEOUT
        ) as response:
            if (
                response.status_code == HTTPStatus.NOT_MODIFIED
                and unchanged_path is not None
            ):
                runs.record_snapshot(
                    run,
                    SnapshotKind.GTFS_FEED,
                    snapshots.describe_existing(unchanged_path),
                )
                logger.info("Transit feed %s unchanged (HTTP 304)", feed.file_name)
                return TakenFeed(feed, unchanged_path)
            if response.status_code != HTTPStatus.OK:
                raise IngestionError(
                    f"Fetching {feed.file_name} failed: HTTP {response.status_code}."
                )
            saved = snapshots.save_response(
                response, RAW_DIRECTORY_NAME, feed.file_name, fetched_at, run.pk
            )
            runs.record_snapshot(
                run,
                SnapshotKind.GTFS_FEED,
                saved,
                response_etag=_storable_header(response, "ETag"),
                response_last_modified=_storable_header(response, "Last-Modified"),
            )
    except requests.RequestException as error:
        raise IngestionError(
            f"Fetching {feed.file_name} failed: {redact_secrets(str(error))}"
        ) from None
    logger.info("Transit feed %s downloaded (HTTP 200)", feed.file_name)
    return TakenFeed(feed, saved.absolute_path)


def _latest_saved_snapshot(feed: TransitFeed) -> IngestionSnapshot | None:
    """Return the feed's latest downloaded snapshot, failed runs' included."""
    return (
        IngestionSnapshot.objects.filter(
            kind=SnapshotKind.GTFS_FEED,
            fetched_at__isnull=False,
            path__startswith=f"{RAW_DIRECTORY_NAME}/",
            path__endswith=f"-{feed.file_name}",
        )
        .order_by("-fetched_at", "-pk")
        .first()
    )


def _snapshot_path(snapshot: IngestionSnapshot) -> Path | None:
    """Return the snapshot's file, or None when it is no longer on disk."""
    path = Path(settings.RAW_DATA_DIRECTORY) / snapshot.path
    return path if path.is_file() else None


def _storable_header(response: requests.Response, name: str) -> str:
    """Return a response header as sent, or "" when absent or too long to store."""
    value = response.headers.get(name, "")
    return value if len(value) <= RESPONSE_HEADER_MAXIMUM_LENGTH else ""
