"""The admin mixin of every page that shows ingested data or pipeline records."""

from django.http import HttpRequest


class ViewOnlyAdminMixin:
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
