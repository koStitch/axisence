"""Generated client reports and teasers, kept with their job and never changed or
deleted.
"""

from collections.abc import Iterable

from django.conf import settings
from django.db import models
from django.db.models.base import ModelBase

from axisence.research.models import PermanentRecordError, ResearchJob


class ReportKind(models.TextChoices):
    DRAFT = "draft", "Draft"
    FINAL = "final", "Final"
    TEASER = "teaser", "Teaser"


_NEVER_CHANGED_OR_DELETED_MESSAGE = "Reports are never changed or deleted."


class ReportQuerySet(models.QuerySet["Report"]):
    """Refuses bulk changes and deletions, which bypass the model's own guards."""

    def update(self, **kwargs: object) -> int:
        raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)

    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)


class Report(models.Model):
    """One generated document of a research job: a client's competitor analysis or a
    teaser.

    The primary key is the document's number, printed on its pages; the generation
    time is its cut-off.
    """

    job = models.ForeignKey(
        ResearchJob, on_delete=models.PROTECT, related_name="reports"
    )
    kind = models.CharField(max_length=20, choices=ReportKind.choices)
    generated_at = models.DateTimeField()
    # Null when the command generated it. Never printed.
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_reports",
    )
    census_vintage = models.PositiveSmallIntegerField(null=True, blank=True)
    tabulation_area_code = models.CharField(max_length=5, blank=True)
    ny_state_provider_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports_reading_ny_state_providers",
    )
    nyc_health_provider_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports_reading_nyc_health_providers",
    )
    # Relative to REPORT_DIRECTORY.
    file_name = models.CharField(max_length=100, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)

    objects = ReportQuerySet.as_manager()

    class Meta:
        ordering = ("-pk",)
        default_permissions = ("view",)
        permissions = (
            ("generate_report", "Can generate reports"),
            ("generate_teaser", "Can generate teasers"),
        )

    def __str__(self) -> str:
        return f"Report #{self.pk}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # Reports are inserted once, with their reserved number, through
        # objects.create.
        if not force_insert:
            raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)
