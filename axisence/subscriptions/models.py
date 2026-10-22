"""Subscriptions, a client's standing order for repeat reports on a cadence, and the
repeat jobs prepared for their due dates.

Subscriptions are never deleted; a serving job is recorded once and never changed or
deleted, so the due date it serves never changes.
"""

from collections.abc import Iterable

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.base import ModelBase

from axisence.research.models import PermanentRecordError, ResearchJob


class Cadence(models.IntegerChoices):
    # Whole months between due dates. New cadences are appended, and stored values
    # never change.
    QUARTERLY = 3, "Quarterly"
    TWICE_A_YEAR = 6, "Twice a year"
    YEARLY = 12, "Yearly"


END_BEFORE_START_MESSAGE = "The end date cannot be before the start date."

_SUBSCRIPTION_NEVER_DELETED_MESSAGE = (
    "Subscriptions are never deleted; set an end date instead."
)
_SERVING_JOB_NEVER_CHANGED_OR_DELETED_MESSAGE = (
    "Serving jobs are never changed or deleted."
)


class SubscriptionQuerySet(models.QuerySet["Subscription"]):
    """Refuses bulk deletions, which bypass the model's own guard."""

    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_SUBSCRIPTION_NEVER_DELETED_MESSAGE)


class Subscription(models.Model):
    """A client's standing order for a repeat report every cadence, starting from
    one job. Due dates are fixed from the start date.
    """

    starting_job = models.ForeignKey(
        ResearchJob, on_delete=models.PROTECT, related_name="started_subscriptions"
    )
    cadence = models.PositiveSmallIntegerField(choices=Cadence.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_subscriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SubscriptionQuerySet.as_manager()

    class Meta:
        ordering = ("pk",)
        default_permissions = ("add", "change", "view")
        permissions = (("prepare_repeat_job", "Can prepare repeat jobs"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="subscription_ends_on_or_after_its_start",
                violation_error_message=END_BEFORE_START_MESSAGE,
            ),
        )

    def __str__(self) -> str:
        # Never the organization or the address: admin history stores this text.
        return f"Subscription #{self.pk}"

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_SUBSCRIPTION_NEVER_DELETED_MESSAGE)


class ServingJobQuerySet(models.QuerySet["ServingJob"]):
    """Refuses bulk changes and deletions, which bypass the model's own guards."""

    def update(self, **kwargs: object) -> int:
        raise PermanentRecordError(_SERVING_JOB_NEVER_CHANGED_OR_DELETED_MESSAGE)

    def delete(self) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_SERVING_JOB_NEVER_CHANGED_OR_DELETED_MESSAGE)


class ServingJob(models.Model):
    """The repeat job prepared for one due date of a subscription."""

    job = models.OneToOneField(
        ResearchJob, on_delete=models.PROTECT, related_name="serving"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="serving_jobs"
    )
    due_date = models.DateField()

    objects = ServingJobQuerySet.as_manager()

    class Meta:
        ordering = ("subscription_id", "due_date", "job_id")
        default_permissions = ()

    def __str__(self) -> str:
        return f"Serving job #{self.pk}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # Serving jobs are inserted once, with their repeat job, through
        # objects.create.
        if not force_insert:
            raise PermanentRecordError(_SERVING_JOB_NEVER_CHANGED_OR_DELETED_MESSAGE)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise PermanentRecordError(_SERVING_JOB_NEVER_CHANGED_OR_DELETED_MESSAGE)
