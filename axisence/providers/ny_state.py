"""Parse the NY State OCFS Child Care Regulated Programs export into records."""

from collections.abc import Mapping, Sequence

from axisence.ingestion.socrata import SocrataDataset
from axisence.providers import normalization
from axisence.providers.models import (
    NY_STATE_CODE_MAXIMUM_LENGTH,
    PHONE_NUMBER_MAXIMUM_LENGTH,
    PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH,
    Borough,
    ProviderKind,
    ProviderSource,
)
from axisence.providers.parsing import (
    MAXIMUM_TEXT_LENGTH,
    ParseResult,
    ProviderRecord,
    RowWarnings,
    unique_rows,
)

NY_STATE_DATASET = SocrataDataset(
    domain="data.ny.gov",
    identifier="cb42-qumz",
    raw_directory_name="ny-ocfs",
    file_stem="child-care-regulated-programs",
)

# Export order. Georeference only repeats Latitude and Longitude, so it is not read.
NY_STATE_REQUIRED_HEADERS = (
    "Facility ID",
    "Program Type",
    "Region Code",
    "County",
    "Facility Status",
    "Facility Name",
    "Facility Opened Date",
    "License Issue Date",
    "License Expiration Date",
    "Address Omitted",
    "Street Number",
    "Street Name",
    "Additional Address",
    "Floor",
    "Apartment",
    "City",
    "State",
    "Zip Code",
    "Phone Number Omitted",
    "Phone Number",
    "Phone Extension",
    "Provider Name",
    "School District Name",
    "Capacity Description",
    "Infant Capacity",
    "Toddler Capacity",
    "Preschool Capacity",
    "School Age Capacity",
    "Total Capacity",
    "Program Profile",
    "Latitude",
    "Longitude",
)

NY_STATE_KINDS = {
    "DCC": ProviderKind.DAY_CARE_CENTER,
    "SDCC": ProviderKind.SMALL_DAY_CARE_CENTER,
    "GFDC": ProviderKind.GROUP_FAMILY_DAY_CARE,
    "FDC": ProviderKind.FAMILY_DAY_CARE,
    "SACC": ProviderKind.SCHOOL_AGE_CHILD_CARE,
}

NEW_YORK_CITY_REGION_CODE = "NYCDOH"

# New York City rows name the borough; the county names are accepted too, so a
# publisher switch to them keeps every borough.
BOROUGHS_BY_COUNTY = {
    "BRONX": Borough.BRONX,
    "BROOKLYN": Borough.BROOKLYN,
    "KINGS": Borough.BROOKLYN,
    "MANHATTAN": Borough.MANHATTAN,
    "NEW YORK": Borough.MANHATTAN,
    "QUEENS": Borough.QUEENS,
    "STATEN ISLAND": Borough.STATEN_ISLAND,
    "RICHMOND": Borough.STATEN_ISLAND,
}

_PROGRAM_PROFILE_SCHEMES = ("https://", "http://")


def parse_ny_state_rows(rows: Sequence[Mapping[str, str]]) -> ParseResult:
    """Parse every row whose Facility ID is present and unique."""
    unique, skipped_rows, repeated_identifiers = unique_rows(
        rows, "Facility ID", ProviderSource.NY_STATE
    )
    records: list[ProviderRecord] = []
    field_warnings = []
    for identifier, row in unique:
        warnings = RowWarnings(ProviderSource.NY_STATE, identifier)
        records.append(_parse_row(identifier, row, warnings))
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
    program_type = row["Program Type"]
    kind = NY_STATE_KINDS.get(program_type.strip().upper())
    if kind is None:
        warnings.warn("Program Type")
        kind = ProviderKind.OTHER
    is_address_omitted = row["Address Omitted"].strip().upper() == "Y"
    is_phone_omitted = row["Phone Number Omitted"].strip().upper() == "Y"

    def address_text(column: str) -> str:
        # An omitted address stays omitted, even when the export publishes it.
        return "" if is_address_omitted else warnings.text(column, row[column])

    def phone_text(column: str, maximum_length: int = MAXIMUM_TEXT_LENGTH) -> str:
        if is_phone_omitted:
            return ""
        return warnings.text(column, row[column], maximum_length)

    original_name = warnings.text("Facility Name", row["Facility Name"])
    street_number = address_text("Street Number")
    street_name = address_text("Street Name")
    street_address = " ".join(
        part for part in (street_number.strip(), street_name.strip()) if part
    )
    published_zip_code = warnings.text(
        "Zip Code", row["Zip Code"], PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH
    )
    return ProviderRecord(
        source=ProviderSource.NY_STATE,
        source_identifier=identifier,
        kind=kind,
        original_name=original_name,
        display_name=normalization.display_name(original_name),
        name_key=normalization.name_key(original_name),
        street_address=street_address,
        published_zip_code=published_zip_code,
        zip_key=warnings.zip_key(published_zip_code),
        address_key=normalization.address_key(street_address),
        borough=_borough(row, warnings),
        phone_number=phone_text("Phone Number", PHONE_NUMBER_MAXIMUM_LENGTH),
        total_capacity=warnings.count("Total Capacity", row["Total Capacity"]),
        ny_state_program_type=warnings.text(
            "Program Type", program_type, NY_STATE_CODE_MAXIMUM_LENGTH
        ),
        ny_state_region_code=warnings.text(
            "Region Code", row["Region Code"], NY_STATE_CODE_MAXIMUM_LENGTH
        ),
        ny_state_county=warnings.text("County", row["County"]),
        ny_state_facility_status=warnings.text(
            "Facility Status", row["Facility Status"]
        ),
        ny_state_facility_opened_on=warnings.us_date(
            "Facility Opened Date", row["Facility Opened Date"]
        ),
        ny_state_license_issued_on=warnings.us_date(
            "License Issue Date", row["License Issue Date"]
        ),
        ny_state_license_expires_on=warnings.us_date(
            "License Expiration Date", row["License Expiration Date"]
        ),
        ny_state_is_address_omitted=is_address_omitted,
        ny_state_is_phone_omitted=is_phone_omitted,
        ny_state_street_number=street_number,
        ny_state_street_name=street_name,
        ny_state_additional_address=address_text("Additional Address"),
        ny_state_floor=address_text("Floor"),
        ny_state_apartment=address_text("Apartment"),
        ny_state_city=warnings.text("City", row["City"]),
        ny_state_state=warnings.text("State", row["State"]),
        ny_state_phone_extension=phone_text("Phone Extension"),
        ny_state_licensee_name=warnings.text("Provider Name", row["Provider Name"]),
        ny_state_school_district_name=warnings.text(
            "School District Name", row["School District Name"]
        ),
        ny_state_capacity_description=warnings.text(
            "Capacity Description", row["Capacity Description"]
        ),
        ny_state_infant_capacity=warnings.count(
            "Infant Capacity", row["Infant Capacity"]
        ),
        ny_state_toddler_capacity=warnings.count(
            "Toddler Capacity", row["Toddler Capacity"]
        ),
        ny_state_preschool_capacity=warnings.count(
            "Preschool Capacity", row["Preschool Capacity"]
        ),
        ny_state_school_age_capacity=warnings.count(
            "School Age Capacity", row["School Age Capacity"]
        ),
        ny_state_program_profile_url=_program_profile_url(
            row["Program Profile"], warnings
        ),
        coordinates=None
        if is_address_omitted
        else warnings.coordinates(row["Latitude"], row["Longitude"]),
    )


def _borough(row: Mapping[str, str], warnings: RowWarnings) -> str:
    if row["Region Code"].strip().upper() != NEW_YORK_CITY_REGION_CODE:
        return ""
    borough = BOROUGHS_BY_COUNTY.get(row["County"].strip().upper())
    if borough is None:
        warnings.warn("County")
        return ""
    return borough


def _program_profile_url(value: str, warnings: RowWarnings) -> str:
    stripped = value.strip()
    if stripped == "":
        return ""
    if not stripped.startswith(_PROGRAM_PROFILE_SCHEMES):
        warnings.warn("Program Profile")
        return ""
    return warnings.text("Program Profile", stripped)
