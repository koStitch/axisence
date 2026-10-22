"""Factories for synthetic repeatable jobs, repeat jobs, subscriptions, and serving
jobs.
"""

from axisence.research.models import JobCompetitor, JobStatus
from axisence.subscriptions.models import Cadence, ServingJob, Subscription
from tests.reports.factories import make_listed_report, make_ready_job, make_report_job


def make_repeatable_job(*, researcher, **fields):
    """Return a Complete client report job with a final report listing its one
    competitor.
    """
    job = make_ready_job(researcher=researcher, status=JobStatus.COMPLETE, **fields)
    make_listed_report(job, main=[JobCompetitor.objects.get(job=job).competitor])
    return job


def make_subscription(
    starting_job, *, created_by, cadence=Cadence.QUARTERLY, start_date, end_date=None
):
    return Subscription.objects.create(
        starting_job=starting_job,
        created_by=created_by,
        cadence=cadence,
        start_date=start_date,
        end_date=end_date,
    )


def make_repeat_job(predecessor, *, researcher, status=JobStatus.RESEARCHING, **fields):
    return make_report_job(
        researcher=researcher, status=status, predecessor=predecessor, **fields
    )


def make_serving_job(subscription, job, due_date):
    return ServingJob.objects.create(
        job=job, subscription=subscription, due_date=due_date
    )
