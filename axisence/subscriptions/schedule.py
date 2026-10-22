"""Due dates of subscriptions, fixed from their start dates, and how far each
subscription has come: its delivered dates, its next due date, and its due state.
"""

import calendar
import dataclasses
from collections.abc import Collection, Iterator, Mapping, Sequence
from datetime import date, timedelta

from django.db import models

from axisence.reports.models import Report, ReportKind
from axisence.research.models import JobStatus
from axisence.subscriptions.models import ServingJob, Subscription

# Research takes days, so a due date shows two weeks ahead.
DUE_SOON_DAYS = 14


class DueState(models.TextChoices):
    """How near a subscription's next due date is; never stored."""

    OVERDUE = "overdue", "Overdue"
    DUE_SOON = "due_soon", "Due soon"


@dataclasses.dataclass(frozen=True, slots=True)
class SubscriptionProgress:
    is_running: bool
    # Due dates served by a serving job that is not Cancelled.
    open_serving_jobs: Mapping[date, ServingJob]
    # Due dates whose open serving job has a final report.
    delivered_due_dates: frozenset[date]
    next_due_date: date | None
    due_state: DueState | None


def due_date(subscription: Subscription, round_number: int) -> date:
    """Return the due date of a round, counted from 1: the start date plus the
    cadence's months that many times, on the last day of a month too short for the
    start date's day.
    """
    month_index = (
        subscription.start_date.month - 1 + subscription.cadence * round_number
    )
    year = subscription.start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(subscription.start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def due_dates(subscription: Subscription) -> Iterator[date]:
    """Yield the subscription's due dates in order, up to its end date, if any."""
    round_number = 1
    while True:
        due = due_date(subscription, round_number)
        if subscription.end_date is not None and due > subscription.end_date:
            return
        yield due
        round_number += 1


def is_running(subscription: Subscription, today: date) -> bool:
    return subscription.end_date is None or subscription.end_date >= today


def earliest_due_date_outside(
    subscription: Subscription, taken: Collection[date]
) -> date | None:
    """Return the first due date not taken, or None when every due date up to the end
    date is taken.
    """
    for due in due_dates(subscription):
        if due not in taken:
            return due
    return None


def due_state_on(next_due_date: date | None, today: date) -> DueState | None:
    if next_due_date is None:
        return None
    if next_due_date < today:
        return DueState.OVERDUE
    if next_due_date <= today + timedelta(days=DUE_SOON_DAYS):
        return DueState.DUE_SOON
    return None


def load_progress(
    subscriptions: Sequence[Subscription], *, today: date
) -> dict[int, SubscriptionProgress]:
    """Return each subscription's progress, in at most two queries for any number."""
    serving_jobs = list(
        ServingJob.objects.filter(subscription__in=subscriptions)
        .exclude(job__status=JobStatus.CANCELLED)
        .select_related("job")
    )
    delivered_job_ids = (
        set(
            Report.objects.filter(
                job_id__in=[serving.job_id for serving in serving_jobs],
                kind=ReportKind.FINAL,
            ).values_list("job_id", flat=True)
        )
        if serving_jobs
        else set()
    )
    open_serving_by_subscription_id: dict[int, dict[date, ServingJob]] = {}
    for serving in serving_jobs:
        open_serving_by_subscription_id.setdefault(
            serving.subscription_id, {}
        ).setdefault(serving.due_date, serving)
    progress = {}
    for subscription in subscriptions:
        open_serving = open_serving_by_subscription_id.get(subscription.pk, {})
        delivered = frozenset(
            due
            for due, serving in open_serving.items()
            if serving.job_id in delivered_job_ids
        )
        next_due_date = earliest_due_date_outside(subscription, delivered)
        progress[subscription.pk] = SubscriptionProgress(
            is_running=is_running(subscription, today),
            open_serving_jobs=open_serving,
            delivered_due_dates=delivered,
            next_due_date=next_due_date,
            due_state=due_state_on(next_due_date, today),
        )
    return progress


def listed_due_dates(
    subscription: Subscription, progress: SubscriptionProgress, today: date
) -> tuple[date, ...]:
    """Return the due dates the subscription page lists, in order: every one that is
    today or earlier, served by a serving job that is not Cancelled, or next due.
    """
    last = max(
        [
            today,
            *progress.open_serving_jobs,
            *(() if progress.next_due_date is None else (progress.next_due_date,)),
        ]
    )
    listed = []
    for due in due_dates(subscription):
        if due > last:
            break
        if (
            due <= today
            or due in progress.open_serving_jobs
            or due == progress.next_due_date
        ):
            listed.append(due)
    return tuple(listed)
