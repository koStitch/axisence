"""View-only admin pages for ingestion runs and their raw snapshots."""

from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from axisence.ingestion.models import IngestionRun, IngestionSnapshot
from axisence.ingestion.view_only_admin import ViewOnlyAdminMixin

# django-stubs declares these admin classes generic, but they have no runtime
# __class_getitem__, so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    IngestionRunAdminBase = admin.ModelAdmin[IngestionRun]
    IngestionSnapshotAdminBase = admin.ModelAdmin[IngestionSnapshot]
    IngestionSnapshotInlineBase = admin.TabularInline[IngestionSnapshot, IngestionRun]
else:
    IngestionRunAdminBase = admin.ModelAdmin
    IngestionSnapshotAdminBase = admin.ModelAdmin
    IngestionSnapshotInlineBase = admin.TabularInline

_SNAPSHOT_FIELD_NAMES = (
    "kind",
    "path",
    "size_bytes",
    "sha256",
    "fetched_at",
    "response_etag",
    "response_last_modified",
)


class IngestionSnapshotInline(IngestionSnapshotInlineBase):
    model = IngestionSnapshot
    fields = _SNAPSHOT_FIELD_NAMES
    readonly_fields = _SNAPSHOT_FIELD_NAMES
    extra = 0
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: IngestionRun | None = None
    ) -> bool:
        return False


@admin.register(IngestionRun)
class IngestionRunAdmin(ViewOnlyAdminMixin, IngestionRunAdminBase):
    list_display = (
        "__str__",
        "source",
        "outcome",
        "started_at",
        "finished_at",
        "rows_read_count",
        "created_count",
        "updated_count",
        "marked_missing_count",
        "field_warning_count",
    )
    list_filter = (
        "source",
        "outcome",
        "is_reingestion",
        "guard_override_used",
        "census_vintage",
    )
    search_fields = ("failure_message", "snapshots__path", "snapshots__sha256")
    date_hierarchy = "started_at"
    inlines = (IngestionSnapshotInline,)


@admin.register(IngestionSnapshot)
class IngestionSnapshotAdmin(ViewOnlyAdminMixin, IngestionSnapshotAdminBase):
    list_display = ("path", "kind", "size_bytes", "sha256", "run", "fetched_at")
    list_filter = ("kind", "run__source")
    search_fields = ("path", "sha256")
    list_select_related = ("run",)
