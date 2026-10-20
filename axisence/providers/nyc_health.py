"""Parse the NYC Health Active Child Care Programs export into records."""

import collections
import dataclasses
import re
from collections.abc import Mapping, Sequence

from axisence.ingestion.socrata import SocrataDataset
from axisence.providers import normalization
from axisence.providers.models import (
    PHONE_NUMBER_MAXIMUM_LENGTH,
    PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH,
    Borough,
    ProviderKind,
    ProviderSource,
)
from axisence.providers.parsing import (
    FieldWarning,
    ParseResult,
    ProviderRecord,
    RowWarnings,
    unique_rows,
)

NYC_HEALTH_DATASET = SocrataDataset(
    domain="data.cityofnewyork.us",
    identifier="gy3q-4tzp",
    raw_directory_name="nyc-dohmh",
    file_stem="active-child-care-programs",
)

# Export order. NTA Code follows the 2010 boundaries, so it is not read.
NYC_HEALTH_REQUIRED_HEADERS = (
    "DCID",
    "Permit Number",
    "Program Name",
    "Facility Type",
    "Program Type",
    "Street Address",
    "Borough",
    "Zip Code",
    "Phone Number",
    "Child Age Range",
    "Children Allowed in Care",
    "Administer Medication",
    "BIN",
    "BBL",
    "Community Board",
    "Council District",
    "Census Tract",
    "Latitude",
    "Longitude",
)

NYC_HEALTH_KINDS = {
    "GCC": ProviderKind.DAY_CARE_CENTER,
    "SBCC": ProviderKind.SCHOOL_BASED_CHILD_CARE,
}

KNOWN_PROGRAM_TYPES = frozenset({"PRESCHOOL", "INFANT TODDLER"})

BOROUGHS_BY_NAME = {
    "MANHATTAN": Borough.MANHATTAN,
    "BRONX": Borough.BRONX,
    "BROOKLYN": Borough.BROOKLYN,
    "QUEENS": Borough.QUEENS,
    "STATEN ISLAND": Borough.STATEN_ISLAND,
}

# The first digit of BBL and Community Board.
BOROUGHS_BY_DIGIT = {
    "1": Borough.MANHATTAN,
    "2": Borough.BRONX,
    "3": Borough.BROOKLYN,
    "4": Borough.QUEENS,
    "5": Borough.STATEN_ISLAND,
}

# A borough digit followed by six zeros stands for an unknown building.
PLACEHOLDER_BUILDING_IDENTIFICATION_NUMBER = re.compile(r"^[1-5]000000$")

_NOT_APPLICABLE_PERMIT_NUMBER = "N/A"


def parse_nyc_health_rows(rows: Sequence[Mapping[str, str]]) -> ParseResult:
    """Parse every row whose DCID is present and unique.

    A row whose borough neither Borough, BBL, nor Community Board gives takes the most
    frequent borough of the rows with the same zip key in this export.
    """
    unique, skipped_rows, repeated_identifiers = unique_rows(
        rows, "DCID", ProviderSource.NYC_HEALTH
    )
    parsed = []
    for identifier, row in unique:
        warnings = RowWarnings(ProviderSource.NYC_HEALTH, identifier)
        parsed.append((_parse_row(identifier, row, warnings), warnings))

    boroughs_by_zip_key: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for record, _ in parsed:
        if record.borough and record.zip_key:
            boroughs_by_zip_key[record.zip_key][record.borough] += 1

    records: list[ProviderRecord] = []
    field_warnings: list[FieldWarning] = []
    for record, warnings in parsed:
        resolved = record
        if not record.borough:
            borough = _majority_borough(boroughs_by_zip_key.get(record.zip_key))
            if borough:
                resolved = dataclasses.replace(record, borough=borough)
            else:
                warnings.warn("Borough")
        records.append(resolved)
        field_warnings.extend(warnings.warnings)
    return ParseResult(
        records=tuple(records),
        skipped_rows=tuple(skipped_rows),
        field_warnings=tuple(field_warnings),
        repeated_identifiers=repeated_identifiers,
    )


def _parse_row(
    identifier: str, row: Mapping[str, str], warnings: RowWarnings
) -> ProviderRecord:
    facility_type = row["Facility Type"]
    kind = NYC_HEALTH_KINDS.get(facility_type.strip().upper())
    if kind is None:
        warnings.warn("Facility Type")
        kind = ProviderKind.OTHER
    program_type = row["Program Type"]
    if program_type.strip().upper() not in KNOWN_PROGRAM_TYPES:
        warnings.warn("Program Type")
    original_name = warnings.text("Program Name", row["Program Name"])
    street_address = warnings.text("Street Address", row["Street Address"])
    published_zip_code = warnings.text(
        "Zip Code", row["Zip Code"], PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH
    )
    return ProviderRecord(
        source=ProviderSource.NYC_HEALTH,
        source_identifier=identifier,
        kind=kind,
        original_name=original_name,
        display_name=normalization.display_name(original_name),
        name_key=normalization.name_key(original_name),
        street_address=street_address,
        published_zip_code=published_zip_code,
        zip_key=warnings.zip_key(published_zip_code),
        address_key=normalization.address_key(street_address),
        borough=_first_pass_borough(row, warnings),
        phone_number=warnings.text(
            "Phone Number", row["Phone Number"], PHONE_NUMBER_MAXIMUM_LENGTH
        ),
        total_capacity=warnings.count(
            "Children Allowed in Care", row["Children Allowed in Care"]
        ),
        nyc_health_permit_number=_permit_number(row["Permit Number"], warnings),
        nyc_health_facility_type=warnings.text("Facility Type", facility_type),
        nyc_health_program_type=warnings.text("Program Type", program_type),
        nyc_health_child_age_range=warnings.text(
            "Child Age Range", row["Child Age Range"]
        ),
        nyc_health_administers_medication=_administers_medication(
            row["Administer Medication"], warnings
        ),
        nyc_health_building_identification_number=_building_identification_number(
            row["BIN"], warnings
        ),
        nyc_health_borough_block_lot=warnings.text("BBL", row["BBL"]),
        nyc_health_community_board=warnings.text(
            "Community Board", row["Community Board"]
        ),
        nyc_health_council_district=warnings.text(
            "Council District", row["Council District"]
        ),
        nyc_health_census_tract=warnings.text("Census Tract", row["Census Tract"]),
        coordinates=warnings.coordinates(row["Latitude"], row["Longitude"]),
    )


def _first_pass_borough(row: Mapping[str, str], warnings: RowWarnings) -> str:
    published = row["Borough"].strip().upper()
    borough = BOROUGHS_BY_NAME.get(published)
    if borough is not None:
        return borough
    if published:
        warnings.warn("Borough")
    for column in ("BBL", "Community Board"):
        borough = BOROUGHS_BY_DIGIT.get(row[column].strip()[:1])
        if borough is not None:
            return borough
    return ""


def _majority_borough(boroughs: collections.Counter[str] | None) -> str:
    if not boroughs:
        return ""
    most_common = boroughs.most_common(2)
    if len(most_common) > 1 and most_common[0][1] == most_common[1][1]:
        return ""
    return most_common[0][0]


def _permit_number(value: str, warnings: RowWarnings) -> str:
    # Every school-based program publishes N/A, which is no permit number.
    if value.strip().upper() == _NOT_APPLICABLE_PERMIT_NUMBER:
        return ""
    return warnings.text("Permit Number", value)


def _administers_medication(value: str, warnings: RowWarnings) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "yes":
        return True
    if normalized == "no":
        return False
    warnings.warn("Administer Medication")
    return None


def _building_identification_number(value: str, warnings: RowWarnings) -> str:
    if PLACEHOLDER_BUILDING_IDENTIFICATION_NUMBER.match(value.strip()):
        return ""
    return warnings.text("BIN", value)
