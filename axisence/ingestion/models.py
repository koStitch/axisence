"""Records of pipeline runs and of the raw snapshots each run saved or read."""

from django.db import models


class IngestionSource(models.TextChoices):
    NY_STATE_PROVIDERS = "ny_state_providers", "NY State providers"
    NYC_HEALTH_PROVIDERS = "nyc_health_providers", "NYC Health providers"
    CENSUS_DEMOGRAPHICS = "census_demographics", "Census demographics"
    CENSUS_BOUNDARIES = "census_boundaries", "Census boundaries"
    MTA_TRANSIT = "mta_transit", "MTA transit"


class IngestionOutcome(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class SnapshotKind(models.TextChoices):
    PROVIDER_EXPORT = "provider_export", "Provider export"
    PROVIDER_METADATA = "provider_metadata", "Provider metadata"
    GEOCODER_RESULT = "geocoder_result", "Geocoder result"
    CENSUS_TABLE = "census_table", "Census table"
    CENSUS_VARIABLES = "census_variables", "Census variable labels"
    TABULATION_AREA_LIST = "tabulation_area_list", "Tabulation area list"
    BOUNDARY_FILE = "boundary_file", "Boundary file"
    GTFS_FEED = "gtfs_feed", "GTFS feed"


RESPONSE_HEADER_MAXIMUM_LENGTH = 255


class IngestionRun(models.Model):
    """One invocation of an ingestion command, with what it read and what it changed.

    A null count means the count does not apply to the source or the run failed before
    measuring it.
    """

    source = models.CharField(max_length=40, choices=IngestionSource.choices)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(
        max_length=20,
        choices=IngestionOutcome.choices,
        default=IngestionOutcome.IN_PROGRESS,
    )
    failure_message = models.TextField(blank=True)
    is_reingestion = models.BooleanField(default=False)
    guard_override_used = models.BooleanField(default=False)
    publisher_rows_updated_at = models.DateTimeField(null=True, blank=True)
    census_vintage = models.PositiveSmallIntegerField(null=True, blank=True)
    rows_read_count = models.PositiveIntegerField(null=True, blank=True)
    created_count = models.PositiveIntegerField(null=True, blank=True)
    updated_count = models.PositiveIntegerField(null=True, blank=True)
    unchanged_count = models.PositiveIntegerField(null=True, blank=True)
    marked_missing_count = models.PositiveIntegerField(null=True, blank=True)
    reactivated_count = models.PositiveIntegerField(null=True, blank=True)
    skipped_count = models.PositiveIntegerField(null=True, blank=True)
    field_warning_count = models.PositiveIntegerField(null=True, blank=True)
    geocoded_count = models.PositiveIntegerField(null=True, blank=True)
    geocoding_failure_count = models.PositiveIntegerField(null=True, blank=True)
    competitors_created_count = models.PositiveIntegerField(null=True, blank=True)
    review_flags_raised_count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)
        indexes = (
            models.Index(
                fields=("source", "-started_at"), name="ingestion_run_source_started"
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_source_display()} run {self.pk}"


class IngestionSnapshot(models.Model):
    """One raw file a run saved or re-ingested, located under RAW_DATA_DIRECTORY."""

    run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="snapshots"
    )
    kind = models.CharField(max_length=30, choices=SnapshotKind.choices)
    # Relative to RAW_DATA_DIRECTORY, with forward slashes, so the host and the
    # container read the same value.
    path = models.CharField(max_length=500)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    # Null when the run re-ingested a snapshot saved earlier.
    fetched_at = models.DateTimeField(null=True, blank=True)
    # The ETag and Last-Modified headers of the response that saved the file, as sent;
    # blank for re-recorded files and for sources that make no conditional requests.
    response_etag = models.CharField(
        max_length=RESPONSE_HEADER_MAXIMUM_LENGTH, blank=True
    )
    response_last_modified = models.CharField(
        max_length=RESPONSE_HEADER_MAXIMUM_LENGTH, blank=True
    )

    class Meta:
        ordering = ("run", "pk")

    def __str__(self) -> str:
        return self.path
