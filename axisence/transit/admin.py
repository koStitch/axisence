"""The view-only admin list of transit stops."""

from typing import TYPE_CHECKING

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from axisence.ingestion.view_only_admin import ViewOnlyAdminMixin
from axisence.transit.models import TransitStop

# django-stubs declares ModelAdmin generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    TransitStopAdminBase = admin.ModelAdmin[TransitStop]
else:
    TransitStopAdminBase = admin.ModelAdmin


@admin.register(TransitStop)
class TransitStopAdmin(ViewOnlyAdminMixin, TransitStopAdminBase):
    list_display = (
        "name",
        "mode",
        "published_stop_id",
        "line_names",
        "station_group_key",
        "is_active",
        "first_seen_run",
        "last_seen_run",
    )
    list_filter = ("mode", "is_active")
    search_fields = ("name", "=published_stop_id")
    list_select_related = ("first_seen_run", "last_seen_run")
    fields = (
        "mode",
        "published_stop_id",
        "name",
        "coordinates",
        "line_names",
        "station_group_key",
        "is_active",
        "first_seen_run",
        "last_seen_run",
    )
    readonly_fields = fields

    def get_queryset(self, request: HttpRequest) -> QuerySet[TransitStop]:
        return super().get_queryset(request).prefetch_related("lines")

    @admin.display(description="Lines")
    def line_names(self, stop: TransitStop) -> str:
        lines = sorted(
            stop.lines.all(), key=lambda line: (line.sort_position, line.name)
        )
        return ", ".join(line.name for line in lines)

    @admin.display(description="Coordinates")
    def coordinates(self, stop: TransitStop) -> str:
        return f"{stop.location.y:.6f}, {stop.location.x:.6f}"
