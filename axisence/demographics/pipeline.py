"""Run the Census demographics and tabulation-area boundaries ingestions.

Every response is saved as a snapshot and parsed from the saved file. Writes happen
only after every download parsed, inside one transaction.
"""

import json
import logging
from datetime import datetime

import requests
from django.db import transaction
from django.utils import timezone

from axisence.demographics import boundaries, census_api, loading, parsing
from axisence.demographics.models import CensusTable
from axisence.ingestion import http_session, runs, snapshots
from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import IngestionRun, IngestionSource, SnapshotKind
from axisence.ingestion.redaction import redact_secrets
from axisence.ingestion.snapshots import SavedSnapshot

logger = logging.getLogger(__name__)


def run_demographics_ingestion(vintage: int) -> IngestionRun:
    """Load the data profile tables DP02 to DP05 of one ACS 5-year vintage."""
    run = runs.start_run(IngestionSource.CENSUS_DEMOGRAPHICS, census_vintage=vintage)
    counts = runs.RunCounts()
    return runs.execute_run(
        run, counts, lambda: _ingest_demographics(vintage, run, counts)
    )


def run_boundaries_ingestion() -> IngestionRun:
    """Load the 2020 cartographic boundaries of the NY State tabulation areas."""
    run = runs.start_run(IngestionSource.CENSUS_BOUNDARIES)
    counts = runs.RunCounts()
    return runs.execute_run(run, counts, lambda: _ingest_boundaries(run, counts))


def _ingest_demographics(
    vintage: int, run: IngestionRun, counts: runs.RunCounts
) -> None:
    api_key = census_api.require_census_api_key()
    tables: list[parsing.ProfileTable] = []
    labels: dict[CensusTable, dict[str, tuple[str, str]]] = {}
    with http_session.build_session() as session:
        fetched_at = timezone.now()
        for table in CensusTable:
            data_snapshot = census_api.fetch_census_json(
                session,
                census_api.profile_url(vintage),
                {
                    "get": f"group({table.value})",
                    "ucgid": census_api.NEW_YORK_TABULATION_AREAS_PREDICATE,
                },
                api_key,
                census_api.CENSUS_RAW_DIRECTORY_NAME,
                f"acs5-profile-{vintage}-{table.value}.json",
                fetched_at,
                run.pk,
            )
            runs.record_snapshot(run, SnapshotKind.CENSUS_TABLE, data_snapshot)
            labels_snapshot = census_api.fetch_census_json(
                session,
                census_api.group_labels_url(vintage, table),
                {},
                api_key,
                census_api.CENSUS_RAW_DIRECTORY_NAME,
                f"acs5-profile-{vintage}-{table.value}-variables.json",
                fetched_at,
                run.pk,
            )
            runs.record_snapshot(run, SnapshotKind.CENSUS_VARIABLES, labels_snapshot)
            tables.append(parsing.parse_profile_table(table, _read_json(data_snapshot)))
            labels[table] = parsing.parse_variable_labels(
                table, _read_json(labels_snapshot)
            )

    field_warning_count = 0
    for profile_table in tables:
        for warning in profile_table.field_warnings:
            logger.warning(
                "Census field warning: area %s, column %s",
                warning.tabulation_area_code,
                warning.column,
            )
        field_warning_count += len(profile_table.field_warnings)
        table_labels = labels[profile_table.table]
        for code in profile_table.codes:
            if not table_labels.get(code, ("", ""))[0]:
                logger.warning("Census variable %s has no label", code)
                field_warning_count += 1
    counts.rows_read_count = sum(profile_table.rows_read for profile_table in tables)
    counts.field_warning_count = field_warning_count

    with transaction.atomic():
        load_counts = loading.store_census_vintage(vintage, tables, labels)
    counts.created_count = load_counts.created
    counts.updated_count = load_counts.updated
    counts.unchanged_count = load_counts.unchanged


def _ingest_boundaries(run: IngestionRun, counts: runs.RunCounts) -> None:
    api_key = census_api.require_census_api_key()
    with http_session.build_session() as session:
        fetched_at = timezone.now()
        area_list_snapshot = census_api.fetch_census_json(
            session,
            census_api.profile_url(census_api.TABULATION_AREA_LIST_VINTAGE),
            {
                "get": "NAME",
                "ucgid": census_api.NEW_YORK_TABULATION_AREAS_PREDICATE,
            },
            api_key,
            boundaries.BOUNDARY_RAW_DIRECTORY_NAME,
            f"acs5-profile-{census_api.TABULATION_AREA_LIST_VINTAGE}"
            "-new-york-tabulation-areas.json",
            fetched_at,
            run.pk,
        )
        runs.record_snapshot(run, SnapshotKind.TABULATION_AREA_LIST, area_list_snapshot)
        codes = parsing.parse_tabulation_area_codes(_read_json(area_list_snapshot))
        boundary_snapshot = _download_boundary_file(session, fetched_at, run.pk)
        runs.record_snapshot(run, SnapshotKind.BOUNDARY_FILE, boundary_snapshot)

    result = boundaries.read_boundaries(boundary_snapshot.absolute_path, codes)
    if not result.boundaries:
        raise IngestionError(
            "The boundary file holds none of the New York tabulation areas."
        )
    for code in result.missing_codes:
        logger.warning("Tabulation area %s is missing from the boundary file", code)
    for code in result.unusable_codes:
        logger.warning("Tabulation area %s has an unusable geometry", code)
    counts.rows_read_count = result.features_read
    counts.field_warning_count = len(result.missing_codes) + len(result.unusable_codes)

    # store_boundaries creates any missing area itself.
    with transaction.atomic():
        load_counts = loading.store_boundaries(result.boundaries)
    counts.created_count = load_counts.created
    counts.updated_count = load_counts.updated
    counts.unchanged_count = load_counts.unchanged


def _download_boundary_file(
    session: requests.Session, fetched_at: datetime, run_id: int
) -> SavedSnapshot:
    try:
        with session.get(
            boundaries.BOUNDARY_FILE_URL, stream=True, timeout=http_session.TIMEOUT
        ) as response:
            response.raise_for_status()
            return snapshots.save_response(
                response,
                boundaries.BOUNDARY_RAW_DIRECTORY_NAME,
                boundaries.BOUNDARY_FILE_NAME,
                fetched_at,
                run_id,
            )
    except requests.RequestException as error:
        raise IngestionError(
            f"Downloading the boundary file failed: {redact_secrets(str(error))}"
        ) from None


def _read_json(snapshot: SavedSnapshot) -> object:
    try:
        with snapshot.absolute_path.open(encoding="utf-8") as file:
            return json.load(file)
    except ValueError:
        raise IngestionError(
            f"The saved response {snapshot.relative_path} is not valid JSON."
        ) from None
