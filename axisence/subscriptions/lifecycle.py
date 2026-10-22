"""Subscription lifecycle: validation and saving of new and changed subscriptions.

The starting job, cadence, and start date change only until the first repeat job is
prepared for a due date; the end date changes at any time within its limits.
"""

import logging
from datetime import date

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Max

from axisence.accounts.models import User
from axisence.reports.listed_competitors import latest_final_report_with_listings
from axisence.research.models import JobPurpose, JobStatus, ResearchJob
from axisence.subscriptions.lines import RepeatLines
from axisence.subscriptions.models import (
    END_BEFORE_START_MESSAGE,
    ServingJob,
    Subscription,
)
from axisence.subscriptions.schedule import is_running

logger = logging.getLogger(__name__)

STARTING_JOB_MESSAGE = (
    "The starting job must be a Complete client report job with a final report to "
    "compare against."
)
LOCKED_FIELDS_MESSAGE = (
    "The starting job, cadence, and start date change only until the first repeat "
    "job is prepared for a due date."
)


def validate_subscription(subscription: Subscription, *, today: date) -> None:
    """Check a new or changed subscription and raise ValidationError keyed by field.

    Locks job rows before the stored row (the last job of the stored line when the
    starting job changes, then the starting job), so it runs inside a transaction.
    """
    is_new = subscription.pk is None
    if not is_new:
        stored_starting_job_id = (
            Subscription.objects.filter(pk=subscription.pk)
            .values_list("starting_job_id", flat=True)
            .get()
        )
        if stored_starting_job_id != subscription.starting_job_id:
            # A preparation from the stored line and the correction never pass
            # together.
            last_job_id = RepeatLines.load().line_from(stored_starting_job_id)[-1]
            ResearchJob.objects.select_for_update().get(pk=last_job_id)
    job = ResearchJob.objects.select_for_update().get(pk=subscription.starting_job_id)
    stored = (
        None
        if is_new
        else Subscription.objects.select_for_update().get(pk=subscription.pk)
    )
    starting_job_changed = (
        stored is None or stored.starting_job_id != subscription.starting_job_id
    )
    errors: dict[str, list[str]] = {}
    if (
        stored is not None
        and ServingJob.objects.filter(subscription=stored).exists()
        and _locked_fields(stored) != _locked_fields(subscription)
    ):
        errors.setdefault(NON_FIELD_ERRORS, []).append(LOCKED_FIELDS_MESSAGE)
    lines = RepeatLines.load()
    running_others = [
        other
        for other in Subscription.objects.exclude(pk=subscription.pk).order_by("pk")
        if is_running(other, today)
    ]
    if starting_job_changed:
        starting_job_error = _starting_job_error(job, lines, running_others)
        if starting_job_error is not None:
            errors.setdefault("starting_job", []).append(starting_job_error)
    end_date_error = _end_date_error(
        subscription,
        stored,
        lines,
        running_others,
        starting_job_changed=starting_job_changed,
        today=today,
    )
    if end_date_error is not None:
        errors.setdefault("end_date", []).append(end_date_error)
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def save_subscription(subscription: Subscription, *, user: User, today: date) -> None:
    """Validate and store a new or changed subscription."""
    validate_subscription(subscription, today=today)
    if subscription.pk is None:
        subscription.created_by = user
        subscription.save()
        logger.info("Subscription %d created", subscription.pk)
        return
    subscription.save(
        update_fields=("starting_job", "cadence", "start_date", "end_date")
    )
    logger.info("Subscription %d changed", subscription.pk)


def _locked_fields(subscription: Subscription) -> tuple[object, ...]:
    return (subscription.starting_job_id, subscription.cadence, subscription.start_date)


def _starting_job_error(
    job: ResearchJob, lines: RepeatLines, running_others: list[Subscription]
) -> str | None:
    if (
        job.purpose != JobPurpose.CLIENT_REPORT
        or job.status != JobStatus.COMPLETE
        or latest_final_report_with_listings(job.pk) is None
    ):
        return STARTING_JOB_MESSAGE
    repeat_id = lines.open_repeat_id(job.pk)
    if repeat_id is not None:
        return (
            f"Research job #{repeat_id} repeats this job. Start the subscription from "
            "the latest job of its line."
        )
    for other in running_others:
        if job.pk in lines.line_from(other.starting_job_id):
            return f"This job already belongs to subscription #{other.pk}."
    return None


def _end_date_error(
    subscription: Subscription,
    stored: Subscription | None,
    lines: RepeatLines,
    running_others: list[Subscription],
    *,
    starting_job_changed: bool,
    today: date,
) -> str | None:
    end_date = subscription.end_date
    if end_date is not None and end_date < subscription.start_date:
        return END_BEFORE_START_MESSAGE
    if stored is None:
        return None
    latest_served = (
        ServingJob.objects.filter(subscription=stored)
        .exclude(job__status=JobStatus.CANCELLED)
        .aggregate(latest=Max("due_date"))["latest"]
    )
    if end_date is not None and latest_served is not None and end_date < latest_served:
        return (
            f"The end date cannot be before {latest_served.isoformat()}, the latest "
            "due date served by a job that is not Cancelled."
        )
    # A new or changed starting job is checked with the starting job's rules.
    if starting_job_changed or not is_running(subscription, today):
        return None
    line = frozenset(lines.line_from(subscription.starting_job_id))
    for other in running_others:
        if line & frozenset(lines.line_from(other.starting_job_id)):
            return f"A job of this subscription belongs to subscription #{other.pk}."
    return None
