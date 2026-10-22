"""Tests for taking the MTA feeds through the command: conditional downloads, unchanged
feeds, saved snapshots, and failed requests.
"""

import hashlib
import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from axisence.ingestion.models import (
    IngestionOutcome,
    IngestionRun,
    IngestionSnapshot,
    SnapshotKind,
)
from axisence.transit.feeds import FEEDS
from axisence.transit.models import TransitStop
from tests.fixture_files import fixture_bytes

pytestmark = pytest.mark.django_db

LAST_MODIFIED = "Thu, 27 Aug 2026 13:39:18 GMT"


def _etag(feed):
    # The MTA's host sends the ETag with its quotes, and gets it back verbatim.
    return f'"synthetic-{feed.file_name}"'


def _serve_feeds(fake_transport):
    fake_transport.routes = []
    for feed in FEEDS:
        fake_transport.add(
            "GET",
            feed.url,
            fixture_bytes(f"mta-gtfs/{feed.file_name}"),
            headers={
                "Content-Type": "application/zip",
                "ETag": _etag(feed),
                "Last-Modified": LAST_MODIFIED,
            },
        )


def _serve_unchanged_feeds(fake_transport):
    fake_transport.routes = []
    for feed in FEEDS:
        fake_transport.add(
            "GET", feed.url, b"", status=304, headers={"ETag": _etag(feed)}
        )


def _run(*arguments):
    stdout = StringIO()
    call_command("ingest_mta_transit", *arguments, stdout=stdout, stderr=StringIO())
    return stdout.getvalue()


def _sent_etags(requests):
    return {
        request.url.rsplit("/", 1)[1]: request.headers.get("If-None-Match")
        for request in requests
    }


def test_a_first_run_downloads_saves_and_records_every_feed(
    fake_transport, raw_data_directory
):
    _serve_feeds(fake_transport)

    output = _run()

    run = IngestionRun.objects.get()
    snapshots = list(run.snapshots.order_by("pk"))
    snapshot_names = [snapshot.path.removeprefix("mta-gtfs/") for snapshot in snapshots]
    saved_names = [path.name for path in (raw_data_directory / "mta-gtfs").iterdir()]
    file_name = re.compile(rf"\d{{8}}T\d{{6}}Z-run-{run.pk}-gtfs_[a-z]+\.zip")
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert _sent_etags(fake_transport.requests) == {
        feed.file_name: None for feed in FEEDS
    }
    assert sorted(saved_names) == sorted(snapshot_names)
    assert all(file_name.fullmatch(name) for name in snapshot_names)
    assert [name.rsplit("-", 1)[1] for name in snapshot_names] == [
        feed.file_name for feed in FEEDS
    ]
    for feed, snapshot in zip(FEEDS, snapshots, strict=True):
        content = (raw_data_directory / snapshot.path).read_bytes()
        assert content == fixture_bytes(f"mta-gtfs/{feed.file_name}")
        assert snapshot.kind == SnapshotKind.GTFS_FEED
        assert snapshot.sha256 == hashlib.sha256(content).hexdigest()
        assert snapshot.size_bytes == len(content)
        assert snapshot.fetched_at is not None
        assert snapshot.response_etag == _etag(feed)
        assert snapshot.response_last_modified == LAST_MODIFIED
    for feed in FEEDS:
        assert f"Feed {feed.file_name}: downloaded\n" in output


def test_an_unchanged_feed_is_answered_from_its_latest_snapshot(
    fake_transport, raw_data_directory
):
    _serve_feeds(fake_transport)
    _run()
    first_run = IngestionRun.objects.get()
    first_paths = [snapshot.path for snapshot in first_run.snapshots.order_by("pk")]
    saved_files = sorted((raw_data_directory / "mta-gtfs").iterdir())
    _serve_unchanged_feeds(fake_transport)
    fake_transport.requests.clear()

    output = _run()

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    snapshots = list(run.snapshots.order_by("pk"))
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert not run.is_reingestion
    assert _sent_etags(fake_transport.requests) == {
        feed.file_name: _etag(feed) for feed in FEEDS
    }
    assert sorted((raw_data_directory / "mta-gtfs").iterdir()) == saved_files
    assert [snapshot.path for snapshot in snapshots] == first_paths
    assert {
        (snapshot.fetched_at, snapshot.response_etag, snapshot.response_last_modified)
        for snapshot in snapshots
    } == {(None, "", "")}
    assert run.unchanged_count == TransitStop.objects.count()
    for feed in FEEDS:
        assert f"Feed {feed.file_name}: unchanged\n" in output


def test_a_latest_snapshot_missing_from_disk_is_requested_without_its_etag(
    fake_transport, raw_data_directory
):
    _serve_feeds(fake_transport)
    _run()
    missing = IngestionSnapshot.objects.get(path__endswith="-gtfs_m.zip")
    (raw_data_directory / missing.path).unlink()
    fake_transport.requests.clear()

    output = _run()

    sent = _sent_etags(fake_transport.requests)
    assert sent.pop("gtfs_m.zip") is None
    assert all(sent.values())
    assert "Feed gtfs_m.zip: downloaded\n" in output


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "HTTP 404."), (None, "No fake route.")],
    ids=["not found", "connection error"],
)
def test_a_failed_request_fails_the_run_before_any_stop_is_written(
    fake_transport, status, message
):
    _serve_feeds(fake_transport)
    failing = next(feed for feed in FEEDS if feed.file_name == "gtfs_si.zip")
    fake_transport.routes = [
        route for route in fake_transport.routes if route[1] != failing.url
    ]
    if status is not None:
        fake_transport.add("GET", failing.url, b"Not Found", status=status)

    with pytest.raises(CommandError) as raised:
        _run()

    run = IngestionRun.objects.get()
    assert str(raised.value) == (
        f"MTA transit run {run.pk} failed: Fetching gtfs_si.zip failed: {message}"
    )
    assert run.outcome == IngestionOutcome.FAILED
    assert not TransitStop.objects.exists()


def test_a_run_from_the_latest_snapshots_requests_nothing(fake_transport):
    _serve_feeds(fake_transport)
    _run()
    first_run = IngestionRun.objects.get()
    request_count = len(fake_transport.requests)

    output = _run("--from-latest-snapshots")

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    assert len(fake_transport.requests) == request_count
    assert run.is_reingestion
    assert list(run.snapshots.values_list("path", flat=True)) == list(
        first_run.snapshots.values_list("path", flat=True)
    )
    assert "Re-ingested an existing snapshot.\n" in output
    for feed in FEEDS:
        assert f"Feed {feed.file_name}: read from the latest snapshot\n" in output


def test_a_run_from_the_latest_snapshots_fails_naming_a_feed_without_one(
    fake_transport, raw_data_directory
):
    _serve_feeds(fake_transport)
    _run()
    missing = IngestionSnapshot.objects.get(path__endswith="-gtfs_q.zip")
    (raw_data_directory / missing.path).unlink()

    with pytest.raises(CommandError) as raised:
        _run("--from-latest-snapshots")

    run = IngestionRun.objects.latest("pk")
    assert str(raised.value) == (
        f"MTA transit run {run.pk} failed: No saved snapshot of gtfs_q.zip is on "
        "disk; run ingest_mta_transit without --from-latest-snapshots first."
    )
