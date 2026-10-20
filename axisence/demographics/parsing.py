"""Parse Census Data API responses into figures, labels, and tabulation-area codes.

Values are Decimal, never float. Sentinels, empty strings, and nulls are unknown.
Sentinels are recognized numerically, because the API spells them in several forms
(-666666666, -666666666.0, -666666666.00).
"""

import dataclasses
import re
from decimal import Decimal, InvalidOperation

from axisence.demographics.models import CensusTable
from axisence.ingestion.errors import IngestionError

SENTINEL_VALUES = frozenset(
    Decimal(value)
    for value in (
        "-999999999",
        "-888888888",
        "-666666666",
        "-555555555",
        "-333333333",
        "-222222222",
    )
)
TABULATION_AREA_UCGID = re.compile(r"^860Z200US(\d{5})$")
MAXIMUM_DECIMAL_PLACES = 4
MAXIMUM_MAGNITUDE = Decimal(10) ** 14

UCGID_COLUMN = "ucgid"
_VALUE_SUFFIXES = ("E", "M", "PE", "PM")
_LABEL_SUFFIXES = ("E", "PE")


@dataclasses.dataclass(frozen=True, slots=True)
class FigureValues:
    estimate: Decimal | None
    estimate_margin_of_error: Decimal | None
    percent: Decimal | None
    percent_margin_of_error: Decimal | None


@dataclasses.dataclass(frozen=True, slots=True)
class CensusFieldWarning:
    tabulation_area_code: str
    column: str


@dataclasses.dataclass(frozen=True, slots=True)
class ProfileTable:
    """One data profile table; figures are keyed by (area code, variable code)."""

    table: CensusTable
    rows_read: int
    codes: tuple[str, ...]
    figures: dict[tuple[str, str], FigureValues]
    field_warnings: tuple[CensusFieldWarning, ...]


def parse_census_value(value: object) -> tuple[Decimal | None, bool]:
    """Return the value and whether it was valid; unknown values are valid None."""
    if value is None or value == "":
        return None, True
    if not isinstance(value, str):
        return None, False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None, False
    if not parsed.is_finite():
        return None, False
    if parsed in SENTINEL_VALUES:
        return None, True
    exponent = parsed.as_tuple().exponent
    too_precise = isinstance(exponent, int) and -exponent > MAXIMUM_DECIMAL_PLACES
    if too_precise or abs(parsed) >= MAXIMUM_MAGNITUDE:
        return None, False
    return parsed, True


def parse_profile_table(table: CensusTable, payload: object) -> ProfileTable:
    """Read a group(<table>) response: every code's four values in every area."""
    format_error = IngestionError(
        f"The Census {table.value} response has an unexpected format."
    )
    header, rows = _header_and_rows(payload, format_error)
    estimate_column = re.compile(rf"^{table.value}_\d{{4}}E$")
    if not any(estimate_column.match(column) for column in header):
        raise format_error
    value_column = re.compile(rf"^({table.value}_\d{{4}})(E|M|PE|PM)$")
    column_indexes: dict[str, dict[str, int]] = {}
    for index, column in enumerate(header):
        match = value_column.match(column)
        if match:
            column_indexes.setdefault(match.group(1), {})[match.group(2)] = index
    codes = tuple(sorted(column_indexes))
    ucgid_index = header.index(UCGID_COLUMN)
    figures: dict[tuple[str, str], FigureValues] = {}
    field_warnings: list[CensusFieldWarning] = []
    for row in rows:
        area_code = _tabulation_area_code(
            row[ucgid_index],
            f"The Census {table.value} response holds an unrecognized tabulation area.",
        )
        for code in codes:
            values: list[Decimal | None] = []
            for suffix in _VALUE_SUFFIXES:
                column_index = column_indexes[code].get(suffix)
                if column_index is None:
                    values.append(None)
                    continue
                value, is_valid = parse_census_value(row[column_index])
                if not is_valid:
                    field_warnings.append(
                        CensusFieldWarning(area_code, header[column_index])
                    )
                values.append(value)
            figures[(area_code, code)] = FigureValues(*values)
    return ProfileTable(
        table=table,
        rows_read=len(rows),
        codes=codes,
        figures=figures,
        field_warnings=tuple(field_warnings),
    )


def parse_variable_labels(
    table: CensusTable, payload: object
) -> dict[str, tuple[str, str]]:
    """Map each code to its estimate label and percent label, "" when absent."""
    variables = payload.get("variables") if isinstance(payload, dict) else None
    if not isinstance(variables, dict):
        raise IngestionError(
            f"The Census {table.value} variable labels have an unexpected format."
        )
    label_name = re.compile(rf"^({table.value}_\d{{4}})(E|PE)$")
    labels: dict[str, dict[str, str]] = {}
    for name, description in variables.items():
        match = label_name.match(name) if isinstance(name, str) else None
        if match is None:
            continue
        label = description.get("label") if isinstance(description, dict) else None
        labels.setdefault(match.group(1), {})[match.group(2)] = (
            label if isinstance(label, str) else ""
        )
    return {
        code: (suffix_labels.get("E", ""), suffix_labels.get("PE", ""))
        for code, suffix_labels in labels.items()
    }


def parse_tabulation_area_codes(payload: object) -> list[str]:
    """Return the sorted unique tabulation-area codes of a list response."""
    header, rows = _header_and_rows(
        payload,
        IngestionError("The Census tabulation area list has an unexpected format."),
    )
    ucgid_index = header.index(UCGID_COLUMN)
    return sorted(
        {
            _tabulation_area_code(
                row[ucgid_index],
                "The Census tabulation area list holds an unrecognized tabulation "
                "area.",
            )
            for row in rows
        }
    )


def _header_and_rows(
    payload: object, format_error: IngestionError
) -> tuple[list[str], list[list[object]]]:
    # A response is a JSON array of arrays: a header row, then one row per area.
    if not (
        isinstance(payload, list)
        and len(payload) >= 2
        and all(isinstance(row, list) for row in payload)
    ):
        raise format_error
    header, *rows = payload
    if not all(isinstance(column, str) for column in header):
        raise format_error
    if UCGID_COLUMN not in header:
        raise format_error
    if any(len(row) != len(header) for row in rows):
        raise format_error
    return header, rows


def _tabulation_area_code(ucgid: object, message: str) -> str:
    match = TABULATION_AREA_UCGID.match(ucgid) if isinstance(ucgid, str) else None
    if match is None:
        raise IngestionError(message)
    return match.group(1)
