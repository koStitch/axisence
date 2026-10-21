"""The local demographics section: the newest usable vintage, the label check, and the
figures of the target's tabulation area.
"""

import dataclasses
from collections.abc import Sequence
from decimal import Decimal

from django.db.models import Max

from axisence.demographics.models import CensusFigure, CensusVariable
from axisence.reports.errors import ReportRefusedError
from axisence.reports.figure_lists import (
    FIGURE_LISTS,
    TEASER_FIGURE_CODES,
    FigureDefinition,
    FigureDisplay,
)
from axisence.reports.formatting import (
    EN_DASH,
    PLUS_MINUS,
    format_count,
    format_percent,
)

OUTSIDE_EVERY_AREA_MESSAGE = (
    "The target location lies outside every ZIP Code Tabulation Area, so no Census "
    "figures are shown."
)
TEASER_OUTSIDE_EVERY_AREA_MESSAGE = (
    "Your location lies outside every ZIP Code Tabulation Area, so no Census figures "
    "are shown."
)
NO_FIGURES_MESSAGE = "No Census figures are loaded."
NOT_PUBLISHED = "Not published"


@dataclasses.dataclass(frozen=True, slots=True)
class FigureRow:
    label: str
    estimate: str
    percent: str


@dataclasses.dataclass(frozen=True, slots=True)
class FigureGroup:
    heading: str
    rows: tuple[FigureRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class DemographicsSection:
    heading: str
    caveat: str
    # Set instead of the figures when none are shown.
    message: str
    groups: tuple[FigureGroup, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class LoadedDemographics:
    section: DemographicsSection
    # The vintage of the figures shown; None when none are shown.
    vintage: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class _FigureValues:
    estimate: Decimal | None = None
    estimate_margin_of_error: Decimal | None = None
    percent: Decimal | None = None
    percent_margin_of_error: Decimal | None = None


def load_demographics(area_code: str | None) -> LoadedDemographics:
    """Return the demographics section of a target in the area, in up to 3 queries.

    The figure list of the newest ingested vintage that has one is checked against the
    stored labels whatever the area, so a list that no longer matches refuses every
    report.
    """
    vintage = _newest_checked_vintage()
    if area_code is None:
        return LoadedDemographics(
            section=_message_section(OUTSIDE_EVERY_AREA_MESSAGE), vintage=None
        )
    if vintage is None:
        return LoadedDemographics(
            section=_message_section(NO_FIGURES_MESSAGE), vintage=None
        )
    rows_by_heading: dict[str, list[FigureRow]] = {}
    for definition, row in _figure_rows(area_code, vintage, FIGURE_LISTS[vintage]):
        rows_by_heading.setdefault(definition.heading, []).append(row)
    return LoadedDemographics(
        section=_figures_section(
            area_code,
            vintage,
            tuple(
                FigureGroup(heading=heading, rows=tuple(rows))
                for heading, rows in rows_by_heading.items()
            ),
        ),
        vintage=vintage,
    )


def load_teaser_demographics(area_code: str | None) -> LoadedDemographics:
    """Return a teaser's local demographics: its headline figures, in one group without
    a heading, in up to 3 queries.

    The vintage's whole figure list is checked against the stored labels, as for every
    report.
    """
    vintage = _newest_checked_vintage()
    if area_code is None:
        return LoadedDemographics(
            section=_message_section(TEASER_OUTSIDE_EVERY_AREA_MESSAGE), vintage=None
        )
    if vintage is None:
        return LoadedDemographics(
            section=_message_section(NO_FIGURES_MESSAGE), vintage=None
        )
    definitions_by_code = {
        definition.code: definition for definition in FIGURE_LISTS[vintage]
    }
    rows = tuple(
        row
        for _, row in _figure_rows(
            area_code,
            vintage,
            [definitions_by_code[code] for code in TEASER_FIGURE_CODES[vintage]],
        )
    )
    return LoadedDemographics(
        section=_figures_section(
            area_code, vintage, (FigureGroup(heading="", rows=rows),)
        ),
        vintage=vintage,
    )


def survey_description(vintage: int) -> str:
    """Return the survey, its years, and its tables, as reports cite them."""
    tables = sorted({definition.table for definition in FIGURE_LISTS[vintage]})
    return (
        f"American Community Survey 5-year estimates, {vintage - 4}{EN_DASH}{vintage}, "
        f"tables {tables[0]} to {tables[-1]}"
    )


def _newest_checked_vintage() -> int | None:
    """Return the newest ingested vintage that has a figure list, after checking its
    labels, or None.
    """
    vintage: int | None = CensusVariable.objects.filter(
        vintage__in=list(FIGURE_LISTS)
    ).aggregate(newest=Max("vintage"))["newest"]
    if vintage is not None:
        _check_labels(vintage)
    return vintage


def _figure_rows(
    area_code: str, vintage: int, definitions: Sequence[FigureDefinition]
) -> list[tuple[FigureDefinition, FigureRow]]:
    """Return each definition with its row for the area, in the definitions' order, in
    one query.
    """
    figures = {
        figure.variable.code: figure
        for figure in CensusFigure.objects.filter(
            tabulation_area__code=area_code,
            variable__vintage=vintage,
            variable__code__in=[definition.code for definition in definitions],
        ).select_related("variable")
    }
    rows = []
    for definition in definitions:
        figure = figures.get(definition.code)
        values = (
            _FigureValues()
            if figure is None
            else _FigureValues(
                estimate=figure.estimate,
                estimate_margin_of_error=figure.estimate_margin_of_error,
                percent=figure.percent,
                percent_margin_of_error=figure.percent_margin_of_error,
            )
        )
        rows.append((definition, _row(definition, values, vintage)))
    return rows


def _figures_section(
    area_code: str, vintage: int, groups: tuple[FigureGroup, ...]
) -> DemographicsSection:
    return DemographicsSection(
        heading=f"ZIP Code Tabulation Area {area_code}: {survey_description(vintage)}",
        caveat=(
            f"ZIP Code Tabulation Area {area_code} is the Census Bureau's "
            "approximation of the zip code area; it is not the zip code itself."
        ),
        message="",
        groups=groups,
    )


def _check_labels(vintage: int) -> None:
    definitions = FIGURE_LISTS[vintage]
    stored = dict(
        CensusVariable.objects.filter(
            vintage=vintage,
            code__in=[definition.code for definition in definitions],
        ).values_list("code", "estimate_label")
    )
    if any(
        stored.get(definition.code) != definition.expected_estimate_label
        for definition in definitions
    ):
        raise ReportRefusedError(
            f"The Census figure list for vintage {vintage} does not match the stored "
            "labels."
        )


def _message_section(message: str) -> DemographicsSection:
    return DemographicsSection(heading="", caveat="", message=message, groups=())


def _row(
    definition: FigureDefinition, values: _FigureValues, vintage: int
) -> FigureRow:
    label = definition.report_label.format(vintage=vintage)
    match definition.display:
        case FigureDisplay.COUNT:
            return FigureRow(label=label, estimate=_count_text(values), percent="")
        case FigureDisplay.COUNT_AND_PERCENT:
            return FigureRow(
                label=label,
                estimate=_count_text(values),
                percent=_percent_text(values, definition.percent_base),
            )
        case FigureDisplay.PERCENT:
            return FigureRow(
                label=label,
                estimate="",
                percent=_percent_text(values, definition.percent_base),
            )
        case FigureDisplay.DOLLARS:
            return FigureRow(
                label=label, estimate=_dollars_text(definition, values), percent=""
            )


def _count_text(values: _FigureValues) -> str:
    if values.estimate is None:
        return NOT_PUBLISHED
    text = format_count(values.estimate)
    if values.estimate_margin_of_error is not None:
        text += f" {PLUS_MINUS} {format_count(values.estimate_margin_of_error)}"
    return text


def _percent_text(values: _FigureValues, percent_base: str) -> str:
    if values.percent is None:
        return NOT_PUBLISHED
    text = format_percent(values.percent)
    if values.percent_margin_of_error is not None:
        text += f" {PLUS_MINUS} {values.percent_margin_of_error:.1f}"
    if percent_base:
        text += f" {percent_base}"
    return text


def _dollars_text(definition: FigureDefinition, values: _FigureValues) -> str:
    estimate = values.estimate
    if estimate is None:
        return NOT_PUBLISHED
    # A coded median has no usable margin of error.
    cap = definition.top_coding_cap
    if cap is not None and estimate == cap + 1:
        return f"${cap:,} or more"
    floor = definition.bottom_coding_floor
    if floor is not None and estimate == floor - 1:
        return f"${floor:,} or less"
    text = f"${estimate:,.0f}"
    if values.estimate_margin_of_error is not None:
        text += f" {PLUS_MINUS} ${values.estimate_margin_of_error:,.0f}"
    return text
