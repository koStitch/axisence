"""Tests for the Census figures a report shows and their checks."""

import json
from decimal import Decimal

import pytest

from axisence.demographics.models import CensusVariable
from axisence.reports.demographics import load_demographics
from axisence.reports.figure_lists import FIGURE_LISTS
from axisence.reports.formatting import EN_DASH, PLUS_MINUS
from tests.fixture_files import fixture_bytes
from tests.reports.factories import make_area, make_census_2024
from tests.research.factories import BOROUGH_HALL

pytestmark = pytest.mark.django_db


def _rows(section):
    return {row.label: row for group in section.groups for row in group.rows}


def _label(code):
    definition = next(
        definition for definition in FIGURE_LISTS[2024] if definition.code == code
    )
    return definition.report_label.format(vintage=2024)


def test_the_figure_list_matches_the_published_vintage_2024_labels():
    published = json.loads(
        fixture_bytes("census/acs5-profile-2024-report-figure-variables.json")
    )["variables"]

    assert {
        definition.code: definition.expected_estimate_label
        for definition in FIGURE_LISTS[2024]
    } == {
        definition.code: published[f"{definition.code}E"]["label"]
        for definition in FIGURE_LISTS[2024]
    }


def test_the_newest_vintage_with_a_figure_list_is_used():
    make_census_2024(make_area("11201", BOROUGH_HALL))
    CensusVariable.objects.create(
        vintage=2025,
        table="DP05",
        code="DP05_0005",
        estimate_label="Estimate!!A renumbered synthetic label",
    )

    loaded = load_demographics("11201")

    assert loaded.vintage == 2024
    assert loaded.section.groups


FIGURES: tuple[tuple[str, dict[str, Decimal | None], str, str], ...] = (
    ("DP05_0005", {}, "estimate", f"3,036 {PLUS_MINUS} 526"),
    ("DP05_0005", {}, "percent", f"7.0% {PLUS_MINUS} 1.1 of total population"),
    (
        "DP03_0015",
        {},
        "percent",
        f"87.8% {PLUS_MINUS} 4.9 of own children under 6 years",
    ),
    ("DP03_0062", {}, "estimate", f"$162,863 {PLUS_MINUS} $12,941"),
    ("DP03_0121", {}, "percent", f"1.5% {PLUS_MINUS} 1.9"),
    ("DP03_0121", {}, "estimate", ""),
    ("DP02_0054", {"estimate": None}, "estimate", "Not published"),
    ("DP02_0014", {"percent": None}, "percent", "Not published"),
    (
        "DP02_0014",
        {"percent_margin_of_error": None},
        "percent",
        "25.0% of households",
    ),
    ("DP02_0001", {"estimate_margin_of_error": None}, "estimate", "19,800"),
    ("DP05_0001", {}, "percent", ""),
)


@pytest.mark.parametrize(
    ("code", "values", "column", "expected"),
    FIGURES,
    ids=[
        f"{code} {column} {index}" for index, (code, _, column, _) in enumerate(FIGURES)
    ],
)
def test_figures_show_their_values_and_margins(code, values, column, expected):
    make_census_2024(make_area("11201", BOROUGH_HALL), values={code: values})

    row = _rows(load_demographics("11201").section)[_label(code)]

    assert getattr(row, column) == expected


def test_coded_medians():
    make_census_2024(
        make_area("11201", BOROUGH_HALL),
        values={
            "DP03_0062": {
                "estimate": Decimal(250001),
                "estimate_margin_of_error": Decimal(100),
            },
            "DP04_0134": {"estimate": Decimal(3501), "estimate_margin_of_error": None},
            "DP03_0086": {
                "estimate": Decimal(2499),
                "estimate_margin_of_error": Decimal(100),
            },
        },
    )

    rows = _rows(load_demographics("11201").section)

    assert rows[_label("DP03_0062")].estimate == "$250,000 or more"
    assert rows[_label("DP04_0134")].estimate == "$3,500 or more"
    assert rows[_label("DP03_0086")].estimate == "$2,500 or less"


def test_the_heading_caveat_and_missing_figures():
    assert load_demographics("11201").section.message == "No Census figures are loaded."

    make_census_2024(make_area("11201", BOROUGH_HALL))
    inside = load_demographics("11201")
    outside = load_demographics(None)

    assert inside.section.heading == (
        "ZIP Code Tabulation Area 11201: American Community Survey 5-year estimates, "
        f"2020{EN_DASH}2024, tables DP02 to DP05"
    )
    assert inside.section.caveat == (
        "ZIP Code Tabulation Area 11201 is the Census Bureau's approximation of the "
        "zip code area; it is not the zip code itself."
    )
    assert [group.heading for group in inside.section.groups] == [
        "Children",
        "Working parents",
        "Households and schooling",
        "Income and poverty",
        "Housing",
    ]
    assert _label("DP03_0062") == "Median household income (2024 dollars)"
    assert outside.vintage is None
    assert outside.section.message == (
        "The target location lies outside every ZIP Code Tabulation Area, so no "
        "Census figures are shown."
    )
    assert outside.section.groups == ()
