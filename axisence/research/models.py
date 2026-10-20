"""Research jobs, the competitors selected for them, and what each contact found.

Contacts and observations are append-only: nothing changes or deletes them except
retraction, which records who retracted them, when, and why. Research jobs are never
deleted.
"""

import dataclasses
import functools
import operator
from collections.abc import Iterable
from datetime import time
from decimal import Decimal
from typing import TypeVar

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import F, Q
from django.db.models.base import ModelBase
from django.utils.dates import MONTHS

from axisence.providers.models import Competitor, ProviderKind

RESEARCHERS_GROUP_NAME = "Researchers"

ADDRESS_MAXIMUM_LENGTH = 255
NOTES_MAXIMUM_LENGTH = 2000
RETRACTION_REASON_MAXIMUM_LENGTH = 500
DEFINITION_NAME_MAXIMUM_LENGTH = 100
INSPECTION_STATUS_MAXIMUM_LENGTH = 100
SHORT_DESCRIPTION_MAXIMUM_LENGTH = 200
LONG_DESCRIPTION_MAXIMUM_LENGTH = 1000
WEBSITE_ADDRESS_MAXIMUM_LENGTH = 500

NOTES_WARNING = "Do not enter names, emails, or phone numbers of people."
DESCRIPTION_WARNING = "Do not name people."

RETRACTION_FIELD_NAMES = frozenset(
    {"retracted_at", "retracted_by", "retraction_reason"}
)

# The en dash of ranges, as a code point, because ruff flags the literal character.
_EN_DASH = chr(0x2013)


class JobPurpose(models.TextChoices):
    CLIENT_REPORT = "client_report", "Client report"
    PROSPECT = "prospect", "Prospect"


class JobStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    RESEARCHING = "researching", "Researching"
    COMPLETE = "complete", "Complete"
    CANCELLED = "cancelled", "Cancelled"


class TargetPointSource(models.TextChoices):
    GEOCODER = "geocoder", "Geocoder"
    RESEARCHER = "researcher", "Researcher"


class TargetLookupOutcome(models.TextChoices):
    MATCHED = "matched", "Matched"
    SEVERAL_MATCHES = "several_matches", "Several matches"
    NO_MATCH = "no_match", "No match"
    OUTSIDE_NEW_YORK = "outside_new_york", "Outside New York"
    REQUEST_FAILED = "request_failed", "Request failed"


class ResearchStatus(models.TextChoices):
    TO_DO = "to_do", "To do"
    IN_PROGRESS = "in_progress", "In progress"
    DONE = "done", "Done"
    UNREACHABLE = "unreachable", "Unreachable"


class ContactMethod(models.TextChoices):
    CALL = "call", "Call"
    WEBSITE = "website", "Website"
    EMAIL = "email", "Email"


class PersonRole(models.TextChoices):
    DIRECTOR = "director", "Director"
    FRONT_DESK = "front_desk", "Front desk"
    OWNER = "owner", "Owner"
    OTHER = "other", "Other"


class ObservationKind(models.TextChoices):
    AGE_GROUP = "age_group", "Age group"
    SCHEDULE = "schedule", "Schedule"
    AGE_GROUP_RETIREMENT = "age_group_retirement", "Age group retirement"
    SCHEDULE_RETIREMENT = "schedule_retirement", "Schedule retirement"
    PRICE = "price", "Price"
    AVAILABILITY = "availability", "Availability"
    DISCOUNT = "discount", "Discount"
    OTHER_CHARGE = "other_charge", "Other charge"
    OPERATING_STATUS = "operating_status", "Operating status"
    WEEKS_OPEN = "weeks_open", "Weeks open per year"
    WEEKDAY_HOURS = "weekday_hours", "Weekday hours"
    INSPECTION_STATUS = "inspection_status", "Inspection status"
    STATED_PLACES = "stated_places", "Places as stated"
    MINIMUM_BOOKING = "minimum_booking", "Minimum booking"
    LAST_FEE_RISE = "last_fee_rise", "Last fee rise"
    DESCRIPTION = "description", "Description or unique selling point"
    PARKING = "parking", "Parking"
    WEBSITE = "website", "Website address"
    THREE_K_FOR_ALL = "three_k_for_all", "Offers 3-K for All seats"
    PRE_K_FOR_ALL = "pre_k_for_all", "Offers Pre-K for All seats"
    ACCEPTS_VOUCHERS = "accepts_vouchers", "Accepts child care vouchers"


class PricePeriod(models.TextChoices):
    HOURLY = "hourly", "Hourly"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    ANNUAL = "annual", "Annual"


class AvailabilityStatus(models.TextChoices):
    OPENINGS = "openings", "Openings"
    LIMITED_OPENINGS = "limited_openings", "Limited openings"
    WAITLIST = "waitlist", "Waitlist"
    FULL_WITHOUT_WAITLIST = "full_without_waitlist", "Full without a waitlist"


class DiscountKind(models.TextChoices):
    SIBLING = "sibling", "Sibling"
    MULTI_DAY = "multi_day", "Multi-day"
    UPFRONT_PAYMENT = "upfront_payment", "Upfront payment"
    REFERRAL = "referral", "Referral"
    OTHER = "other", "Other"


class ChargeKind(models.TextChoices):
    REGISTRATION = "registration", "Registration"
    DEPOSIT = "deposit", "Deposit"
    LATE_PICKUP = "late_pickup", "Late pickup"
    OTHER = "other", "Other"


class ChargeFrequency(models.TextChoices):
    ONCE = "once", "Once"
    PER_YEAR = "per_year", "Per year"
    PER_OCCURRENCE = "per_occurrence", "Per occurrence"
    PER_HOUR = "per_hour", "Per hour"


class OperatingStatus(models.TextChoices):
    OPERATING = "operating", "Operating"
    TEMPORARILY_CLOSED = "temporarily_closed", "Temporarily closed"
    PERMANENTLY_CLOSED = "permanently_closed", "Permanently closed"


class ParkingAvailability(models.TextChoices):
    ON_SITE = "on_site", "On-site"
    STREET_ONLY = "street_only", "Street only"
    NONE = "none", "None"


# Kinds of which a contact records at most one observation.
SINGLE_FACT_KINDS = (
    ObservationKind.OPERATING_STATUS,
    ObservationKind.WEEKS_OPEN,
    ObservationKind.WEEKDAY_HOURS,
    ObservationKind.INSPECTION_STATUS,
    ObservationKind.STATED_PLACES,
    ObservationKind.MINIMUM_BOOKING,
    ObservationKind.LAST_FEE_RISE,
    ObservationKind.DESCRIPTION,
    ObservationKind.PARKING,
    ObservationKind.WEBSITE,
    ObservationKind.THREE_K_FOR_ALL,
    ObservationKind.PRE_K_FOR_ALL,
    ObservationKind.ACCEPTS_VOUCHERS,
)

# The provider's own definitions that prices and availability refer to.
DEFINITION_KINDS = (ObservationKind.AGE_GROUP, ObservationKind.SCHEDULE)

DESCRIPTION_MAXIMUM_LENGTH_BY_KIND: dict[str, int] = {
    ObservationKind.DESCRIPTION: LONG_DESCRIPTION_MAXIMUM_LENGTH,
    ObservationKind.DISCOUNT: SHORT_DESCRIPTION_MAXIMUM_LENGTH,
    ObservationKind.OTHER_CHARGE: SHORT_DESCRIPTION_MAXIMUM_LENGTH,
    ObservationKind.PARKING: SHORT_DESCRIPTION_MAXIMUM_LENGTH,
}


def default_provider_kinds() -> list[str]:
    """Return the provider kinds a new job searches: centers and school-based care."""
    return [
        ProviderKind.DAY_CARE_CENTER.value,
        ProviderKind.SMALL_DAY_CARE_CENTER.value,
        ProviderKind.SCHOOL_BASED_CHILD_CARE.value,
    ]


class PermanentRecordError(Exception):
    """A contact, observation, or research job was about to be changed or deleted."""


_RecordT = TypeVar("_RecordT", bound=models.Model)

_NEVER_CHANGED_MESSAGE = (
    "Contacts and observations are never changed; retract them instead."
)
_NEVER_DELETED_MESSAGE = (
    "Contacts and observations are never deleted; retract them instead."
)
_NEVER_CHANGED_OR_DELETED_MESSAGE = (
    "Contacts and observations are never changed or deleted; retract them instead."
)
_JOB_NEVER_DELETED_MESSAGE = "Research jobs are never deleted; cancel them instead."


class AppendOnlyQuerySet(models.QuerySet[_RecordT]):
    """Refuses bulk changes and deletions, which bypass the models' own guards."""

    def update(self, **kwargs: object) -> int:
        raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)

    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_NEVER_CHANGED_OR_DELETED_MESSAGE)


class ResearchJobQuerySet(models.QuerySet["ResearchJob"]):
    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_JOB_NEVER_DELETED_MESSAGE)


class ResearchJob(models.Model):
    """One competitor analysis for a target address, from the draft to its end.

    The target point is proposed by the geocoder or placed by a researcher, and the
    search and the research need it confirmed on the map.
    """

    purpose = models.CharField(max_length=30, choices=JobPurpose.choices)
    organization_name = models.CharField(max_length=255)
    target_address = models.CharField(
        max_length=ADDRESS_MAXIMUM_LENGTH,
        help_text="One line, as the client or prospect gives it.",
    )
    target_point = models.PointField(srid=4326, geography=True, null=True, blank=True)
    target_point_source = models.CharField(
        max_length=30, choices=TargetPointSource.choices, blank=True
    )
    target_point_confirmed_at = models.DateTimeField(null=True, blank=True)
    target_point_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_research_job_targets",
    )
    geocoding_request_address = models.CharField(
        max_length=ADDRESS_MAXIMUM_LENGTH, blank=True
    )
    geocoding_matched_address = models.CharField(
        max_length=ADDRESS_MAXIMUM_LENGTH, blank=True
    )
    geocoding_outcome = models.CharField(
        max_length=30, choices=TargetLookupOutcome.choices, blank=True
    )
    geocoding_attempted_at = models.DateTimeField(null=True, blank=True)
    # Filled only for several matches: matched_address, longitude, and latitude of
    # each, the coordinates as decimal text.
    geocoding_candidates = models.JSONField(default=list, blank=True)
    preselected_competitor_count = models.PositiveSmallIntegerField(
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        verbose_name="competitors to pre-select",
    )
    search_radius_miles = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[
            MinValueValidator(Decimal("0.10")),
            MaxValueValidator(Decimal("5.00")),
        ],
    )
    provider_kinds = ArrayField(
        models.CharField(max_length=40, choices=ProviderKind.choices),
        default=default_provider_kinds,
    )
    own_competitor = models.ForeignKey(
        Competitor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="research_jobs_as_own_competitor",
        help_text="The target itself, when it is a listed provider.",
    )
    status = models.CharField(
        max_length=30, choices=JobStatus.choices, default=JobStatus.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_research_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_researcher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_research_jobs",
    )

    objects = ResearchJobQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(Q(target_point__isnull=True) & Q(target_point_source=""))
                | (Q(target_point__isnull=False) & ~Q(target_point_source="")),
                name="research_job_point_source_matches_point",
            ),
            models.CheckConstraint(
                condition=(
                    Q(target_point_confirmed_at__isnull=True)
                    & Q(target_point_confirmed_by__isnull=True)
                )
                | (
                    Q(target_point_confirmed_at__isnull=False)
                    & Q(target_point_confirmed_by__isnull=False)
                    & Q(target_point__isnull=False)
                ),
                name="research_job_confirmation_is_complete",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=("researching", "complete"))
                | Q(target_point_confirmed_at__isnull=False),
                name="research_job_research_requires_confirmed_point",
            ),
            models.CheckConstraint(
                condition=Q(preselected_competitor_count__gte=1)
                & Q(preselected_competitor_count__lte=50),
                name="research_job_preselected_competitor_count_range",
            ),
            models.CheckConstraint(
                condition=Q(search_radius_miles__gte=Decimal("0.10"))
                & Q(search_radius_miles__lte=Decimal("5.00")),
                name="research_job_search_radius_range",
            ),
            models.CheckConstraint(
                condition=Q(provider_kinds__len__gte=1),
                name="research_job_has_provider_kinds",
            ),
        )

    def __str__(self) -> str:
        # Never the address or the organization: admin history stores this text.
        return f"Research job #{self.pk}"

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_JOB_NEVER_DELETED_MESSAGE)

    @property
    def is_target_point_confirmed(self) -> bool:
        return self.target_point_confirmed_at is not None

    def provider_kind_labels(self) -> list[str]:
        return [ProviderKind(kind).label for kind in self.provider_kinds]


class JobCompetitor(models.Model):
    """A competitor selected into a job, with its distance when it was selected."""

    job = models.ForeignKey(
        ResearchJob, on_delete=models.PROTECT, related_name="job_competitors"
    )
    competitor = models.ForeignKey(
        Competitor,
        on_delete=models.PROTECT,
        related_name="research_job_selections",
        limit_choices_to={"is_active": True},
    )
    distance_meters = models.FloatField(null=True, blank=True)
    research_status = models.CharField(
        max_length=30, choices=ResearchStatus.choices, default=ResearchStatus.TO_DO
    )

    class Meta:
        ordering = (F("distance_meters").asc(nulls_last=True), "competitor_id")
        constraints = (
            models.UniqueConstraint(
                fields=("job", "competitor"), name="unique_competitor_per_research_job"
            ),
        )

    def __str__(self) -> str:
        return f"Job competitor #{self.pk}"


class AppendOnlyRecord(models.Model):
    """A row that is inserted once and afterwards changes only by being retracted."""

    class Meta:
        abstract = True

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # pk rather than the private _state tells an update from an insert.
        field_names = None if update_fields is None else frozenset(update_fields)
        if (
            self.pk is not None
            and not force_insert
            and (field_names is None or not field_names <= RETRACTION_FIELD_NAMES)
        ):
            raise PermanentRecordError(_NEVER_CHANGED_MESSAGE)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=field_names,
        )

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_NEVER_DELETED_MESSAGE)


class Contact(AppendOnlyRecord):
    """One call, website visit, or email about one competitor.

    Its observations take their date, method, and researcher from it.
    """

    competitor = models.ForeignKey(
        Competitor, on_delete=models.PROTECT, related_name="research_contacts"
    )
    job = models.ForeignKey(
        ResearchJob,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contacts",
    )
    contact_date = models.DateField()
    method = models.CharField(max_length=30, choices=ContactMethod.choices)
    researcher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="research_contacts",
    )
    entered_at = models.DateTimeField()
    person_role = models.CharField(
        max_length=30,
        choices=PersonRole.choices,
        blank=True,
        verbose_name="role of the person spoken to",
    )
    # A CharField, unlike a TextField, enforces the limit in the database too.
    notes = models.CharField(
        max_length=NOTES_MAXIMUM_LENGTH, blank=True, help_text=NOTES_WARNING
    )
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retracted_research_contacts",
    )
    retraction_reason = models.CharField(
        max_length=RETRACTION_REASON_MAXIMUM_LENGTH, blank=True
    )

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("-contact_date", "-entered_at", "-pk")
        permissions = (("retract_contact", "Can retract contacts and observations"),)
        constraints = (
            models.CheckConstraint(
                condition=(
                    Q(retracted_at__isnull=True)
                    & Q(retracted_by__isnull=True)
                    & Q(retraction_reason="")
                )
                | (
                    Q(retracted_at__isnull=False)
                    & Q(retracted_by__isnull=False)
                    & ~Q(retraction_reason="")
                ),
                name="contact_retraction_is_complete",
            ),
        )

    def __str__(self) -> str:
        return f"Contact #{self.pk}"

    @property
    def is_retracted(self) -> bool:
        return self.retracted_at is not None


@dataclasses.dataclass(frozen=True, slots=True)
class ObservationFields:
    """Which value columns an observation kind must set, and which it may set."""

    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


OBSERVATION_VALUE_FIELD_NAMES = (
    "name",
    "youngest_age_months",
    "oldest_age_months",
    "days_per_week",
    "hours_per_day",
    "age_group",
    "schedule",
    "amount",
    "price_period",
    "includes_discounts",
    "availability_status",
    "earliest_start_on",
    "expected_wait_months",
    "discount_kind",
    "percent",
    "charge_kind",
    "months_of_tuition",
    "charge_frequency",
    "is_none_offered",
    "operating_status",
    "weeks_open_per_year",
    "opens_at",
    "closes_at",
    "inspection_status",
    "inspected_on",
    "stated_places",
    "last_fee_rise_on",
    "description",
    "parking",
    "website_url",
    "yes_no_answer",
)

# Columns whose empty value is the empty string; is_none_offered is empty when False;
# every other value column is empty when null.
_TEXT_VALUE_FIELD_NAMES = frozenset(
    {
        "name",
        "price_period",
        "availability_status",
        "discount_kind",
        "charge_kind",
        "charge_frequency",
        "operating_status",
        "inspection_status",
        "description",
        "parking",
        "website_url",
    }
)

# The two columns that refer to definitions, both foreign keys to Observation.
DEFINITION_REFERENCE_FIELD_NAMES = ("age_group", "schedule")

# The mutually exclusive forms a discount or charge value takes.
_VALUE_FORM_FIELD_NAMES = ("amount", "percent", "months_of_tuition")

# What a "none offered" discount or charge must leave empty.
_NONE_OFFERED_EMPTY_FIELD_NAMES = (
    *_VALUE_FORM_FIELD_NAMES,
    "charge_frequency",
    "description",
)

OBSERVATION_FIELDS_BY_KIND: dict[str, ObservationFields] = {
    ObservationKind.AGE_GROUP: ObservationFields(
        required=("name", "youngest_age_months"), optional=("oldest_age_months",)
    ),
    ObservationKind.SCHEDULE: ObservationFields(
        required=("name", "days_per_week"), optional=("hours_per_day",)
    ),
    ObservationKind.AGE_GROUP_RETIREMENT: ObservationFields(required=("age_group",)),
    ObservationKind.SCHEDULE_RETIREMENT: ObservationFields(required=("schedule",)),
    ObservationKind.PRICE: ObservationFields(
        required=(
            "age_group",
            "schedule",
            "amount",
            "price_period",
            "includes_discounts",
        )
    ),
    ObservationKind.AVAILABILITY: ObservationFields(
        required=("age_group", "availability_status"),
        optional=("earliest_start_on", "expected_wait_months"),
    ),
    ObservationKind.DISCOUNT: ObservationFields(
        required=("discount_kind",),
        optional=("is_none_offered", "amount", "percent", "description"),
    ),
    ObservationKind.OTHER_CHARGE: ObservationFields(
        required=("charge_kind",),
        optional=(
            "is_none_offered",
            "amount",
            "months_of_tuition",
            "charge_frequency",
            "description",
        ),
    ),
    ObservationKind.OPERATING_STATUS: ObservationFields(required=("operating_status",)),
    ObservationKind.WEEKS_OPEN: ObservationFields(required=("weeks_open_per_year",)),
    ObservationKind.WEEKDAY_HOURS: ObservationFields(
        required=("opens_at", "closes_at")
    ),
    ObservationKind.INSPECTION_STATUS: ObservationFields(
        required=("inspection_status",), optional=("inspected_on",)
    ),
    ObservationKind.STATED_PLACES: ObservationFields(required=("stated_places",)),
    ObservationKind.MINIMUM_BOOKING: ObservationFields(required=("days_per_week",)),
    ObservationKind.LAST_FEE_RISE: ObservationFields(required=("last_fee_rise_on",)),
    ObservationKind.DESCRIPTION: ObservationFields(required=("description",)),
    ObservationKind.PARKING: ObservationFields(
        required=("parking",), optional=("description",)
    ),
    ObservationKind.WEBSITE: ObservationFields(required=("website_url",)),
    ObservationKind.THREE_K_FOR_ALL: ObservationFields(required=("yes_no_answer",)),
    ObservationKind.PRE_K_FOR_ALL: ObservationFields(required=("yes_no_answer",)),
    ObservationKind.ACCEPTS_VOUCHERS: ObservationFields(required=("yes_no_answer",)),
}

_NEEDS_VALUE_MESSAGE = "This kind of observation needs a value here."
_UNUSED_VALUE_MESSAGE = "This kind of observation does not use this value."


def _empty_value_condition(field_name: str) -> Q:
    if field_name in _TEXT_VALUE_FIELD_NAMES:
        return Q(**{field_name: ""})
    if field_name == "is_none_offered":
        return Q(is_none_offered=False)
    return Q(**{f"{field_name}__isnull": True})


def _fields_match_kind_condition() -> Q:
    """Require each kind's columns, and leave the columns it does not use empty."""
    kind_conditions = []
    for kind, fields in OBSERVATION_FIELDS_BY_KIND.items():
        condition = Q(kind=kind)
        for field_name in OBSERVATION_VALUE_FIELD_NAMES:
            if field_name in fields.required:
                condition &= ~_empty_value_condition(field_name)
            elif field_name not in fields.optional:
                condition &= _empty_value_condition(field_name)
        kind_conditions.append(condition)
    return functools.reduce(operator.or_, kind_conditions)


class Observation(AppendOnlyRecord):
    """One typed fact within a contact.

    The kind decides which value columns are set, as OBSERVATION_FIELDS_BY_KIND lists
    them. Age groups and schedules are observations too, so prices and availability
    refer to the provider's own definitions.
    """

    contact = models.ForeignKey(
        Contact, on_delete=models.PROTECT, related_name="observations"
    )
    kind = models.CharField(max_length=40, choices=ObservationKind.choices)
    name = models.CharField(
        max_length=DEFINITION_NAME_MAXIMUM_LENGTH,
        blank=True,
        help_text="The provider's own name for it.",
    )
    youngest_age_months = models.PositiveSmallIntegerField(null=True, blank=True)
    oldest_age_months = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Blank when the group has no upper limit."
    )
    days_per_week = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    hours_per_day = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal(24))],
    )
    age_group = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="age_group_references",
    )
    schedule = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="schedule_references",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(0))],
        help_text="US dollars, as stated.",
    )
    price_period = models.CharField(
        max_length=30, choices=PricePeriod.choices, blank=True
    )
    includes_discounts = models.BooleanField(null=True, blank=True)
    availability_status = models.CharField(
        max_length=30, choices=AvailabilityStatus.choices, blank=True
    )
    earliest_start_on = models.DateField(null=True, blank=True)
    expected_wait_months = models.PositiveSmallIntegerField(null=True, blank=True)
    discount_kind = models.CharField(
        max_length=30, choices=DiscountKind.choices, blank=True
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal(100)),
        ],
    )
    charge_kind = models.CharField(
        max_length=30, choices=ChargeKind.choices, blank=True
    )
    months_of_tuition = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    charge_frequency = models.CharField(
        max_length=30, choices=ChargeFrequency.choices, blank=True
    )
    is_none_offered = models.BooleanField(default=False)
    operating_status = models.CharField(
        max_length=30, choices=OperatingStatus.choices, blank=True
    )
    weeks_open_per_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(52)],
    )
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)
    inspection_status = models.CharField(
        max_length=INSPECTION_STATUS_MAXIMUM_LENGTH,
        blank=True,
        help_text="As the source states it.",
    )
    inspected_on = models.DateField(null=True, blank=True)
    stated_places = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="As the provider states them; never licensed capacity.",
    )
    last_fee_rise_on = models.DateField(
        null=True, blank=True, help_text="The first day of the month of the rise."
    )
    description = models.CharField(
        max_length=LONG_DESCRIPTION_MAXIMUM_LENGTH,
        blank=True,
        help_text=DESCRIPTION_WARNING,
    )
    parking = models.CharField(
        max_length=30, choices=ParkingAvailability.choices, blank=True
    )
    website_url = models.URLField(max_length=WEBSITE_ADDRESS_MAXIMUM_LENGTH, blank=True)
    yes_no_answer = models.BooleanField(null=True, blank=True)
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retracted_research_observations",
    )
    retraction_reason = models.CharField(
        max_length=RETRACTION_REASON_MAXIMUM_LENGTH, blank=True
    )

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("pk",)
        constraints = (
            models.CheckConstraint(
                condition=_fields_match_kind_condition(),
                name="observation_fields_match_kind",
            ),
            models.CheckConstraint(
                condition=(
                    Q(retracted_at__isnull=True)
                    & Q(retracted_by__isnull=True)
                    & Q(retraction_reason="")
                )
                | (
                    Q(retracted_at__isnull=False)
                    & Q(retracted_by__isnull=False)
                    & ~Q(retraction_reason="")
                ),
                name="observation_retraction_is_complete",
            ),
            models.CheckConstraint(
                condition=Q(oldest_age_months__isnull=True)
                | Q(youngest_age_months__isnull=True)
                | Q(youngest_age_months__lte=F("oldest_age_months")),
                name="observation_age_range_is_ordered",
            ),
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True)
                | Q(closes_at__isnull=True)
                | Q(opens_at__lt=F("closes_at")),
                name="observation_opening_hours_are_ordered",
            ),
            models.CheckConstraint(
                condition=~(Q(amount__isnull=False) & Q(percent__isnull=False))
                & ~(Q(amount__isnull=False) & Q(months_of_tuition__isnull=False))
                & ~(Q(percent__isnull=False) & Q(months_of_tuition__isnull=False)),
                name="observation_has_one_value_form",
            ),
            models.CheckConstraint(
                condition=Q(is_none_offered=False)
                | (
                    Q(amount__isnull=True)
                    & Q(percent__isnull=True)
                    & Q(months_of_tuition__isnull=True)
                    & Q(charge_frequency="")
                    & Q(description="")
                ),
                name="observation_none_offered_has_no_value",
            ),
            models.CheckConstraint(
                condition=Q(last_fee_rise_on__isnull=True) | Q(last_fee_rise_on__day=1),
                name="observation_last_fee_rise_is_first_of_month",
            ),
            models.UniqueConstraint(
                fields=("contact", "kind"),
                condition=Q(kind__in=SINGLE_FACT_KINDS),
                name="unique_single_fact_per_contact",
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()} #{self.pk}"

    def clean(self) -> None:
        fields = OBSERVATION_FIELDS_BY_KIND.get(self.kind)
        if fields is None:
            # clean_fields already reports an unknown kind.
            return
        errors: dict[str, list[str]] = {}
        for field_name in OBSERVATION_VALUE_FIELD_NAMES:
            is_empty = _is_empty_value(self, field_name)
            if field_name in fields.required:
                if is_empty:
                    errors.setdefault(field_name, []).append(_NEEDS_VALUE_MESSAGE)
            elif field_name not in fields.optional and not is_empty:
                errors.setdefault(field_name, []).append(_UNUSED_VALUE_MESSAGE)
        if (
            self.youngest_age_months is not None
            and self.oldest_age_months is not None
            and self.youngest_age_months > self.oldest_age_months
        ):
            errors.setdefault("oldest_age_months", []).append(
                "The oldest age cannot be below the youngest age."
            )
        if (
            self.opens_at is not None
            and self.closes_at is not None
            and self.opens_at >= self.closes_at
        ):
            errors.setdefault("closes_at", []).append(
                "The closing time must come after the opening time."
            )
        value_forms = [
            field_name
            for field_name in _VALUE_FORM_FIELD_NAMES
            if getattr(self, field_name) is not None
        ]
        if len(value_forms) > 1:
            for field_name in value_forms:
                errors.setdefault(field_name, []).append(
                    "Give only one of an amount, a percent, and months of tuition."
                )
        if self.is_none_offered:
            for field_name in _NONE_OFFERED_EMPTY_FIELD_NAMES:
                if not _is_empty_value(self, field_name):
                    errors.setdefault(field_name, []).append(
                        "Leave this empty when none is offered."
                    )
        if self.last_fee_rise_on is not None and self.last_fee_rise_on.day != 1:
            errors.setdefault("last_fee_rise_on", []).append(
                "A fee rise is recorded on the first day of its month."
            )
        description_limit = DESCRIPTION_MAXIMUM_LENGTH_BY_KIND.get(self.kind)
        if description_limit is not None and len(self.description) > description_limit:
            errors.setdefault("description", []).append(
                f"At most {description_limit} characters for this kind of observation."
            )
        if errors:
            raise ValidationError(errors)

    @property
    def is_retracted(self) -> bool:
        return self.retracted_at is not None

    def value_summary(self) -> str:
        """Describe the value in one line, for lists, the call sheet, and choices."""
        match self.kind:
            case ObservationKind.AGE_GROUP:
                if self.oldest_age_months is None:
                    return f"{self.name}, {self.youngest_age_months} months and older"
                return (
                    f"{self.name}, {self.youngest_age_months}{_EN_DASH}"
                    f"{self.oldest_age_months} months"
                )
            case ObservationKind.SCHEDULE:
                summary = f"{self.name}, {self.days_per_week} days per week"
                if self.hours_per_day is not None:
                    summary += f", {_plain_number(self.hours_per_day)} hours per day"
                return summary
            case ObservationKind.AGE_GROUP_RETIREMENT:
                return f"Retires age group {_name_of(self.age_group)}"
            case ObservationKind.SCHEDULE_RETIREMENT:
                return f"Retires schedule {_name_of(self.schedule)}"
            case ObservationKind.PRICE:
                return self._price_summary()
            case ObservationKind.AVAILABILITY:
                return self._availability_summary()
            case ObservationKind.DISCOUNT:
                return self._discount_or_charge_summary(
                    self.get_discount_kind_display()
                )
            case ObservationKind.OTHER_CHARGE:
                return self._discount_or_charge_summary(self.get_charge_kind_display())
        return self._single_fact_summary()

    def _price_summary(self) -> str:
        summary = f"{_dollars(self.amount)} {self.get_price_period_display().lower()}"
        if self.includes_discounts:
            summary += " including discounts"
        return f"{summary} for {_name_of(self.age_group)} on {_name_of(self.schedule)}"

    def _availability_summary(self) -> str:
        summary = (
            f"{self.get_availability_status_display()} for {_name_of(self.age_group)}"
        )
        if self.earliest_start_on is not None:
            summary += f", earliest start {self.earliest_start_on.isoformat()}"
        if self.expected_wait_months is not None:
            summary += f", wait {self.expected_wait_months} months"
        return summary

    def _discount_or_charge_summary(self, kind_label: str) -> str:
        if self.is_none_offered:
            return f"{kind_label}: none offered"
        summary = kind_label
        if self.percent is not None:
            summary += f": {_plain_number(self.percent)}%"
        elif self.amount is not None:
            summary += f": {_dollars(self.amount)}"
        elif self.months_of_tuition is not None:
            summary += f": {_plain_number(self.months_of_tuition)} months of tuition"
        if self.charge_frequency:
            summary += f" {self.get_charge_frequency_display().lower()}"
        if self.description:
            summary += f" ({self.description})"
        return summary

    def _single_fact_summary(self) -> str:
        match self.kind:
            case ObservationKind.OPERATING_STATUS:
                return self.get_operating_status_display()
            case ObservationKind.WEEKS_OPEN:
                return f"{self.weeks_open_per_year} weeks per year"
            case ObservationKind.WEEKDAY_HOURS:
                opens_at = _clock_time(self.opens_at)
                closes_at = _clock_time(self.closes_at)
                return f"{opens_at}{_EN_DASH}{closes_at}"
            case ObservationKind.INSPECTION_STATUS:
                if self.inspected_on is None:
                    return self.inspection_status
                inspected_on = self.inspected_on.isoformat()
                return f"{self.inspection_status}, inspected {inspected_on}"
            case ObservationKind.STATED_PLACES:
                return f"{self.stated_places} places"
            case ObservationKind.MINIMUM_BOOKING:
                return f"At least {self.days_per_week} days per week"
            case ObservationKind.LAST_FEE_RISE:
                if self.last_fee_rise_on is None:
                    return ""
                month = MONTHS[self.last_fee_rise_on.month]
                return f"{month} {self.last_fee_rise_on.year}"
            case ObservationKind.DESCRIPTION:
                return self.description
            case ObservationKind.PARKING:
                summary = self.get_parking_display()
                if self.description:
                    summary += f" ({self.description})"
                return summary
            case ObservationKind.WEBSITE:
                return self.website_url
            case (
                ObservationKind.THREE_K_FOR_ALL
                | ObservationKind.PRE_K_FOR_ALL
                | ObservationKind.ACCEPTS_VOUCHERS
            ):
                return "Yes" if self.yes_no_answer else "No"
        return ""


def _is_empty_value(observation: Observation, field_name: str) -> bool:
    if field_name in DEFINITION_REFERENCE_FIELD_NAMES:
        # The _id attribute, because the related definition may not be loaded.
        return getattr(observation, f"{field_name}_id") is None
    value = getattr(observation, field_name)
    # Text columns are empty as the empty string, is_none_offered as False.
    if field_name in _TEXT_VALUE_FIELD_NAMES or field_name == "is_none_offered":
        return not value
    return value is None


def _name_of(definition: Observation | None) -> str:
    return definition.name if definition is not None else ""


def _dollars(amount: Decimal | None) -> str:
    return "" if amount is None else f"${amount:,.2f}"


def _plain_number(value: Decimal) -> str:
    # Without trailing zeros and never in exponent notation: 10.00 gives 10.
    return f"{value.normalize():f}"


def _clock_time(value: time | None) -> str:
    return "" if value is None else value.strftime("%H:%M")
