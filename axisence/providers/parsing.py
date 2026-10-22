"""Shared types and cell rules for parsing the provider exports into records.

Parsing is pure: it reads rows and returns records, warnings, and skipped rows, and
never touches the database.
"""

import collections
import csv
import dataclasses
import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from axisence.ingestion.coordinates import Coordinates, is_inside_new_york_state
from axisence.ingestion.errors import IngestionError
from axisence.providers import normalization
from axisence.providers.models import (
    SOURCE_IDENTIFIER_MAXIMUM_LENGTH,
    ProviderKind,
    ProviderSource,
)

MAXIMUM_TEXT_LENGTH = 255
# The largest value a PositiveIntegerField holds in PostgreSQL.
MAXIMUM_COUNT = 2_147_483_647
UNKNOWN_COUNT_MARKER = "NO DATA"

_DIGITS = re.compile(r"[0-9]+")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@dataclasses.dataclass(frozen=True, slots=True)
class FieldWarning:
    """A cell that could not be used; field names the published column or columns."""

    source: ProviderSource
    source_identifier: str
    field: str


@dataclasses.dataclass(frozen=True, slots=True)
class SkippedRow:
    """A row that was not ingested; row_number is 1-based among data rows."""

    source: ProviderSource
    row_number: int
    reason: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ProviderRecord:
    """One parsed row, with one attribute per name in INGESTED_FIELD_NAMES."""

    source: ProviderSource
    source_identifier: str
    kind: ProviderKind
    original_name: str = ""
    display_name: str = ""
    name_key: str = ""
    street_address: str = ""
    published_zip_code: str = ""
    zip_key: str = ""
    address_key: str = ""
    borough: str = ""
    phone_number: str = ""
    total_capacity: int | None = None
    ny_state_program_type: str = ""
    ny_state_region_code: str = ""
    ny_state_county: str = ""
    ny_state_facility_status: str = ""
    ny_state_facility_opened_on: date | None = None
    ny_state_license_issued_on: date | None = None
    ny_state_license_expires_on: date | None = None
    ny_state_is_address_omitted: bool | None = None
    ny_state_is_phone_omitted: bool | None = None
    ny_state_street_number: str = ""
    ny_state_street_name: str = ""
    ny_state_additional_address: str = ""
    ny_state_floor: str = ""
    ny_state_apartment: str = ""
    ny_state_city: str = ""
    ny_state_state: str = ""
    ny_state_phone_extension: str = ""
    ny_state_licensee_name: str = ""
    ny_state_school_district_name: str = ""
    ny_state_capacity_description: str = ""
    ny_state_infant_capacity: int | None = None
    ny_state_toddler_capacity: int | None = None
    ny_state_preschool_capacity: int | None = None
    ny_state_school_age_capacity: int | None = None
    ny_state_program_profile_url: str = ""
    nyc_health_permit_number: str = ""
    nyc_health_facility_type: str = ""
    nyc_health_program_type: str = ""
    nyc_health_child_age_range: str = ""
    nyc_health_administers_medication: bool | None = None
    nyc_health_building_identification_number: str = ""
    nyc_health_borough_block_lot: str = ""
    nyc_health_community_board: str = ""
    nyc_health_council_district: str = ""
    nyc_health_census_tract: str = ""
    coordinates: Coordinates | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ParseResult:
    records: tuple[ProviderRecord, ...]
    skipped_rows: tuple[SkippedRow, ...]
    field_warnings: tuple[FieldWarning, ...]
    # Identifiers whose rows were all skipped for repeating; their providers stay as
    # they are, neither updated nor marked missing.
    repeated_identifiers: frozenset[str]


def read_export(path: Path, required_headers: Sequence[str]) -> list[dict[str, str]]:
    """Read a CSV export, requiring every expected header and ignoring extra ones.

    A byte that is not UTF-8 becomes a replacement character instead of failing the
    whole export.
    """
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames
        if not headers:
            raise IngestionError("The export has no header row.")
        missing = [header for header in required_headers if header not in headers]
        if missing:
            raise IngestionError(
                f"The export is missing expected columns: {', '.join(missing)}."
            )
        # An extra field lands under the key None, and a missing value is None.
        rows = [
            {key: value or "" for key, value in row.items() if key is not None}
            for row in reader
        ]
    if not rows:
        raise IngestionError("The export has no data rows.")
    return rows


def unique_rows(
    rows: Sequence[Mapping[str, str]], identifier_column: str, source: ProviderSource
) -> tuple[list[tuple[str, Mapping[str, str]]], list[SkippedRow], frozenset[str]]:
    """Return the rows whose identifier is present, storable, and unique, in order.

    Every row of a blank, too long, or repeated identifier is skipped, and the
    repeated identifiers are returned too. A too-long identifier cannot match a
    stored provider, so it is never among the repeated ones.
    """
    identifiers = [row.get(identifier_column, "").strip() for row in rows]
    occurrences = collections.Counter(identifiers)
    unique: list[tuple[str, Mapping[str, str]]] = []
    skipped: list[SkippedRow] = []
    for row_number, (identifier, row) in enumerate(
        zip(identifiers, rows, strict=True), start=1
    ):
        if not identifier:
            skipped.append(SkippedRow(source, row_number, "blank source identifier"))
        elif not _is_storable_identifier(identifier):
            skipped.append(SkippedRow(source, row_number, "source identifier too long"))
        elif occurrences[identifier] > 1:
            skipped.append(SkippedRow(source, row_number, "repeated source identifier"))
        else:
            unique.append((identifier, row))
    repeated = frozenset(
        identifier
        for identifier, count in occurrences.items()
        if identifier and _is_storable_identifier(identifier) and count > 1
    )
    return unique, skipped, repeated


def _is_storable_identifier(identifier: str) -> bool:
    return len(identifier) <= SOURCE_IDENTIFIER_MAXIMUM_LENGTH


class RowWarnings:
    """Apply the cell rules to one row, collecting a field warning for each problem."""

    def __init__(self, source: ProviderSource, source_identifier: str) -> None:
        self.source = source
        self.source_identifier = source_identifier
        self.warnings: list[FieldWarning] = []

    def warn(self, field: str) -> None:
        """Record a field problem once per row; the cell is stored as unknown."""
        warning = FieldWarning(self.source, self.source_identifier, field)
        if warning not in self.warnings:
            self.warnings.append(warning)

    def text(
        self, field: str, value: str, maximum_length: int = MAXIMUM_TEXT_LENGTH
    ) -> str:
        """Return the value unchanged, or "" when it is too long for its column."""
        if len(value) > maximum_length:
            self.warn(field)
            return ""
        return value

    def count(self, field: str, value: str) -> int | None:
        """Return a count without thousands separators; blank or NO DATA is unknown."""
        stripped = value.strip()
        if stripped == "" or stripped.upper() == UNKNOWN_COUNT_MARKER:
            return None
        digits = stripped.replace(",", "")
        if _DIGITS.fullmatch(digits) and int(digits) <= MAXIMUM_COUNT:
            return int(digits)
        self.warn(field)
        return None

    def us_date(self, field: str, value: str) -> date | None:
        """Return a MM/DD/YYYY date; blank is unknown."""
        stripped = value.strip()
        if stripped == "":
            return None
        match = _US_DATE.match(stripped)
        if match is None:
            self.warn(field)
            return None
        month, day, year = (int(group) for group in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            self.warn(field)
            return None

    def zip_key(self, published: str) -> str:
        """Return the zip key, warning when a non-blank value is not a zip code."""
        key = normalization.zip_key(published)
        if published.strip() and not key:
            self.warn("Zip Code")
        return key

    def coordinates(self, latitude: str, longitude: str) -> Coordinates | None:
        """Return the point, or None when absent, unparseable, or outside the state."""
        latitude_text = latitude.strip()
        longitude_text = longitude.strip()
        if not latitude_text and not longitude_text:
            return None
        try:
            parsed = Coordinates(
                longitude=Decimal(longitude_text), latitude=Decimal(latitude_text)
            )
        except InvalidOperation:
            self.warn("Latitude and Longitude")
            return None
        if not (
            parsed.longitude.is_finite()
            and parsed.latitude.is_finite()
            and is_inside_new_york_state(parsed)
        ):
            self.warn("Latitude and Longitude")
            return None
        return parsed
