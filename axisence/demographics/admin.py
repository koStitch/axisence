"""View-only admin pages for tabulation areas, Census variables, and figures."""

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.contrib.admin import ModelAdmin
from django.db.models import QuerySet
from django.http import HttpRequest

from axisence.demographics.models import CensusFigure, CensusVariable, TabulationArea

# django-stubs declares ModelAdmin generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    TabulationAreaAdminBase = admin.ModelAdmin[TabulationArea]
    CensusVariableAdminBase = admin.ModelAdmin[CensusVariable]
    CensusFigureAdminBase = admin.ModelAdmin[CensusFigure]
else:
    TabulationAreaAdminBase = admin.ModelAdmin
    CensusVariableAdminBase = admin.ModelAdmin
    CensusFigureAdminBase = admin.ModelAdmin


class _ViewOnlyAdminMixin:
    """Ingested data changes only through the pipeline, never in the admin."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        return False


class TabulationAreaBoundaryListFilter(admin.SimpleListFilter):
    title = "boundary"
    parameter_name = "boundary"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return [("with", "With a boundary"), ("without", "Without a boundary")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "with":
            return queryset.filter(boundary__isnull=False)
        if self.value() == "without":
            return queryset.filter(boundary__isnull=True)
        return queryset


class CensusFigureVintageListFilter(admin.SimpleListFilter):
    title = "vintage"
    parameter_name = "vintage"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        vintages = (
            CensusVariable.objects.order_by("vintage")
            .values_list("vintage", flat=True)
            .distinct()
        )
        return [(str(vintage), str(vintage)) for vintage in vintages]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        value = self.value()
        if value is None or not value.isdigit():
            return queryset
        return queryset.filter(variable__vintage=int(value))


@admin.register(TabulationArea)
class TabulationAreaAdmin(_ViewOnlyAdminMixin, TabulationAreaAdminBase):
    list_display = ("code", "has_boundary")
    list_filter = (TabulationAreaBoundaryListFilter,)
    search_fields = ("code",)
    # The raw boundary is thousands of coordinates; the page shows its outline.
    fields = ("code", "boundary_extent", "boundary_polygon_count")
    readonly_fields = fields

    @admin.display(boolean=True, description="Boundary")
    def has_boundary(self, area: TabulationArea) -> bool:
        return area.boundary is not None

    @admin.display(description="Boundary extent")
    def boundary_extent(self, area: TabulationArea) -> str | None:
        if area.boundary is None:
            return None
        return ", ".join(f"{coordinate:.4f}" for coordinate in area.boundary.extent)

    @admin.display(description="Boundary polygons")
    def boundary_polygon_count(self, area: TabulationArea) -> int | None:
        if area.boundary is None:
            return None
        return len(area.boundary)


@admin.register(CensusVariable)
class CensusVariableAdmin(_ViewOnlyAdminMixin, CensusVariableAdminBase):
    list_display = ("code", "vintage", "table", "estimate_label")
    list_filter = ("vintage", "table")
    search_fields = ("code", "estimate_label", "percent_label")


@admin.register(CensusFigure)
class CensusFigureAdmin(_ViewOnlyAdminMixin, CensusFigureAdminBase):
    list_display = (
        "tabulation_area",
        "variable_code",
        "vintage",
        "estimate",
        "estimate_margin_of_error",
        "percent",
        "percent_margin_of_error",
    )
    list_filter = (CensusFigureVintageListFilter, "variable__table")
    search_fields = ("=tabulation_area__code", "=variable__code")
    list_select_related = ("tabulation_area", "variable")
    # Counting about a million figures on every page would make the list slow.
    show_full_result_count = False

    @admin.display(description="Variable", ordering="variable__code")
    def variable_code(self, figure: CensusFigure) -> str:
        return figure.variable.code

    @admin.display(description="Vintage", ordering="variable__vintage")
    def vintage(self, figure: CensusFigure) -> int:
        return figure.variable.vintage
