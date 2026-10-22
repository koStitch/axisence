"""Preparing repeat jobs, from a job's page or a subscription's page, through one
service, and the subscription rules for reopening a serving job.

Preparing copies the predecessor's target and search settings, never looks the
address up, and records the due date a repeat of a running subscription's latest job
serves. Refusals are not logged.
"""

import dataclasses
import logging
from datetime import date

from django.db import transaction
from django.db.models import Q

from axisence.accounts.models import User
from axisence.reports.listed_competitors import latest_final_report_with_listings
from axisence.research.jobs import ResearchJobError
from axisence.research.merges import MergeChains
from axisence.research.models import JobPurpose, JobStatus, ResearchJob
from axisence.subscriptions.lines import RepeatLines
from axisence.subscriptions.models import ServingJob, Subscription
from axisence.subscriptions.schedule import earliest_due_date_outside, is_running

logger = logging.getLogger(__name__)


class RepeatRefusedError(Exception):
    """A repeat job that cannot be prepared; its message is shown in the admin."""


class _LineMovedError(Exception):
    """The subscription's line moved between reading it and locking it; nothing was
    written.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedRepeat:
    job: ResearchJob
    serving: ServingJob | None


def prepared_message(prepared: PreparedRepeat) -> str:
    job = prepared.job
    message = (
        f"Research job #{job.pk} prepared as a repeat of research job "
        f"#{job.predecessor_id}"
    )
    if prepared.serving is None:
        return f"{message}."
    return (
        f"{message} for subscription #{prepared.serving.subscription_id}, due "
        f"{prepared.serving.due_date.isoformat()}."
    )


def preparation_refusal(job: ResearchJob, *, today: date) -> str | None:
    """Return why preparing a repeat of the job would be refused now, or None."""
    lines = RepeatLines.load()
    refusal = _predecessor_refusal(job, lines)
    if refusal is not None:
        return refusal
    subscription = _running_subscription_ending_at(job.pk, lines, today)
    if subscription is not None and (
        earliest_due_date_outside(subscription, _open_served_due_dates(subscription))
        is None
    ):
        return _no_due_date_left_message(subscription)
    return None


@transaction.atomic
def prepare_repeat_job(
    *,
    predecessor: ResearchJob,
    user: User,
    today: date,
    for_subscription: Subscription | None = None,
) -> PreparedRepeat:
    """Prepare a repeat job of the predecessor. for_subscription is the subscription
    whose page asked, which the repeat must then serve.

    Job rows are locked before subscription rows. Every refusal comes before anything
    is written, so a refused preparation writes and logs nothing.
    """
    stored = ResearchJob.objects.select_for_update().get(pk=predecessor.pk)
    lines = RepeatLines.load()
    refusal = _predecessor_refusal(stored, lines)
    if refusal is not None:
        raise RepeatRefusedError(refusal)
    subscription = _running_subscription_ending_at(stored.pk, lines, today)
    served: Subscription | None = None
    if for_subscription is not None:
        locked = Subscription.objects.select_for_update().get(pk=for_subscription.pk)
        if not is_running(locked, today):
            raise RepeatRefusedError(_ended_message(locked))
        if subscription is None or subscription.pk != locked.pk:
            raise _LineMovedError
        served = locked
    elif subscription is not None:
        locked = Subscription.objects.select_for_update().get(pk=subscription.pk)
        # Read again under the lock, which a correction of the starting job takes too.
        if (
            is_running(locked, today)
            and RepeatLines.load().line_from(locked.starting_job_id)[-1] == stored.pk
        ):
            served = locked
    due: date | None = None
    if served is not None:
        due = earliest_due_date_outside(served, _open_served_due_dates(served))
        if due is None:
            raise RepeatRefusedError(_no_due_date_left_message(served))
    job = ResearchJob.objects.create(
        purpose=JobPurpose.CLIENT_REPORT,
        organization_name=stored.organization_name,
        target_address=stored.target_address,
        target_point=stored.target_point,
        target_point_source=stored.target_point_source,
        geocoding_request_address=stored.geocoding_request_address,
        geocoding_matched_address=stored.geocoding_matched_address,
        geocoding_outcome=stored.geocoding_outcome,
        geocoding_attempted_at=stored.geocoding_attempted_at,
        geocoding_candidates=list(stored.geocoding_candidates),
        preselected_competitor_count=stored.preselected_competitor_count,
        search_radius_miles=stored.search_radius_miles,
        provider_kinds=list(stored.provider_kinds),
        own_competitor_id=None
        if stored.own_competitor_id is None
        else MergeChains.load().current_id(stored.own_competitor_id),
        status=JobStatus.DRAFT,
        created_by=user,
        assigned_researcher=stored.assigned_researcher,
        predecessor=stored,
    )
    if served is None or due is None:
        logger.info(
            "Research job %d prepared as a repeat of research job %d",
            job.pk,
            stored.pk,
        )
        return PreparedRepeat(job=job, serving=None)
    serving = ServingJob.objects.create(job=job, subscription=served, due_date=due)
    logger.info(
        "Research job %d prepared as a repeat of research job %d for subscription "
        "%d, due %s",
        job.pk,
        stored.pk,
        served.pk,
        due.isoformat(),
    )
    return PreparedRepeat(job=job, serving=serving)


@transaction.atomic
def prepare_repeat_job_for_subscription(
    *, subscription: Subscription, user: User, today: date
) -> PreparedRepeat:
    """Prepare the repeat job of the latest job of the subscription's line, for its
    earliest open due date.
    """
    stored = Subscription.objects.get(pk=subscription.pk)
    if not is_running(stored, today):
        raise RepeatRefusedError(_ended_message(stored))
    # A second attempt follows a starting job the superuser corrected between the
    # read of the line and the locks.
    for _ in range(2):
        starting_job_id = Subscription.objects.get(pk=stored.pk).starting_job_id
        latest_id = RepeatLines.load().line_from(starting_job_id)[-1]
        try:
            return prepare_repeat_job(
                predecessor=ResearchJob.objects.get(pk=latest_id),
                user=user,
                today=today,
                for_subscription=stored,
            )
        except _LineMovedError:
            continue
    raise RuntimeError(f"The line of subscription {stored.pk} moved while preparing.")


def refuse_reopening_a_served_due_date(
    sender: type[ResearchJob],
    *,
    job: ResearchJob,
    status: str,
    **kwargs: object,
) -> None:
    """Refuse moving a Cancelled serving job back to Researching while its due date
    is served again or lies after the end date.
    """
    if job.status != JobStatus.CANCELLED or status != JobStatus.RESEARCHING:
        return
    serving = ServingJob.objects.filter(job=job).first()
    if serving is None:
        return
    # The job row is locked already, so job rows stay locked before subscription
    # rows.
    subscription = Subscription.objects.select_for_update().get(
        pk=serving.subscription_id
    )
    due = serving.due_date
    other = (
        ServingJob.objects.filter(subscription=subscription, due_date=due)
        .exclude(job=job)
        .exclude(job__status=JobStatus.CANCELLED)
        .order_by("job_id")
        .first()
    )
    if other is not None:
        raise ResearchJobError(
            f"Research job #{other.job_id} serves due date {due.isoformat()} of "
            f"subscription #{subscription.pk}."
        )
    if subscription.end_date is not None and due > subscription.end_date:
        raise ResearchJobError(
            f"Due date {due.isoformat()} lies after the end date of subscription "
            f"#{subscription.pk}."
        )


def _predecessor_refusal(job: ResearchJob, lines: RepeatLines) -> str | None:
    if job.purpose != JobPurpose.CLIENT_REPORT:
        return "Only client report jobs can be repeated."
    if job.status != JobStatus.COMPLETE:
        return "Only a Complete job can be repeated."
    if latest_final_report_with_listings(job.pk) is None:
        return "The job has no final report to compare against."
    repeat_id = lines.open_repeat_id(job.pk)
    if repeat_id is not None:
        return f"Research job #{repeat_id} already repeats this job."
    return None


def _running_subscription_ending_at(
    job_id: int, lines: RepeatLines, today: date
) -> Subscription | None:
    """Return the running subscription, lowest number first, whose line ends at the
    job.
    """
    for subscription in Subscription.objects.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).order_by("pk"):
        if lines.line_from(subscription.starting_job_id)[-1] == job_id:
            return subscription
    return None


def _open_served_due_dates(subscription: Subscription) -> frozenset[date]:
    return frozenset(
        ServingJob.objects.filter(subscription=subscription)
        .exclude(job__status=JobStatus.CANCELLED)
        .values_list("due_date", flat=True)
    )


def _ended_message(subscription: Subscription) -> str:
    return f"Subscription #{subscription.pk} has ended."


def _no_due_date_left_message(subscription: Subscription) -> str:
    return f"Subscription #{subscription.pk} has no due date left."
