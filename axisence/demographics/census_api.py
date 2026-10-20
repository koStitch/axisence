"""Requests to the US Census Data API, each saved as a raw snapshot.

The key travels only as the key query parameter and is redacted from every message.
A rejected key answers HTTP 200 with an HTML page, so a 200 alone proves nothing.
"""

from collections.abc import Mapping
from datetime import datetime
from http import HTTPStatus
from urllib.parse import urlparse

import requests
from django.conf import settings

from axisence.demographics.models import CensusTable
from axisence.ingestion import http_session, snapshots
from axisence.ingestion.errors import IngestionError
from axisence.ingestion.redaction import redact_secrets
from axisence.ingestion.snapshots import SavedSnapshot

CENSUS_DATA_API_ROOT = "https://api.census.gov/data"
NEW_YORK_TABULATION_AREAS_PREDICATE = "pseudo(0400000US36$8600000)"
# Every ACS release from 2020 to 2029 uses the 2020 tabulation areas, so any vintage in
# that range lists the same areas.
TABULATION_AREA_LIST_VINTAGE = 2024
CENSUS_RAW_DIRECTORY_NAME = "census-acs-profile"
CENSUS_API_KEY_SIGNUP_URL = "https://api.census.gov/data/key_signup.html"

_REJECTED_KEY_PAGES = ("missing_key.html", "invalid_key.html")
_JSON_CONTENT_TYPE = "application/json"


def profile_url(vintage: int) -> str:
    """Return the ACS 5-year data profile endpoint of a vintage."""
    return f"{CENSUS_DATA_API_ROOT}/{vintage}/acs/acs5/profile"


def group_labels_url(vintage: int, table: CensusTable) -> str:
    """Return the variable labels of one data profile table."""
    return f"{profile_url(vintage)}/groups/{table.value}.json"


def require_census_api_key() -> str:
    """Return the configured key, or fail the run before any request."""
    api_key = settings.CENSUS_API_KEY.strip()
    if not api_key:
        raise IngestionError(
            f"CENSUS_API_KEY is not set. Request a key at {CENSUS_API_KEY_SIGNUP_URL}, "
            "add it to .env as CENSUS_API_KEY, and run the command again."
        )
    return api_key


def fetch_census_json(
    session: requests.Session,
    url: str,
    parameters: Mapping[str, str],
    api_key: str,
    directory_name: str,
    name: str,
    fetched_at: datetime,
    run_id: int,
) -> SavedSnapshot:
    """Save one JSON response of the Census Data API as a snapshot."""
    try:
        with session.get(
            url,
            params={**parameters, "key": api_key},
            stream=True,
            timeout=http_session.TIMEOUT,
        ) as response:
            if response.status_code == HTTPStatus.NOT_FOUND:
                raise IngestionError(
                    "The Census Data API has no data at this address; check the "
                    "vintage."
                )
            # A server error after retries reports as a failed request, not as a
            # rejected key.
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if urlparse(response.url).path.endswith(
                _REJECTED_KEY_PAGES
            ) or not content_type.startswith(_JSON_CONTENT_TYPE):
                raise IngestionError(
                    "The Census Data API rejected the request; check CENSUS_API_KEY."
                )
            return snapshots.save_response(
                response, directory_name, name, fetched_at, run_id
            )
    except requests.RequestException as error:
        raise IngestionError(
            f"The Census Data API request failed: {redact_secrets(str(error))}"
        ) from None
