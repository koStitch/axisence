"""Store parsed Census figures and boundaries, updating each vintage in place.

Only new and changed rows are written, so a re-run of an unchanged vintage writes no
figure.
"""

import dataclasses
from collections.abc import Collection, Mapping, Sequence

from django.contrib.gis.geos import MultiPolygon

from axisence.demographics.models import (
    CensusFigure,
    CensusTable,
    CensusVariable,
    TabulationArea,
)
from axisence.demographics.parsing import FigureValues, ProfileTable

FIGURE_BATCH_SIZE = 5000
FIGURE_READ_CHUNK_SIZE = 10_000
BOUNDARY_BATCH_SIZE = 100

FIGURE_VALUE_FIELD_NAMES = (
    "estimate",
    "estimate_margin_of_error",
    "percent",
    "percent_margin_of_error",
)


@dataclasses.dataclass(slots=True)
class LoadCounts:
    created: int
    updated: int
    unchanged: int


def ensure_tabulation_areas(codes: Collection[str]) -> dict[str, int]:
    """Create the missing areas, without boundaries, and return every code's id."""
    TabulationArea.objects.bulk_create(
        [TabulationArea(code=code) for code in codes], ignore_conflicts=True
    )
    return dict(TabulationArea.objects.filter(code__in=codes).values_list("code", "id"))


def store_census_vintage(
    vintage: int,
    tables: Sequence[ProfileTable],
    labels: Mapping[CensusTable, Mapping[str, tuple[str, str]]],
) -> LoadCounts:
    """Upsert the variables and figures of one vintage; called inside a transaction."""
    area_ids = ensure_tabulation_areas(
        {area_code for table in tables for area_code, _ in table.figures}
    )
    variables = []
    for table in tables:
        table_labels = labels.get(table.table, {})
        for code in table.codes:
            estimate_label, percent_label = table_labels.get(code, ("", ""))
            variables.append(
                CensusVariable(
                    vintage=vintage,
                    table=table.table,
                    code=code,
                    estimate_label=estimate_label,
                    percent_label=percent_label,
                )
            )
    CensusVariable.objects.bulk_create(
        variables,
        update_conflicts=True,
        unique_fields=("vintage", "code"),
        update_fields=("table", "estimate_label", "percent_label"),
    )
    variable_ids = dict(
        CensusVariable.objects.filter(vintage=vintage).values_list("code", "id")
    )
    counts = LoadCounts(created=0, updated=0, unchanged=0)
    for table in tables:
        _store_table_figures(table, area_ids, variable_ids, counts)
    return counts


def store_boundaries(boundaries: Mapping[str, MultiPolygon]) -> LoadCounts:
    """Upsert the areas' boundaries, creating missing areas; called in a transaction."""
    existing = {
        area.code: area
        for area in TabulationArea.objects.filter(code__in=list(boundaries))
    }
    counts = LoadCounts(created=0, updated=0, unchanged=0)
    to_write = []
    for code, boundary in boundaries.items():
        area = existing.get(code)
        if area is None:
            counts.created += 1
        elif area.boundary is None or not area.boundary.equals_exact(
            boundary, tolerance=0
        ):
            counts.updated += 1
        else:
            counts.unchanged += 1
            continue
        to_write.append(TabulationArea(code=code, boundary=boundary))
    TabulationArea.objects.bulk_create(
        to_write,
        batch_size=BOUNDARY_BATCH_SIZE,
        update_conflicts=True,
        unique_fields=("code",),
        update_fields=("boundary",),
    )
    return counts


def _store_table_figures(
    table: ProfileTable,
    area_ids: Mapping[str, int],
    variable_ids: Mapping[str, int],
    counts: LoadCounts,
) -> None:
    stored = {
        (area_id, variable_id): stored_values
        for area_id, variable_id, *stored_values in CensusFigure.objects.filter(
            variable_id__in=[variable_ids[code] for code in table.codes]
        )
        .values_list("tabulation_area_id", "variable_id", *FIGURE_VALUE_FIELD_NAMES)
        .iterator(chunk_size=FIGURE_READ_CHUNK_SIZE)
    }
    new_figures: list[CensusFigure] = []
    changed_figures: list[CensusFigure] = []
    # Written in batches while comparing, so a first load never holds a million
    # unsaved figures at once.
    for (area_code, code), values in table.figures.items():
        key = (area_ids[area_code], variable_ids[code])
        current = stored.get(key)
        if current is None:
            new_figures.append(_figure(key, values))
            counts.created += 1
        elif current != _value_list(values):
            changed_figures.append(_figure(key, values))
            counts.updated += 1
        else:
            counts.unchanged += 1
        if len(new_figures) >= FIGURE_BATCH_SIZE:
            _create_figures(new_figures)
        if len(changed_figures) >= FIGURE_BATCH_SIZE:
            _update_figures(changed_figures)
    _create_figures(new_figures)
    _update_figures(changed_figures)


def _value_list(values: FigureValues) -> list[object]:
    return [getattr(values, name) for name in FIGURE_VALUE_FIELD_NAMES]


def _figure(key: tuple[int, int], values: FigureValues) -> CensusFigure:
    area_id, variable_id = key
    return CensusFigure(
        tabulation_area_id=area_id,
        variable_id=variable_id,
        estimate=values.estimate,
        estimate_margin_of_error=values.estimate_margin_of_error,
        percent=values.percent,
        percent_margin_of_error=values.percent_margin_of_error,
    )


def _create_figures(figures: list[CensusFigure]) -> None:
    CensusFigure.objects.bulk_create(figures, batch_size=FIGURE_BATCH_SIZE)
    figures.clear()


def _update_figures(figures: list[CensusFigure]) -> None:
    CensusFigure.objects.bulk_create(
        figures,
        batch_size=FIGURE_BATCH_SIZE,
        update_conflicts=True,
        unique_fields=("tabulation_area", "variable"),
        update_fields=FIGURE_VALUE_FIELD_NAMES,
    )
    figures.clear()
