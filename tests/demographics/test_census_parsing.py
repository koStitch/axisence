"""Tests for parsing Census Data API responses, against recorded fixtures."""

import json
from decimal import Decimal

import pytest

from axisence.demographics.models import CensusTable
from axisence.demographics.parsing import (
    CensusFieldWarning,
    FigureValues,
    parse_census_value,
    parse_profile_table,
    parse_tabulation_area_codes,
    parse_variable_labels,
)
from axisence.ingestion.errors import IngestionError
from tests.fixture_files import fixture_bytes


def _fixture(name):
    return json.loads(fixture_bytes(f"census/{name}"))


@pytest.mark.parametrize(
    "value",
    [
        "-999999999",
        "-888888888",
        "-666666666",
        "-555555555",
        "-333333333",
        "-222222222",
        "-666666666.0",
        "-222222222.00",
        "-999999999.0",
    ],
)
def test_every_sentinel_spelling_is_unknown(value):
    assert parse_census_value(value) == (None, True)


def test_values_are_decimals_not_floats():
    for text in ("72", "2.33", "0.0", "2000001"):
        value, is_valid = parse_census_value(text)
        assert is_valid
        assert type(value) is Decimal
        assert value == Decimal(text)


def test_empty_and_null_values_are_unknown():
    assert parse_census_value(None) == (None, True)
    assert parse_census_value("") == (None, True)


def test_text_and_too_precise_values_are_field_warnings():
    for value in ("(X)", "N", "NaN", "1.23456", "100000000000000", 72):
        assert parse_census_value(value) == (None, False)
    payload = [
        ["DP05_0001E", "DP05_0001M", "ucgid"],
        ["(X)", "43", "860Z200US06390"],
    ]

    table = parse_profile_table(CensusTable.DP05, payload)

    assert table.field_warnings == (CensusFieldWarning("06390", "DP05_0001E"),)
    assert table.figures[("06390", "DP05_0001")].estimate is None
    assert table.figures[("06390", "DP05_0001")].estimate_margin_of_error == 43


def test_tabulation_area_code_comes_from_ucgid_and_keeps_its_leading_zero():
    table = parse_profile_table(
        CensusTable.DP05, _fixture("acs5-profile-2024-DP05.json")
    )
    codes = parse_tabulation_area_codes(
        _fixture("acs5-profile-2024-new-york-tabulation-areas.json")
    )

    assert ("06390", "DP05_0001") in table.figures
    assert codes == ["06390", "11201", "11217"]


def test_profile_table_reads_codes_and_four_values_per_code():
    table = parse_profile_table(
        CensusTable.DP05, _fixture("acs5-profile-2024-DP05.json")
    )

    assert table.table == CensusTable.DP05
    assert table.rows_read == 3
    assert table.codes == ("DP05_0001", "DP05_0005", "DP05_0032")
    assert len(table.figures) == 9
    assert table.field_warnings == ()
    assert table.figures[("06390", "DP05_0001")] == FigureValues(
        Decimal(72), Decimal(43), Decimal(72), None
    )
    assert table.figures[("06390", "DP05_0005")] == FigureValues(
        Decimal(0), Decimal(14), Decimal("0.0"), Decimal("40.0")
    )
    assert table.figures[("06390", "DP05_0032")] == FigureValues(None, None, None, None)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        [["DP05_0001E", "ucgid"]],
        [["DP05_0001E", "NAME"], ["72", "ZCTA5 06390"]],
        [["NAME", "ucgid"], ["ZCTA5 06390", "860Z200US06390"]],
        [["DP05_0001E", "ucgid"], ["72"]],
        [["DP05_0001E", "ucgid"], ["72", "0400000US36"]],
    ],
    ids=[
        "object",
        "empty",
        "header only",
        "no ucgid",
        "no estimate",
        "short row",
        "unrecognized area",
    ],
)
def test_unexpected_response_format_is_a_structural_problem(payload):
    with pytest.raises(IngestionError, match="Census DP05 response"):
        parse_profile_table(CensusTable.DP05, payload)


def test_variable_labels_map_estimate_and_percent_labels():
    labels = parse_variable_labels(
        CensusTable.DP05, _fixture("acs5-profile-2024-DP05-variables.json")
    )

    assert set(labels) == {"DP05_0001", "DP05_0005", "DP05_0032"}
    assert labels["DP05_0005"] == (
        "Estimate!!SEX AND AGE!!Total population!!Under 5 years",
        "Percent!!SEX AND AGE!!Total population!!Under 5 years",
    )
    with pytest.raises(IngestionError, match="unexpected format"):
        parse_variable_labels(CensusTable.DP05, {"variables": []})
