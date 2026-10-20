"""Fetch a Socrata dataset's metadata and full CSV export as raw snapshots."""

import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import requests
from django.conf import settings

from axisence.ingestion import http_session, snapshots
from axisence.ingestion.errors import IngestionError
from axisence.ingestion.redaction import redact_secrets
from axisence.ingestion.snapshots import SavedSnapshot

logger = logging.getLogger(__name__)

SOCRATA_AUTHENTICATION_HEADER = "X-App-Token"


@dataclasses.dataclass(frozen=True, slots=True)
class SocrataDataset:
    domain: str
    identifier: str
    raw_directory_name: str
    file_stem: str

    @property
    def export_url(self) -> str:
        """The full CSV export, with the display names of the columns as headers."""
        return (
            f"https://{self.domain}/api/views/{self.identifier}/rows.csv"
            "?accessType=DOWNLOAD"
        )

    @property
    def metadata_url(self) -> str:
        """The dataset metadata, holding rowsUpdatedAt."""
        return f"https://{self.domain}/api/views/{self.identifier}.json"

    @property
    def metadata_name(self) -> str:
        return f"{self.file_stem}-metadata.json"

    @property
    def export_name(self) -> str:
        return f"{self.file_stem}-export.csv"


@dataclasses.dataclass(frozen=True, slots=True)
class FetchedDataset:
    metadata: SavedSnapshot
    export: SavedSnapshot
    rows_updated_at: datetime | None


@dataclasses.dataclass(frozen=True, slots=True)
class SavedMetadata:
    snapshot: SavedSnapshot
    rows_updated_at: datetime | None


def fetch_dataset(
    session: requests.Session,
    dataset: SocrataDataset,
    fetched_at: datetime,
    run_id: int,
) -> FetchedDataset:
    """Save the metadata, then the export, and read when the publisher last updated."""
    app_token = settings.SOCRATA_APP_TOKEN.strip()
    headers = {SOCRATA_AUTHENTICATION_HEADER: app_token} if app_token else {}
    try:
        metadata = _fetch(
            session,
            dataset.metadata_url,
            headers,
            dataset.raw_directory_name,
            dataset.metadata_name,
            fetched_at,
            run_id,
        )
        export = _fetch(
            session,
            dataset.export_url,
            headers,
            dataset.raw_directory_name,
            dataset.export_name,
            fetched_at,
            run_id,
        )
    except requests.RequestException as error:
        raise IngestionError(
            f"Fetching {dataset.identifier} failed: {redact_secrets(str(error))}"
        ) from None
    return FetchedDataset(
        metadata=metadata,
        export=export,
        rows_updated_at=_read_rows_updated_at(
            metadata.absolute_path, dataset.identifier
        ),
    )


def read_saved_metadata(
    dataset: SocrataDataset, export_path: Path
) -> SavedMetadata | None:
    """Return the metadata saved with an export snapshot, and its publisher time.

    A fetch saves both files under one time and run, so the metadata's name is the
    export's with the other suffix. None when the export was not named by a fetch or
    its metadata is not stored.
    """
    if not export_path.name.endswith(dataset.export_name):
        return None
    metadata_path = export_path.with_name(
        export_path.name.removesuffix(dataset.export_name) + dataset.metadata_name
    )
    if not metadata_path.is_file():
        return None
    return SavedMetadata(
        snapshot=snapshots.describe_existing(metadata_path),
        rows_updated_at=_read_rows_updated_at(metadata_path, dataset.identifier),
    )


def _fetch(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    directory_name: str,
    name: str,
    fetched_at: datetime,
    run_id: int,
) -> SavedSnapshot:
    with session.get(
        url, headers=headers, stream=True, timeout=http_session.TIMEOUT
    ) as response:
        response.raise_for_status()
        return snapshots.save_response(
            response, directory_name, name, fetched_at, run_id
        )


def _read_rows_updated_at(metadata_path: Path, identifier: str) -> datetime | None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except ValueError:
        metadata = None
    rows_updated_at = (
        metadata.get("rowsUpdatedAt") if isinstance(metadata, dict) else None
    )
    # bool is a subclass of int, and never a timestamp.
    if isinstance(rows_updated_at, int) and not isinstance(rows_updated_at, bool):
        return datetime.fromtimestamp(rows_updated_at, tz=UTC)
    logger.warning("The %s metadata has no rowsUpdatedAt", identifier)
    return None
