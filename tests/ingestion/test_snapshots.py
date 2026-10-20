"""Tests for saving, naming, and locating raw snapshots."""

import hashlib
import io
from datetime import UTC, datetime

import pytest
import requests

from axisence.ingestion import snapshots
from axisence.ingestion.errors import IngestionError

BODY = b"Facility ID,Facility Name\n1,Synthetic Day Care\n"
FETCHED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _response(body):
    response = requests.Response()
    response.status_code = 200
    response.raw = io.BytesIO(body)
    return response


def test_saved_snapshot_has_a_timestamped_name_size_and_hash_and_no_part_file(
    raw_data_directory,
):
    snapshot = snapshots.save_response(
        _response(BODY), "ny-ocfs", "export.csv", FETCHED_AT, 7
    )

    assert snapshot.relative_path == "ny-ocfs/20260102T030405Z-run-7-export.csv"
    assert snapshot.absolute_path.read_bytes() == BODY
    assert snapshot.size_bytes == len(BODY)
    assert snapshot.sha256 == hashlib.sha256(BODY).hexdigest()
    assert snapshot.fetched_at == FETCHED_AT
    assert list((raw_data_directory / "ny-ocfs").iterdir()) == [snapshot.absolute_path]


def test_an_existing_snapshot_name_is_never_overwritten():
    first = snapshots.save_response(
        _response(BODY), "ny-ocfs", "export.csv", FETCHED_AT, 7
    )

    with pytest.raises(IngestionError, match="never overwritten"):
        snapshots.save_response(
            _response(b"other"), "ny-ocfs", "export.csv", FETCHED_AT, 7
        )

    assert first.absolute_path.read_bytes() == BODY


def test_a_reingestion_path_outside_the_source_directory_is_refused(
    raw_data_directory, tmp_path
):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(BODY)
    other_source = raw_data_directory / "nyc-dohmh" / "export.csv"
    other_source.parent.mkdir(parents=True)
    other_source.write_bytes(BODY)
    (raw_data_directory / "ny-ocfs").mkdir()
    refused = (
        outside,
        other_source,
        raw_data_directory / "ny-ocfs" / ".." / "nyc-dohmh" / "export.csv",
        raw_data_directory / "ny-ocfs",
        raw_data_directory / "ny-ocfs" / "absent.csv",
    )

    for path in refused:
        with pytest.raises(IngestionError, match=r"under data/raw/ny-ocfs/"):
            snapshots.resolve_reingestion_path(str(path), "ny-ocfs")


def test_a_reingestion_path_inside_the_source_directory_is_accepted(
    raw_data_directory,
):
    saved = raw_data_directory / "ny-ocfs" / "export.csv"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(BODY)

    path = snapshots.resolve_reingestion_path(str(saved), "ny-ocfs")
    described = snapshots.describe_existing(path)

    assert path == saved.resolve()
    assert described.relative_path == "ny-ocfs/export.csv"
    assert described.size_bytes == len(BODY)
    assert described.sha256 == hashlib.sha256(BODY).hexdigest()
    assert described.fetched_at is None
