"""Tests for parsing the NY State export, against redacted fixture rows."""

import csv
from datetime import date
from decimal import Decimal

import pytest

from axisence.ingestion.errors import IngestionError
from axisence.providers.models import Borough, ProviderKind, ProviderSource
from axisence.providers.ny_state import NY_STATE_REQUIRED_HEADERS, parse_ny_state_rows
from axisence.providers.parsing import Coordinates, FieldWarning, read_export
from tests.fixture_files import FIXTURES_DIRECTORY

FIXTURE = FIXTURES_DIRECTORY / "providers" / "ny-state-export.csv"


@pytest.fixture
def rows():
    return read_export(FIXTURE, NY_STATE_REQUIRED_HEADERS)


def _row(rows, identifier, **changes):
    row = dict(next(row for row in rows if row["Facility ID"] == identifier))
    row.update(changes)
    return row


def _parse_one(row):
    result = parse_ny_state_rows([row])
    (record,) = result.records
    return record, result.field_warnings


def _records(rows):
    return {
        record.source_identifier: record for record in parse_ny_state_rows(rows).records
    }


def _warning(identifier, field):
    return FieldWarning(ProviderSource.NY_STATE, identifier, field)


def test_every_fixture_row_becomes_a_record(rows):
    result = parse_ny_state_rows(rows)

    assert [record.source_identifier for record in result.records] == [
        row["Facility ID"] for row in rows
    ]
    assert len(result.records) == 21
    assert result.skipped_rows == ()
    assert result.repeated_identifiers == frozenset()


def test_kind_comes_from_program_type_and_keeps_the_published_code(rows):
    records = _records(rows)

    expected = {
        "40693": ("DCC", ProviderKind.DAY_CARE_CENTER),
        "914823": ("SDCC", ProviderKind.SMALL_DAY_CARE_CENTER),
        "920007": ("GFDC", ProviderKind.GROUP_FAMILY_DAY_CARE),
        "656097": ("FDC", ProviderKind.FAMILY_DAY_CARE),
        "45071": ("SACC", ProviderKind.SCHOOL_AGE_CHILD_CARE),
    }
    for identifier, (program_type, kind) in expected.items():
        assert records[identifier].ny_state_program_type == program_type
        assert records[identifier].kind == kind


def test_capacity_thousands_separator_is_removed(rows):
    record = _records(rows)["45071"]

    assert record.ny_state_school_age_capacity == 1100
    assert record.total_capacity == 1100


def test_zero_capacity_stays_zero_and_blank_capacity_is_unknown(rows):
    record = _records(rows)["40693"]
    blank_record, warnings = _parse_one(_row(rows, "40693", **{"Total Capacity": ""}))

    assert record.ny_state_school_age_capacity == 0
    assert blank_record.total_capacity is None
    assert warnings == ()


def test_dates_are_parsed_as_month_day_year(rows):
    record = _records(rows)["40693"]

    assert record.ny_state_facility_opened_on == date(1991, 8, 22)
    assert record.ny_state_license_issued_on == date(2023, 8, 1)
    assert record.ny_state_license_expires_on == date(2027, 7, 31)


def test_blank_status_is_stored_blank(rows):
    assert _records(rows)["842099"].ny_state_facility_status == ""


def test_nine_digit_zip_keys_to_five_digits(rows):
    record = _records(rows)["448651"]

    assert record.published_zip_code == "122071413"
    assert record.zip_key == "12207"


def test_invalid_zip_is_unknown_with_a_field_warning(rows):
    result = parse_ny_state_rows(rows)
    record = next(
        record for record in result.records if record.source_identifier == "943664"
    )

    assert record.published_zip_code == "7726"
    assert record.zip_key == ""
    assert _warning("943664", "Zip Code") in result.field_warnings


def test_omitted_address_has_no_street_key_or_coordinates_even_when_published(rows):
    published = _row(
        rows,
        "762834",
        **{
            "Street Number": "1",
            "Street Name": "SYNTHETIC STREET",
            "Floor": "1",
            "Latitude": "40.650000",
            "Longitude": "-73.950000",
        },
    )

    for row in (_row(rows, "762834"), published):
        record, warnings = _parse_one(row)
        assert record.ny_state_is_address_omitted is True
        assert record.street_address == ""
        assert record.address_key == ""
        assert record.ny_state_street_number == ""
        assert record.ny_state_street_name == ""
        assert record.ny_state_floor == ""
        assert record.coordinates is None
        assert record.ny_state_city == "Bronx"
        assert record.zip_key == "10451"
        assert warnings == ()


def test_omitted_phone_is_blank_even_when_published(rows):
    published = _row(
        rows, "656097", **{"Phone Number": "(555)555-0199", "Phone Extension": "12"}
    )

    for row in (_row(rows, "656097"), published):
        record, _ = _parse_one(row)
        assert record.ny_state_is_phone_omitted is True
        assert record.phone_number == ""
        assert record.ny_state_phone_extension == ""


def test_new_york_city_borough_comes_from_county(rows):
    records = _records(rows)
    kings, warnings = _parse_one(_row(rows, "920007", County="Kings"))

    assert records["920007"].borough == Borough.BROOKLYN
    assert records["656097"].borough == Borough.BRONX
    assert records["486296"].borough == Borough.QUEENS
    assert records["924163"].borough == Borough.MANHATTAN
    assert records["943664"].borough == Borough.STATEN_ISLAND
    assert records["40693"].borough == ""
    assert kings.borough == Borough.BROOKLYN
    assert warnings == ()


def test_unknown_program_type_becomes_other_with_a_field_warning(rows):
    record, warnings = _parse_one(_row(rows, "40693", **{"Program Type": "XYZ"}))

    assert record.kind == ProviderKind.OTHER
    assert record.ny_state_program_type == "XYZ"
    assert warnings == (_warning("40693", "Program Type"),)


def test_coordinates_outside_the_state_are_absent_with_a_field_warning(rows):
    record, warnings = _parse_one(
        _row(rows, "40693", Latitude="34.052235", Longitude="-118.243683")
    )
    inside, _ = _parse_one(_row(rows, "40693"))

    assert record.coordinates is None
    assert warnings == (_warning("40693", "Latitude and Longitude"),)
    assert inside.coordinates == Coordinates(
        longitude=Decimal("-73.561601"), latitude=Decimal("42.734255")
    )


def test_unparseable_capacity_and_date_are_unknown_with_field_warnings(rows):
    record, warnings = _parse_one(
        _row(
            rows,
            "40693",
            **{
                "Total Capacity": "about 20",
                "Facility Opened Date": "1991-08-22",
                "License Issue Date": "02/30/2023",
            },
        )
    )

    assert record.total_capacity is None
    assert record.ny_state_facility_opened_on is None
    assert record.ny_state_license_issued_on is None
    assert set(warnings) == {
        _warning("40693", "Total Capacity"),
        _warning("40693", "Facility Opened Date"),
        _warning("40693", "License Issue Date"),
    }


def test_values_too_long_for_their_columns_are_unknown_with_field_warnings(rows):
    record, warnings = _parse_one(
        _row(
            rows,
            "40693",
            **{
                "Zip Code": "1" * 21,
                "Phone Number": "5" * 31,
                "Program Type": "X" * 21,
                "Region Code": "R" * 21,
                "Total Capacity": "2147483648",
                "Infant Capacity": "2147483647",
            },
        )
    )

    assert (record.published_zip_code, record.zip_key) == ("", "")
    assert record.phone_number == ""
    assert (record.ny_state_program_type, record.kind) == ("", ProviderKind.OTHER)
    assert record.ny_state_region_code == ""
    assert record.total_capacity is None
    assert record.ny_state_infant_capacity == 2147483647
    # One warning per field, though the program type is both unknown and too long.
    assert len(warnings) == 5
    assert set(warnings) == {
        _warning("40693", "Zip Code"),
        _warning("40693", "Phone Number"),
        _warning("40693", "Program Type"),
        _warning("40693", "Region Code"),
        _warning("40693", "Total Capacity"),
    }


def test_blank_too_long_and_repeated_identifiers_skip_every_such_row(rows):
    too_long = "9" * 21
    synthetic_rows = [
        _row(rows, "40693", **{"Facility ID": " "}),
        _row(rows, "914823", **{"Facility ID": "777"}),
        _row(rows, "448651", **{"Facility ID": "777"}),
        _row(rows, "943664", **{"Facility ID": too_long}),
        _row(rows, "918154", **{"Facility ID": too_long}),
        _row(rows, "45071"),
    ]

    result = parse_ny_state_rows(synthetic_rows)

    assert [record.source_identifier for record in result.records] == ["45071"]
    assert [
        (skipped.row_number, skipped.reason) for skipped in result.skipped_rows
    ] == [
        (1, "blank source identifier"),
        (2, "repeated source identifier"),
        (3, "repeated source identifier"),
        (4, "source identifier too long"),
        (5, "source identifier too long"),
    ]
    assert result.repeated_identifiers == frozenset({"777"})


def _write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def test_missing_expected_header_is_a_structural_problem(rows, tmp_path):
    header = [name for name in NY_STATE_REQUIRED_HEADERS if name != "Zip Code"]
    path = tmp_path / "export.csv"
    _write_csv(path, header, [[rows[0][name] for name in header]])

    with pytest.raises(IngestionError, match="missing expected columns: Zip Code"):
        read_export(path, NY_STATE_REQUIRED_HEADERS)


def test_export_without_data_rows_is_a_structural_problem(tmp_path):
    path = tmp_path / "export.csv"
    _write_csv(path, NY_STATE_REQUIRED_HEADERS, [])

    with pytest.raises(IngestionError, match="no data rows"):
        read_export(path, NY_STATE_REQUIRED_HEADERS)


def test_extra_columns_are_ignored(rows, tmp_path):
    header = [*NY_STATE_REQUIRED_HEADERS, "Counties 2"]
    path = tmp_path / "export.csv"
    # The extra header and a value beyond the last header are both ignored.
    _write_csv(path, header, [[*[rows[0][name] for name in header[:-1]], "7", "x"]])

    exported_rows = read_export(path, NY_STATE_REQUIRED_HEADERS)

    (record,) = parse_ny_state_rows(exported_rows).records
    assert record == _records(rows)["40693"]
