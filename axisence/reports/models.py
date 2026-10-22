"""Generated client reports and teasers, and the competitors each report lists, kept
with their job and never changed or deleted.
"""

from collections.abc import Iterable

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.base import ModelBase

from axisence.providers.models import Competitor
from axisence.research.models import PermanentRecordError, ResearchJob


class ReportKind(models.TextChoices):
    DRAFT = "draft", "Draft"
    FINAL = "final", "Final"
    TEASER = "teaser", "Teaser"


class ReportListing(models.TextChoices):
    MAIN = "main", "Main list"
    REPORTED_CLOSED = "reported_closed", "Reported closed"


_NEVER_CHANGED_OR_DELETED_MESSAGE = "Reports are never changed or deleted."
_LISTING_NEVER_CHANGED_OR_DELETED_MESSAGE = (
    "Listed competitors are never changed or deleted."
)


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
    # The report a report of a repeat job is compared with: the predecessor's latest
    # final report with listed competitors at generation. Blank for every other report.
    compared_report = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="comparing_reports",
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
    # The latest succeeded transit run finished by the cut-off; blank for teasers and
    # for reports generated before transit access.
    transit_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports_reading_transit",
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
        constraints = (
            models.CheckConstraint(
                condition=~Q(compared_report=F("id")),
                name="report_not_compared_with_itself",
            ),
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


class ListedCompetitorQuerySet(models.QuerySet["ListedCompetitor"]):
    """Refuses bulk changes and deletions, which bypass the model's own guards."""

    def update(self, **kwargs: object) -> int:
        raise PermanentRecordError(_LISTING_NEVER_CHANGED_OR_DELETED_MESSAGE)

    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_LISTING_NEVER_CHANGED_OR_DELETED_MESSAGE)


class ListedCompetitor(models.Model):
    """One competitor a draft or final report lists, recorded with the report and
    never changed or deleted.
    """

    report = models.ForeignKey(
        Report, on_delete=models.PROTECT, related_name="listed_competitors"
    )
    # The current competitor the report listed, at generation.
    competitor = models.ForeignKey(
        Competitor, on_delete=models.PROTECT, related_name="report_listings"
    )
    listing = models.CharField(max_length=20, choices=ReportListing.choices)
    # The number in the main list; blank in the reported-closed list.
    number = models.PositiveSmallIntegerField(null=True, blank=True)
    # The straight-line distance the report printed.
    distance_meters = models.FloatField(null=True, blank=True)

    objects = ListedCompetitorQuerySet.as_manager()

    class Meta:
        ordering = ("report_id", "listing", "number", "competitor_id")
        default_permissions = ()
        constraints = (
            models.UniqueConstraint(
                fields=("report", "competitor"), name="unique_competitor_per_report"
            ),
            models.UniqueConstraint(
                fields=("report", "number"), name="unique_number_per_report"
            ),
            models.CheckConstraint(
                condition=(
                    Q(listing="main") & Q(number__isnull=False) & Q(number__gte=1)
                )
                | (Q(listing="reported_closed") & Q(number__isnull=True)),
                name="listed_competitor_number_matches_list",
            ),
        )

    def __str__(self) -> str:
        return f"Listed competitor #{self.pk}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # Listed competitors are inserted once, with their report.
        if not force_insert:
            raise PermanentRecordError(_LISTING_NEVER_CHANGED_OR_DELETED_MESSAGE)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_LISTING_NEVER_CHANGED_OR_DELETED_MESSAGE)
