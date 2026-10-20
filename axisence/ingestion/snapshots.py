"""Raw snapshots: every download saved unchanged before anything parses it.

The pipeline never modifies or deletes a snapshot, and nothing prunes them.
"""

import dataclasses
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import requests
from django.conf import settings

from axisence.ingestion.errors import IngestionError

CHUNK_SIZE_BYTES = 1_048_576


@dataclasses.dataclass(frozen=True, slots=True)
class SavedSnapshot:
    absolute_path: Path
    relative_path: str
    size_bytes: int
    sha256: str
    fetched_at: datetime | None


def source_directory(directory_name: str) -> Path:
    """Return the raw directory of one source."""
    return Path(settings.RAW_DATA_DIRECTORY) / directory_name


def snapshot_file_name(fetched_at: datetime, run_id: int, name: str) -> str:
    """Return the file name of a snapshot fetched by a run."""
    return f"{fetched_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-run-{run_id}-{name}"


def save_response(
    response: requests.Response,
    directory_name: str,
    name: str,
    fetched_at: datetime,
    run_id: int,
) -> SavedSnapshot:
    """Stream a response body into a new snapshot file, hashing it while writing."""
    directory = source_directory(directory_name)
    directory.mkdir(parents=True, exist_ok=True)
    file_name = snapshot_file_name(fetched_at, run_id, name)
    target = directory / file_name
    if target.exists():
        raise IngestionError(
            f"A snapshot named {file_name} already exists; it is never overwritten."
        )
    # The body lands under a temporary name first, so a target file is always complete.
    partial = directory / f"{file_name}.part"
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with partial.open("xb") as file:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE_BYTES):
                file.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
        partial.rename(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return SavedSnapshot(
        absolute_path=target,
        relative_path=_relative_path(target),
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        fetched_at=fetched_at,
    )


def describe_existing(path: Path) -> SavedSnapshot:
    """Return the size and hash of a snapshot saved earlier, for re-ingestion."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE_BYTES):
            digest.update(chunk)
            size_bytes += len(chunk)
    return SavedSnapshot(
        absolute_path=path,
        relative_path=_relative_path(path),
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        fetched_at=None,
    )


def resolve_reingestion_path(argument: str, directory_name: str) -> Path:
    """Return the snapshot a re-ingestion names, which must lie in the source directory.

    A relative argument is relative to the current working directory, /app in the
    container.
    """
    path = Path(argument).resolve()
    directory = source_directory(directory_name).resolve()
    if not (path.is_file() and path.is_relative_to(directory)):
        raise IngestionError(
            f"The snapshot must be an existing file under data/raw/{directory_name}/."
        )
    return path


def _relative_path(path: Path) -> str:
    raw_directory = Path(settings.RAW_DATA_DIRECTORY).resolve()
    return path.resolve().relative_to(raw_directory).as_posix()
