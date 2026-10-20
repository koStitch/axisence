"""End-to-end tests of the two provider ingestion commands, with fake routes."""

import hashlib
import logging
import re
from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import (
    IngestionOutcome,
    IngestionRun,
    IngestionSnapshot,
    SnapshotKind,
)
from axisence.providers import geocoding, grouping
from axisence.providers.models import Provider, ProviderSource, ReviewFlag
from axisence.providers.ny_state import NY_STATE_DATASET
from axisence.providers.nyc_health import NYC_HEALTH_DATASET
from tests.fixture_files import fixture_bytes
from tests.providers.geocoder_fakes import recorded_result_for

NY_STATE_EXPORT = fixture_bytes("providers/ny-state-export.csv")
NY_STATE_ROW_COUNT = 21
JSON_HEADERS = {"Content-Type": "application/json;charset=utf-8"}
CSV_HEADERS = {"Content-Type": "text/csv; charset=utf-8"}
SYNTHETIC_SOCRATA_APP_VALUE = "synthetic-socrata-app-value"


def _serve_dataset(
    fake_transport, dataset, fixture_stem, export=None, status=200, metadata=None
):
    fake_transport.add(
        "GET",
        dataset.metadata_url,
        metadata or fixture_bytes(f"providers/{fixture_stem}-metadata.json"),
        headers=JSON_HEADERS,
    )
    fake_transport.add(
        "GET",
        dataset.export_url,
        export or fixture_bytes(f"providers/{fixture_stem}-export.csv"),
        status=status,
        headers=CSV_HEADERS,
    )


def _serve_sources(fake_transport):
    _serve_dataset(fake_transport, NY_STATE_DATASET, "ny-state")
    _serve_dataset(fake_transport, NYC_HEALTH_DATASET, "nyc-health")
    fake_transport.add("POST", geocoding.GEOCODER_BATCH_URL, recorded_result_for)


def _run(command, *arguments):
    stdout = StringIO()
    call_command(command, *arguments, stdout=stdout, stderr=StringIO())
    return stdout.getvalue()


def _provider(source_identifier):
    return Provider.objects.get(source_identifier=source_identifier)


def _export_snapshot_path(raw_data_directory, run):
    snapshot = run.snapshots.get(kind=SnapshotKind.PROVIDER_EXPORT)
    return raw_data_directory / snapshot.path


@pytest.mark.django_db
def test_ny_state_command_fetches_saves_parses_groups_and_geocodes(
    fake_transport, raw_data_directory
):
    _serve_sources(fake_transport)

    output = _run("ingest_ny_state_providers")

    run = IngestionRun.objects.get()
    file_name = re.compile(
        rf"^\d{{8}}T\d{{6}}Z-run-{run.pk}-child-care-regulated-programs-"
        r"(metadata\.json|export\.csv)$"
    )
    saved = sorted(path.name for path in (raw_data_directory / "ny-ocfs").iterdir())
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert len(saved) == 2
    assert all(file_name.match(name) for name in saved)
    assert len(list((raw_data_directory / "census-geocoder").iterdir())) == 1
    for snapshot in IngestionSnapshot.objects.all():
        content = (raw_data_directory / snapshot.path).read_bytes()
        assert snapshot.sha256 == hashlib.sha256(content).hexdigest()
        assert snapshot.size_bytes == len(content)
    assert run.rows_read_count == NY_STATE_ROW_COUNT
    assert run.created_count == NY_STATE_ROW_COUNT
    assert run.publisher_rows_updated_at == datetime.fromtimestamp(1792470220, tz=UTC)
    assert run.geocoded_count == 2
    assert output.startswith(f"NY State providers run {run.pk} succeeded.\n")
    assert "Rows read: 21\n" in output


@pytest.mark.django_db
def test_nyc_health_command_groups_with_existing_ny_state_providers(fake_transport):
    _serve_sources(fake_transport)
    _run("ingest_ny_state_providers")

    output = _run("ingest_nyc_health_providers")

    shared = {
        _provider(identifier).competitor
        for identifier in ("143049", "DC10592", "DC21351")
    }
    run = IngestionRun.objects.get(source="nyc_health_providers")
    assert len(shared) == 1
    for first, second in (("DC10665", "DC841"), ("145691", "DC10455")):
        pair = {_provider(first).competitor, _provider(second).competitor}
        assert len(pair) == 2
        assert ReviewFlag.objects.filter(
            flagged_competitor__in=pair, proposed_competitor__in=pair
        ).exists()
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert output.startswith(f"NYC Health providers run {run.pk} succeeded.\n")


@pytest.mark.django_db
def test_rerunning_on_the_fetched_snapshot_creates_updates_and_marks_nothing(
    fake_transport, raw_data_directory
):
    _serve_sources(fake_transport)
    _run("ingest_ny_state_providers")
    first_run = IngestionRun.objects.get()
    request_count = len(fake_transport.requests)

    output = _run(
        "ingest_ny_state_providers",
        "--snapshot",
        str(_export_snapshot_path(raw_data_directory, first_run)),
    )

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    assert run.is_reingestion
    assert (run.created_count, run.updated_count, run.marked_missing_count) == (
        0,
        0,
        0,
    )
    assert run.unchanged_count == NY_STATE_ROW_COUNT
    assert len(fake_transport.requests) == request_count
    assert "Re-ingested an existing snapshot.\n" in output


@pytest.mark.django_db
def test_a_reingestion_takes_the_publisher_time_saved_with_its_export(
    fake_transport, raw_data_directory
):
    _serve_sources(fake_transport)
    _run("ingest_ny_state_providers")
    first_run = IngestionRun.objects.get()

    output = _run(
        "ingest_ny_state_providers",
        "--snapshot",
        str(_export_snapshot_path(raw_data_directory, first_run)),
    )

    run = IngestionRun.objects.exclude(pk=first_run.pk).get()
    assert run.publisher_rows_updated_at == first_run.publisher_rows_updated_at
    assert run.publisher_rows_updated_at is not None
    assert {(snapshot.kind, snapshot.path) for snapshot in run.snapshots.all()} >= {
        (snapshot.kind, snapshot.path)
        for snapshot in first_run.snapshots.filter(
            kind__in=(SnapshotKind.PROVIDER_METADATA, SnapshotKind.PROVIDER_EXPORT)
        )
    }
    assert "Publisher last updated: " in output


@pytest.mark.django_db
def test_a_reingested_export_without_saved_metadata_has_no_publisher_time(
    fake_transport, raw_data_directory, caplog
):
    export = (
        raw_data_directory
        / "ny-ocfs"
        / "20260102T030405Z-run-999-child-care-regulated-programs-export.csv"
    )
    export.parent.mkdir(parents=True)
    export.write_bytes(NY_STATE_EXPORT)
    fake_transport.add("POST", geocoding.GEOCODER_BATCH_URL, recorded_result_for)

    with caplog.at_level(logging.WARNING):
        output = _run("ingest_ny_state_providers", "--snapshot", str(export))

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert run.publisher_rows_updated_at is None
    assert not run.snapshots.filter(kind=SnapshotKind.PROVIDER_METADATA).exists()
    assert "Publisher last updated" not in output
    assert "The cb42-qumz export snapshot has no saved metadata beside it" in [
        record.getMessage() for record in caplog.records
    ]


@pytest.mark.django_db
def test_socrata_token_is_sent_only_when_set_and_never_stored_or_logged(
    fake_transport, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    _serve_sources(fake_transport)
    _run("ingest_ny_state_providers")
    without_token = list(fake_transport.requests)
    settings.SOCRATA_APP_TOKEN = SYNTHETIC_SOCRATA_APP_VALUE

    _run("ingest_ny_state_providers")

    with_token = fake_transport.requests[len(without_token) :]
    socrata_requests = [
        request for request in with_token if "data.ny.gov" in request.url
    ]
    assert all("X-App-Token" not in request.headers for request in without_token)
    assert len(socrata_requests) == 2
    assert all(
        request.headers["X-App-Token"] == SYNTHETIC_SOCRATA_APP_VALUE
        for request in socrata_requests
    )
    assert all(
        "X-App-Token" not in request.headers
        for request in with_token
        if "data.ny.gov" not in request.url
    )
    assert SYNTHETIC_SOCRATA_APP_VALUE not in caplog.text
    for run in IngestionRun.objects.all():
        assert SYNTHETIC_SOCRATA_APP_VALUE not in run.failure_message
        for snapshot in run.snapshots.all():
            assert SYNTHETIC_SOCRATA_APP_VALUE not in snapshot.path


@pytest.mark.django_db
@pytest.mark.parametrize(
    "metadata",
    [b'{"id": "cb42-qumz", "rowsUpdatedAt": "yesterday"}', b"not json"],
    ids=["no timestamp", "not json"],
)
def test_metadata_without_rows_updated_at_leaves_the_update_time_unknown(
    fake_transport, caplog, metadata
):
    _serve_dataset(fake_transport, NY_STATE_DATASET, "ny-state", metadata=metadata)
    fake_transport.add("POST", geocoding.GEOCODER_BATCH_URL, recorded_result_for)

    output = _run("ingest_ny_state_providers")

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert run.publisher_rows_updated_at is None
    assert "Publisher last updated" not in output
    assert "The cb42-qumz metadata has no rowsUpdatedAt" in caplog.text


@pytest.mark.django_db
def test_http_failure_fails_the_run_before_any_write(fake_transport):
    _serve_dataset(fake_transport, NY_STATE_DATASET, "ny-state", status=500)

    with pytest.raises(CommandError, match="Fetching cb42-qumz failed"):
        _run("ingest_ny_state_providers")

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.FAILED
    assert run.failure_message.startswith("Fetching cb42-qumz failed: 500")
    assert run.finished_at is not None
    assert not Provider.objects.exists()


@pytest.mark.django_db
def test_missing_header_fails_the_run_and_the_run_record_survives(fake_transport):
    header, *rows = NY_STATE_EXPORT.decode("utf-8").splitlines(keepends=True)
    export = (header.replace("Zip Code,", "Postal Code,") + "".join(rows)).encode()
    _serve_dataset(fake_transport, NY_STATE_DATASET, "ny-state", export=export)

    with pytest.raises(CommandError, match="missing expected columns: Zip Code"):
        _run("ingest_ny_state_providers")

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.FAILED
    assert run.snapshots.count() == 2
    assert not Provider.objects.exists()


@pytest.mark.django_db
def test_failure_inside_the_write_transaction_rolls_back_everything(
    fake_transport, monkeypatch
):
    _serve_sources(fake_transport)

    def fail(run):
        raise IngestionError("Synthetic grouping failure.")

    monkeypatch.setattr(grouping, "assign_competitors", fail)

    with pytest.raises(CommandError, match="Synthetic grouping failure"):
        _run("ingest_ny_state_providers")

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.FAILED
    assert run.rows_read_count == NY_STATE_ROW_COUNT
    assert run.created_count is None
    assert not Provider.objects.exists()


@pytest.mark.django_db
def test_snapshot_outside_the_source_directory_fails_the_run(fake_transport, tmp_path):
    outside = tmp_path / "export.csv"
    outside.write_bytes(NY_STATE_EXPORT)

    with pytest.raises(CommandError, match=r"under data/raw/ny-ocfs/"):
        _run("ingest_ny_state_providers", "--snapshot", str(outside))

    run = IngestionRun.objects.get()
    assert run.outcome == IngestionOutcome.FAILED
    assert run.is_reingestion
    assert fake_transport.requests == []


@pytest.mark.django_db
def test_guard_override_option_lets_a_mass_disappearance_run_proceed(
    fake_transport, raw_data_directory
):
    _serve_sources(fake_transport)
    _run("ingest_ny_state_providers")
    shortened = raw_data_directory / "ny-ocfs" / "shortened-export.csv"
    shortened.write_bytes(b"".join(NY_STATE_EXPORT.splitlines(keepends=True)[:3]))

    with pytest.raises(CommandError, match="--allow-mass-disappearance"):
        _run("ingest_ny_state_providers", "--snapshot", str(shortened))
    refused_missing = Provider.objects.filter(missing_since__isnull=False).count()
    output = _run(
        "ingest_ny_state_providers",
        "--snapshot",
        str(shortened),
        "--allow-mass-disappearance",
    )

    run = IngestionRun.objects.latest("started_at")
    assert refused_missing == 0
    assert run.guard_override_used
    assert run.marked_missing_count == NY_STATE_ROW_COUNT - 2
    assert (
        Provider.objects.filter(
            source=ProviderSource.NY_STATE, missing_since__isnull=False
        ).count()
        == NY_STATE_ROW_COUNT - 2
    )
    assert "The mass-disappearance guard was overridden.\n" in output
