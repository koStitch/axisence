"""Licensed childcare providers from both regulators, and the competitors they form."""

from django.conf import settings
from django.contrib.gis.db import models
from django.db.models import F, Q
from django.db.models.functions import Greatest, Least

# Widths of the provider columns narrower than 255 characters that hold published
# values. Parsing checks values against them, so a longer value becomes a field
# warning instead of a failed write.
SOURCE_IDENTIFIER_MAXIMUM_LENGTH = 20
PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH = 20
PHONE_NUMBER_MAXIMUM_LENGTH = 30
NY_STATE_CODE_MAXIMUM_LENGTH = 20


class ProviderSource(models.TextChoices):
    NY_STATE = "ny_state", "NY State OCFS"
    NYC_HEALTH = "nyc_health", "NYC Health"


class ProviderKind(models.TextChoices):
    DAY_CARE_CENTER = "day_care_center", "Day care center"
    SMALL_DAY_CARE_CENTER = "small_day_care_center", "Small day care center"
    GROUP_FAMILY_DAY_CARE = "group_family_day_care", "Group family day care"
    FAMILY_DAY_CARE = "family_day_care", "Family day care"
    SCHOOL_AGE_CHILD_CARE = "school_age_child_care", "School-age child care"
    SCHOOL_BASED_CHILD_CARE = "school_based_child_care", "School-based child care"
    OTHER = "other", "Other"


class Borough(models.TextChoices):
    MANHATTAN = "manhattan", "Manhattan"
    BRONX = "bronx", "Bronx"
    BROOKLYN = "brooklyn", "Brooklyn"
    QUEENS = "queens", "Queens"
    STATEN_ISLAND = "staten_island", "Staten Island"


class LocationSource(models.TextChoices):
    PUBLISHER = "publisher", "Publisher"
    GEOCODER = "geocoder", "Geocoder"
    NONE = "none", "None"


class GeocodingOutcome(models.TextChoices):
    """The result of the latest geocoding attempt; blank when never attempted."""

    MATCHED = "matched", "Matched"
    NO_MATCH = "no_match", "No match"
    TIE = "tie", "Tie"
    OUTSIDE_NEW_YORK = "outside_new_york", "Outside New York"
    REQUEST_FAILED = "request_failed", "Request failed"


class Provider(models.Model):
    """One licensed or permitted program, under its regulator's identifier.

    Columns of one source are prefixed with ny_state_ or nyc_health_. A provider holds
    the current published values only, and is never deleted: a provider absent from
    its source's latest snapshot is marked missing.
    """

    # Identity.
    source = models.CharField(max_length=20, choices=ProviderSource.choices)
    source_identifier = models.CharField(max_length=SOURCE_IDENTIFIER_MAXIMUM_LENGTH)
    kind = models.CharField(max_length=40, choices=ProviderKind.choices)

    # Names.
    original_name = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    name_key = models.CharField(max_length=255, blank=True)

    # Address and contact shared by both sources.
    street_address = models.CharField(max_length=255, blank=True)
    published_zip_code = models.CharField(
        max_length=PUBLISHED_ZIP_CODE_MAXIMUM_LENGTH, blank=True
    )
    zip_key = models.CharField(max_length=5, blank=True)
    address_key = models.CharField(max_length=255, blank=True)
    borough = models.CharField(max_length=20, choices=Borough.choices, blank=True)
    phone_number = models.CharField(max_length=PHONE_NUMBER_MAXIMUM_LENGTH, blank=True)
    total_capacity = models.PositiveIntegerField(null=True, blank=True)

    # NY State OCFS columns.
    ny_state_program_type = models.CharField(
        max_length=NY_STATE_CODE_MAXIMUM_LENGTH, blank=True
    )
    ny_state_region_code = models.CharField(
        max_length=NY_STATE_CODE_MAXIMUM_LENGTH, blank=True
    )
    ny_state_county = models.CharField(max_length=255, blank=True)
    ny_state_facility_status = models.CharField(max_length=255, blank=True)
    ny_state_facility_opened_on = models.DateField(null=True, blank=True)
    ny_state_license_issued_on = models.DateField(null=True, blank=True)
    ny_state_license_expires_on = models.DateField(null=True, blank=True)
    ny_state_is_address_omitted = models.BooleanField(null=True, blank=True)
    ny_state_is_phone_omitted = models.BooleanField(null=True, blank=True)
    ny_state_street_number = models.CharField(max_length=255, blank=True)
    ny_state_street_name = models.CharField(max_length=255, blank=True)
    ny_state_additional_address = models.CharField(max_length=255, blank=True)
    ny_state_floor = models.CharField(max_length=255, blank=True)
    ny_state_apartment = models.CharField(max_length=255, blank=True)
    ny_state_city = models.CharField(max_length=255, blank=True)
    ny_state_state = models.CharField(max_length=255, blank=True)
    ny_state_phone_extension = models.CharField(max_length=255, blank=True)
    ny_state_licensee_name = models.CharField(max_length=255, blank=True)
    ny_state_school_district_name = models.CharField(max_length=255, blank=True)
    ny_state_capacity_description = models.CharField(max_length=255, blank=True)
    ny_state_infant_capacity = models.PositiveIntegerField(null=True, blank=True)
    ny_state_toddler_capacity = models.PositiveIntegerField(null=True, blank=True)
    ny_state_preschool_capacity = models.PositiveIntegerField(null=True, blank=True)
    ny_state_school_age_capacity = models.PositiveIntegerField(null=True, blank=True)
    ny_state_program_profile_url = models.URLField(max_length=255, blank=True)

    # NYC Health columns.
    nyc_health_permit_number = models.CharField(max_length=255, blank=True)
    nyc_health_facility_type = models.CharField(max_length=255, blank=True)
    nyc_health_program_type = models.CharField(max_length=255, blank=True)
    nyc_health_child_age_range = models.CharField(max_length=255, blank=True)
    nyc_health_administers_medication = models.BooleanField(null=True, blank=True)
    nyc_health_building_identification_number = models.CharField(
        max_length=255, blank=True
    )
    nyc_health_borough_block_lot = models.CharField(max_length=255, blank=True)
    nyc_health_community_board = models.CharField(max_length=255, blank=True)
    nyc_health_council_district = models.CharField(max_length=255, blank=True)
    nyc_health_census_tract = models.CharField(max_length=255, blank=True)

    # Location and its provenance.
    location = models.PointField(srid=4326, geography=True, null=True, blank=True)
    location_source = models.CharField(
        max_length=20, choices=LocationSource.choices, default=LocationSource.NONE
    )
    geocoding_request_address = models.CharField(max_length=255, blank=True)
    geocoding_matched_address = models.CharField(max_length=255, blank=True)
    geocoding_attempted_at = models.DateTimeField(null=True, blank=True)
    geocoding_outcome = models.CharField(
        max_length=20, choices=GeocodingOutcome.choices, blank=True
    )

    # Activity in the provider's own source.
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    missing_since = models.DateTimeField(null=True, blank=True)

    # Null only between storing and grouping, inside one transaction.
    competitor = models.ForeignKey(
        "Competitor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="providers",
    )

    class Meta:
        ordering = ("source", "source_identifier")
        constraints = (
            models.UniqueConstraint(
                fields=("source", "source_identifier"),
                name="unique_provider_per_source_identifier",
            ),
            models.CheckConstraint(
                condition=(Q(location__isnull=True) & Q(location_source="none"))
                | (Q(location__isnull=False) & ~Q(location_source="none")),
                name="provider_location_matches_source",
            ),
        )
        indexes = (
            models.Index(
                fields=("address_key", "zip_key"), name="provider_address_and_zip_key"
            ),
        )

    def __str__(self) -> str:
        source = self.get_source_display()
        return f"{self.display_name} ({source} {self.source_identifier})"

    @property
    def is_active(self) -> bool:
        return self.missing_since is None

    @property
    def is_school_based(self) -> bool:
        return self.kind == ProviderKind.SCHOOL_BASED_CHILD_CARE

    @property
    def is_address_omitted(self) -> bool:
        return self.ny_state_is_address_omitted is True

    def has_house_number_and_street(self) -> bool:
        """Return whether the published address can be geocoded."""
        if self.source == ProviderSource.NY_STATE:
            return bool(
                self.ny_state_street_number.strip()
                and self.ny_state_street_name.strip()
            )
        street_address = self.street_address.strip()
        return street_address[:1].isdigit() and len(street_address.split()) >= 2


class Competitor(models.Model):
    """The providers one operator runs at one location, drawn from either source.

    The summary fields describe the representative provider and are refreshed after
    grouping, geocoding, and every researcher action. A retired competitor was merged
    into the competitor named by merged_into and holds no provider.
    """

    display_name = models.CharField(max_length=255, blank=True)
    street_address = models.CharField(max_length=255, blank=True)
    borough = models.CharField(max_length=20, choices=Borough.choices, blank=True)
    location = models.PointField(srid=4326, geography=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_school_based = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="absorbed_competitors",
    )

    class Meta:
        ordering = ("display_name", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(Q(retired_at__isnull=True) & Q(merged_into__isnull=True))
                | (Q(retired_at__isnull=False) & Q(merged_into__isnull=False)),
                name="competitor_retired_with_receiver",
            ),
            models.CheckConstraint(
                condition=~Q(merged_into=F("id")),
                name="competitor_not_merged_into_itself",
            ),
        )

    def __str__(self) -> str:
        return f"{self.display_name or 'Competitor'} (#{self.pk})"


class ReviewFlagReason(models.TextChoices):
    SIMILAR_NAME = "similar_name", "Similar name at one address"
    EQUAL_KEYS_IN_SEVERAL_COMPETITORS = (
        "equal_keys_in_several_competitors",
        "Equal keys in several competitors",
    )
    CHANGED_KEYS = "changed_keys", "Changed keys"


class ReviewFlagState(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class ReviewDecision(models.TextChoices):
    SAME_OPERATOR = "same_operator", "Same operator"
    DIFFERENT_OPERATORS = "different_operators", "Different operators"
    PROVIDER_STAYS = "provider_stays", "Provider stays"
    PROVIDER_MOVED = "provider_moved", "Provider moved"
    SUPERSEDED = "superseded", "Superseded by a merge"


class ReviewFlag(models.Model):
    """A question for a researcher about where a provider belongs.

    A similar-name or equal-keys flag proposes that the flagged competitor and the
    proposed competitor are one operator. A changed-keys flag asks whether a provider
    whose published name or address changed still belongs to its competitor.
    """

    reason = models.CharField(max_length=40, choices=ReviewFlagReason.choices)
    state = models.CharField(
        max_length=20, choices=ReviewFlagState.choices, default=ReviewFlagState.OPEN
    )
    provider = models.ForeignKey(
        Provider, on_delete=models.PROTECT, related_name="review_flags"
    )
    flagged_competitor = models.ForeignKey(
        Competitor, on_delete=models.PROTECT, related_name="flags_as_flagged"
    )
    proposed_competitor = models.ForeignKey(
        Competitor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="flags_as_proposed",
    )
    raised_by_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_flags",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decision = models.CharField(
        max_length=30, choices=ReviewDecision.choices, blank=True
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolved_review_flags",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "pk")
        constraints = (
            models.UniqueConstraint(
                Least("flagged_competitor", "proposed_competitor"),
                Greatest("flagged_competitor", "proposed_competitor"),
                condition=Q(state="open", proposed_competitor__isnull=False),
                name="unique_open_proposal_per_pair",
            ),
            models.UniqueConstraint(
                fields=("provider",),
                condition=Q(state="open", reason="changed_keys"),
                name="unique_open_changed_keys_flag",
            ),
            models.CheckConstraint(
                condition=(
                    Q(reason="changed_keys") & Q(proposed_competitor__isnull=True)
                )
                | (~Q(reason="changed_keys") & Q(proposed_competitor__isnull=False)),
                name="review_flag_proposal_matches_reason",
            ),
            models.CheckConstraint(
                condition=(
                    Q(state="open")
                    & Q(decision="")
                    & Q(resolved_at__isnull=True)
                    & Q(resolved_by__isnull=True)
                )
                | (
                    Q(state="resolved")
                    & ~Q(decision="")
                    & Q(resolved_at__isnull=False)
                    & Q(resolved_by__isnull=False)
                ),
                name="review_flag_resolution_matches_state",
            ),
            models.CheckConstraint(
                condition=~Q(flagged_competitor=F("proposed_competitor")),
                name="review_flag_competitors_differ",
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_reason_display()} #{self.pk}"


class CompetitorDecisionAction(models.TextChoices):
    SAME_OPERATOR = "same_operator", "Same operator"
    DIFFERENT_OPERATORS = "different_operators", "Different operators"
    PROVIDER_STAYS = "provider_stays", "Provider stays"
    PROVIDER_MOVED = "provider_moved", "Provider moved"
    COMPETITORS_MERGED = "competitors_merged", "Competitors merged"


class CompetitorDecision(models.Model):
    """One researcher decision about competitors, kept as a durable log.

    Different-operators decisions also keep grouping from proposing the same pair
    again.
    """

    action = models.CharField(max_length=30, choices=CompetitorDecisionAction.choices)
    review_flag = models.ForeignKey(
        ReviewFlag,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions",
    )
    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="competitor_decisions",
    )
    from_competitor = models.ForeignKey(
        Competitor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions_from",
    )
    to_competitor = models.ForeignKey(
        Competitor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions_to",
    )
    researcher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="competitor_decisions",
    )
    decided_at = models.DateTimeField()

    class Meta:
        ordering = ("-decided_at", "pk")

    def __str__(self) -> str:
        return f"{self.get_action_display()} #{self.pk}"


# Every field a provider run copies from a parsed record, in model order.
INGESTED_FIELD_NAMES = (
    "source",
    "source_identifier",
    "kind",
    "original_name",
    "display_name",
    "name_key",
    "street_address",
    "published_zip_code",
    "zip_key",
    "address_key",
    "borough",
    "phone_number",
    "total_capacity",
    "ny_state_program_type",
    "ny_state_region_code",
    "ny_state_county",
    "ny_state_facility_status",
    "ny_state_facility_opened_on",
    "ny_state_license_issued_on",
    "ny_state_license_expires_on",
    "ny_state_is_address_omitted",
    "ny_state_is_phone_omitted",
    "ny_state_street_number",
    "ny_state_street_name",
    "ny_state_additional_address",
    "ny_state_floor",
    "ny_state_apartment",
    "ny_state_city",
    "ny_state_state",
    "ny_state_phone_extension",
    "ny_state_licensee_name",
    "ny_state_school_district_name",
    "ny_state_capacity_description",
    "ny_state_infant_capacity",
    "ny_state_toddler_capacity",
    "ny_state_preschool_capacity",
    "ny_state_school_age_capacity",
    "ny_state_program_profile_url",
    "nyc_health_permit_number",
    "nyc_health_facility_type",
    "nyc_health_program_type",
    "nyc_health_child_age_range",
    "nyc_health_administers_medication",
    "nyc_health_building_identification_number",
    "nyc_health_borough_block_lot",
    "nyc_health_community_board",
    "nyc_health_council_district",
    "nyc_health_census_tract",
)

# The location and the provenance of the latest geocoding attempt.
LOCATION_FIELD_NAMES = (
    "location",
    "location_source",
    "geocoding_request_address",
    "geocoding_matched_address",
    "geocoding_attempted_at",
    "geocoding_outcome",
)
