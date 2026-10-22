"""End-to-end tests of ingest_mta_transit on the trimmed real feeds: what it stores,
how it updates, how stops become inactive and active again, and the guard.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from axisence.ingestion.models import IngestionOutcome, IngestionRun
from axisence.transit.feeds import FEEDS
from axisence.transit.models import TransitMode, TransitStop
from tests.transit.gtfs_archives import fixture_tables, write_feed

pytestmark = pytest.mark.django_db

STOP_COUNT = 13


def _serve(fake_transport, directory, changes=None):
    """Serve every trimmed feed, each changed by its function in changes."""
    changes = changes or {}
    fake_transport.routes = []
    for feed in FEEDS:
        tables = fixture_tables(feed.file_name)
        if feed.file_name in changes:
            changes[feed.file_name](tables)
        body = write_feed(directory, feed.file_name, tables).read_bytes()
        fake_transport.add("GET", feed.url, body)


def _run(*arguments):
    stdout = StringIO()
    call_command("ingest_mta_transit", *arguments, stdout=stdout, stderr=StringIO())
    return stdout.getvalue()


def _stop(mode, published_stop_id):
    return TransitStop.objects.get(mode=mode, published_stop_id=published_stop_id)


def _line_names(stop):
    return [line.name for line in stop.lines.order_by("sort_position", "name")]


def _without_stop(stop_id):
    """Remove a stop's row, and a station's platforms with it."""

    def change(tables):
        header, *rows = tables["stops.txt"]
        cells = [dict(zip(header, row, strict=True)) for row in rows]
        tables["stops.txt"] = [
            header,
            *(
                row
                for row, cell in zip(rows, cells, strict=True)
                if stop_id not in (cell["stop_id"], cell.get("parent_station"))
            ),
        ]

    return change


def _renamed_stop(stop_id, name):
    def change(tables):
        for row in tables["stops.txt"]:
            if row[0] == stop_id:
                row[1] = name

    return change


def _blank_coordinates(stop_id):
    def change(tables):
        header = tables["stops.txt"][0]
        for row in tables["stops.txt"]:
            if row[0] == stop_id:
                row[header.index("stop_lat")] = ""
                row[header.index("stop_lon")] = ""

    return change


def test_the_feeds_are_stored_as_stations_bus_stops_and_lines(fake_transport, tmp_path):
    _serve(fake_transport, tmp_path)

    output = _run()

    run = IngestionRun.objects.get()
    stations = {
        stop.published_stop_id: (stop.name, stop.station_group_key, _line_names(stop))
        for stop in TransitStop.objects.filter(mode=TransitMode.SUBWAY)
    }
    shared = _stop(TransitMode.BUS, "306533")
    assert stations == {
        "232": ("Borough Hall", "232+423", ["2", "3"]),
        "423": ("Borough Hall", "232+423", ["4", "5"]),
        "R28": ("Court St", "R28", ["R", "W"]),
        "A41": ("Jay St-MetroTech", "A41+R29", ["A", "C", "F"]),
        "R29": ("Jay St-MetroTech", "A41+R29", ["R", "W"]),
    }
    assert TransitStop.objects.filter(mode=TransitMode.BUS).count() == 8
    assert shared.name == "CADMAN PLAZA WEST/MONTAGUE ST"
    assert shared.location.coords == (-73.990408, 40.693578)
    assert _line_names(shared) == ["B52", "B103"]
    assert all(stop.is_active for stop in TransitStop.objects.all())
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert (
        run.created_count,
        run.updated_count,
        run.unchanged_count,
        run.marked_missing_count,
        run.reactivated_count,
        run.field_warning_count,
    ) == (STOP_COUNT, 0, 0, 0, 0, 0)
    assert output.startswith(f"MTA transit run {run.pk} succeeded.\n")
    assert (
        "Created: 13\nUpdated: 0\nUnchanged: 0\nMarked missing: 0\nReactivated: 0\n"
        "Field warnings: 0\n"
    ) in output
    assert output.endswith(
        "".join(f"Feed {feed.file_name}: downloaded\n" for feed in FEEDS)
    )


def test_a_rerun_updates_only_the_changed_stop_under_its_published_id(
    fake_transport, tmp_path
):
    _serve(fake_transport, tmp_path)
    _run()
    first_run = IngestionRun.objects.get()
    court_street = _stop(TransitMode.SUBWAY, "R28")
    _serve(
        fake_transport,
        tmp_path,
        {"gtfs_subway.zip": _renamed_stop("R28", "Synthetic Renamed Station")},
    )

    _run()

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    renamed = _stop(TransitMode.SUBWAY, "R28")
    assert (run.created_count, run.updated_count, run.unchanged_count) == (
        0,
        1,
        STOP_COUNT - 1,
    )
    assert renamed.pk == court_street.pk
    assert renamed.name == "Synthetic Renamed Station"
    assert TransitStop.objects.count() == STOP_COUNT
    assert set(TransitStop.objects.values_list("first_seen_run", "last_seen_run")) == {
        (first_run.pk, run.pk)
    }


def test_an_absent_stop_is_marked_inactive_and_reactivated_when_listed_again(
    fake_transport, tmp_path
):
    _serve(fake_transport, tmp_path)
    _run()
    first_run = IngestionRun.objects.get()
    # The fixtures hold eight bus stops, so losing one needs the guard's allowance.
    _serve(fake_transport, tmp_path, {"gtfs_bx.zip": _without_stop("100019")})
    absent_output = _run("--allow-mass-disappearance")
    absent_run = IngestionRun.objects.latest("pk")
    absent = _stop(TransitMode.BUS, "100019")
    _serve(fake_transport, tmp_path)

    _run()

    listed_run = IngestionRun.objects.latest("pk")
    listed = _stop(TransitMode.BUS, "100019")
    assert absent_run.marked_missing_count == 1
    assert "Marked missing: 1\n" in absent_output
    assert not absent.is_active
    assert absent.last_seen_run_id == first_run.pk
    assert _line_names(absent) == ["Bx1"]
    assert listed.pk == absent.pk
    assert listed.is_active
    assert listed.last_seen_run_id == listed_run.pk
    assert (listed_run.reactivated_count, listed_run.unchanged_count) == (
        1,
        STOP_COUNT,
    )


def test_a_stop_whose_rows_are_all_skipped_stays_as_stored(fake_transport, tmp_path):
    _serve(fake_transport, tmp_path)
    _run()
    first_run = IngestionRun.objects.get()
    _serve(fake_transport, tmp_path, {"gtfs_busco.zip": _blank_coordinates("308940")})

    output = _run()

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    kept = _stop(TransitMode.BUS, "308940")
    assert kept.is_active
    assert kept.last_seen_run_id == first_run.pk
    assert kept.location.coords == (-73.990078, 40.692522)
    assert (run.marked_missing_count, run.unchanged_count) == (0, STOP_COUNT - 1)
    assert "Field warnings: 1\n" in output


def test_the_guard_refuses_losing_one_of_five_stations_before_writing(
    fake_transport, tmp_path
):
    _serve(fake_transport, tmp_path)
    _run()
    _serve(fake_transport, tmp_path, {"gtfs_subway.zip": _without_stop("R28")})

    with pytest.raises(CommandError) as raised:
        _run()

    run = IngestionRun.objects.latest("pk")
    assert str(raised.value) == (
        f"MTA transit run {run.pk} failed: Refusing to mark 1 of 5 active subway "
        "stops inactive, which is more than 10%. Run the command again with "
        "--allow-mass-disappearance if the MTA really removed them."
    )
    assert _stop(TransitMode.SUBWAY, "R28").is_active
    assert not TransitStop.objects.filter(last_seen_run=run).exists()

    output = _run("--allow-mass-disappearance")

    allowed = IngestionRun.objects.latest("pk")
    assert allowed.guard_override_used
    assert "The mass-disappearance guard was overridden.\n" in output
    assert not _stop(TransitMode.SUBWAY, "R28").is_active


def test_a_mode_without_active_stops_is_never_guarded(fake_transport, tmp_path):
    _serve(fake_transport, tmp_path)
    _run()
    TransitStop.objects.filter(mode=TransitMode.SUBWAY).update(is_active=False)
    _serve(fake_transport, tmp_path, {"gtfs_subway.zip": _without_stop("R28")})

    _run()

    run = IngestionRun.objects.latest("pk")
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert not run.guard_override_used
    assert (run.marked_missing_count, run.reactivated_count) == (0, 4)
    assert not _stop(TransitMode.SUBWAY, "R28").is_active
