"""Tests for parsing the NYC Health export, against fixture rows."""

import pytest

from axisence.providers.models import Borough, ProviderKind, ProviderSource
from axisence.providers.nyc_health import (
    NYC_HEALTH_REQUIRED_HEADERS,
    parse_nyc_health_rows,
)
from axisence.providers.parsing import FieldWarning, read_export
from tests.fixture_files import FIXTURES_DIRECTORY

FIXTURE = FIXTURES_DIRECTORY / "providers" / "nyc-health-export.csv"


@pytest.fixture
def rows():
    return read_export(FIXTURE, NYC_HEALTH_REQUIRED_HEADERS)


def _row(rows, identifier, **changes):
    row = dict(next(row for row in rows if row["DCID"] == identifier))
    row.update(changes)
    return row


def _parse(rows):
    result = parse_nyc_health_rows(rows)
    records = {record.source_identifier: record for record in result.records}
    return records, result.field_warnings


def _warning(identifier, field):
    return FieldWarning(ProviderSource.NYC_HEALTH, identifier, field)


def test_school_based_program_has_unknown_capacity_blank_permit_and_school_based_kind(
    rows,
):
    records, warnings = _parse(rows)
    record = records["DC1051"]

    assert record.kind == ProviderKind.SCHOOL_BASED_CHILD_CARE
    assert record.nyc_health_facility_type == "SBCC"
    assert record.total_capacity is None
    assert record.nyc_health_permit_number == ""
    assert not [
        warning for warning in warnings if warning.source_identifier == "DC1051"
    ]


def test_unknown_program_type_is_kept_with_a_field_warning(rows):
    records, warnings = _parse(rows)

    assert records["DC45759"].nyc_health_program_type == "P/S Night Care"
    assert _warning("DC45759", "Program Type") in warnings


def test_administer_medication_becomes_a_boolean(rows):
    records, _ = _parse(rows)
    maybe_records, maybe_warnings = _parse(
        [_row(rows, "DC10592", **{"Administer Medication": "Maybe"})]
    )

    assert records["DC112"].nyc_health_administers_medication is True
    assert records["DC10592"].nyc_health_administers_medication is False
    assert maybe_records["DC10592"].nyc_health_administers_medication is None
    assert maybe_warnings == (_warning("DC10592", "Administer Medication"),)


def test_values_too_long_for_their_columns_are_unknown_with_field_warnings(rows):
    records, warnings = _parse(
        [
            _row(
                rows,
                "DC10592",
                **{
                    "Zip Code": "1" * 21,
                    "Phone Number": "5" * 31,
                    "Children Allowed in Care": "2147483648",
                },
            )
        ]
    )
    record = records["DC10592"]

    assert (record.published_zip_code, record.zip_key) == ("", "")
    assert record.phone_number == ""
    assert record.total_capacity is None
    assert set(warnings) == {
        _warning("DC10592", "Zip Code"),
        _warning("DC10592", "Phone Number"),
        _warning("DC10592", "Children Allowed in Care"),
    }


def test_placeholder_building_identification_number_is_blank(rows):
    records, _ = _parse(rows)

    assert records["DC14269"].nyc_health_building_identification_number == ""
    assert records["DC112"].nyc_health_building_identification_number == "3179326"


def test_borough_digit_only_bbl_is_kept(rows):
    records, _ = _parse(rows)

    assert records["DC20455"].nyc_health_borough_block_lot == "3"
    assert records["DC20455"].borough == Borough.BROOKLYN


def test_blank_borough_comes_from_bbl(rows):
    records, warnings = _parse(rows)

    assert records["DC2794"].borough == Borough.MANHATTAN
    assert _warning("DC2794", "Borough") not in warnings


def test_borough_falls_back_to_community_board_then_zip_majority_then_unknown(rows):
    unlocated = {"Borough": "", "BBL": "", "Community Board": ""}
    synthetic_rows = [
        _row(
            rows, "DC10592", DCID="DC9000001", **{**unlocated, "Community Board": "412"}
        ),
        _row(rows, "DC10592", DCID="DC9000002", **{"Zip Code": "11217"}),
        _row(rows, "DC10592", DCID="DC9000003", **{"Zip Code": "11217"}),
        _row(rows, "DC14385", DCID="DC9000004", **{"Zip Code": "11217"}),
        _row(rows, "DC10592", DCID="DC9000005", **{**unlocated, "Zip Code": "11217"}),
        _row(rows, "DC14269", DCID="DC9000006", **{"Zip Code": "10001"}),
        _row(rows, "DC2489", DCID="DC9000007", **{"Zip Code": "10001"}),
        _row(rows, "DC10592", DCID="DC9000008", **{**unlocated, "Zip Code": "10001"}),
        _row(rows, "DC10592", DCID="DC9000009", **{**unlocated, "Zip Code": "10301"}),
    ]

    records, warnings = _parse(synthetic_rows)

    assert records["DC9000001"].borough == Borough.QUEENS
    assert records["DC9000005"].borough == Borough.BROOKLYN
    assert records["DC9000008"].borough == ""
    assert records["DC9000009"].borough == ""
    assert {
        warning.source_identifier for warning in warnings if warning.field == "Borough"
    } == {"DC9000008", "DC9000009"}


def test_hyphenated_house_number_with_spaces_keys_without_spaces(rows):
    records, _ = _parse(rows)

    assert records["DC37474"].street_address == "1766 - 1768 2ND AVE"
    assert records["DC37474"].address_key == "1766-1768 2 AVE"


def test_rows_without_coordinates_have_no_coordinates(rows):
    records, warnings = _parse(rows)

    assert records["DC21774"].coordinates is None
    assert _warning("DC21774", "Latitude and Longitude") not in warnings
    assert records["DC10592"].coordinates is not None
