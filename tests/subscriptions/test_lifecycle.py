"""Tests for the validation and saving of subscriptions, and their permanence."""

import logging
from datetime import date

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.urls import reverse

from axisence.research.models import (
    JobCompetitor,
    JobStatus,
    PermanentRecordError,
)
from axisence.subscriptions import lifecycle
from axisence.subscriptions.lines import RepeatLines
from axisence.subscriptions.models import Cadence, ServingJob, Subscription
from tests.providers.factories import make_researcher
from tests.reports.factories import make_listed_report, make_ready_job
from tests.research.factories import make_contact
from tests.subscriptions.factories import (
    make_repeat_job,
    make_repeatable_job,
    make_serving_job,
    make_subscription,
)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 21)
START_DATE = date(2026, 7, 1)

ORGANIZATION_MARKER = "Synthetic Organization Marker 7701"
ADDRESS_MARKER = "Synthetic Address Marker 7702"
NOTES_MARKER = "synthetic notes marker 7703"


@pytest.fixture
def researcher():
    return make_researcher()


def _candidate(starting_job, **fields):
    values = {
        "starting_job": starting_job,
        "cadence": Cadence.QUARTERLY,
        "start_date": START_DATE,
    }
    values.update(fields)
    return Subscription(**values)


def _errors(subscription):
    with pytest.raises(ValidationError) as raised:
        lifecycle.validate_subscription(subscription, today=TODAY)
    return raised.value.message_dict


def _complete_with_listed_report(job):
    job.status = JobStatus.COMPLETE
    job.save()
    make_listed_report(job, main=[JobCompetitor.objects.get(job=job).competitor])


def _repeat_of(predecessor, researcher, status=JobStatus.RESEARCHING):
    repeat_job = make_repeat_job(predecessor, researcher=researcher, status=status)
    JobCompetitor.objects.create(
        job=repeat_job, competitor=JobCompetitor.objects.get(job=predecessor).competitor
    )
    return repeat_job


def test_starting_job_refusals_in_order(researcher):
    starting = make_ready_job(researcher=researcher)
    repeat_job = _repeat_of(starting, researcher)
    running = make_subscription(starting, created_by=researcher, start_date=START_DATE)

    not_complete = _errors(_candidate(starting))
    _complete_with_listed_report(starting)
    repeated = _errors(_candidate(starting))
    _complete_with_listed_report(repeat_job)
    taken = _errors(_candidate(repeat_job))

    assert not_complete == {"starting_job": [lifecycle.STARTING_JOB_MESSAGE]}
    assert repeated == {
        "starting_job": [
            f"Research job #{repeat_job.pk} repeats this job. Start the subscription "
            "from the latest job of its line."
        ]
    }
    assert taken == {
        "starting_job": [f"This job already belongs to subscription #{running.pk}."]
    }


def test_end_date_refusals(admin_client, researcher):
    starting = make_repeatable_job(researcher=researcher)
    stored = make_subscription(starting, created_by=researcher, start_date=START_DATE)
    served = _repeat_of(starting, researcher)
    make_serving_job(stored, served, date(2026, 10, 1))
    cancelled = make_repeat_job(
        starting, researcher=researcher, status=JobStatus.CANCELLED
    )
    make_serving_job(stored, cancelled, date(2027, 1, 1))
    ended_job = make_repeatable_job(researcher=researcher)
    ended = make_subscription(
        ended_job,
        created_by=researcher,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 3, 1),
    )
    running = make_subscription(ended_job, created_by=researcher, start_date=START_DATE)
    stored.end_date = date(2026, 9, 30)
    ended.end_date = None

    before_start = _errors(
        _candidate(
            make_repeatable_job(researcher=researcher), end_date=date(2026, 6, 30)
        )
    )
    before_served = _errors(stored)
    stored.end_date = date(2026, 10, 1)
    lifecycle.validate_subscription(stored, today=TODAY)
    revived = _errors(ended)
    form_page = admin_client.post(
        reverse("admin:subscriptions_subscription_add"),
        {
            "starting_job": make_repeatable_job(researcher=researcher).pk,
            "cadence": Cadence.QUARTERLY,
            "start_date": "2026-07-01",
            "end_date": "2026-06-30",
        },
    )

    assert before_start == {
        "end_date": ["The end date cannot be before the start date."]
    }
    # The Cancelled serving job's later due date never holds the end date back.
    assert before_served == {
        "end_date": [
            "The end date cannot be before 2026-10-01, the latest due date served by "
            "a job that is not Cancelled."
        ]
    }
    assert revived == {
        "end_date": [
            f"A job of this subscription belongs to subscription #{running.pk}."
        ]
    }
    assert form_page.status_code == 200
    assert form_page.text.count("The end date cannot be before the start date.") == 1


def test_locked_fields_after_the_first_serving_job(researcher):
    starting = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        starting, created_by=researcher, start_date=START_DATE
    )
    # A Cancelled serving job keeps the lock.
    make_serving_job(
        subscription,
        make_repeat_job(starting, researcher=researcher, status=JobStatus.CANCELLED),
        date(2026, 10, 1),
    )
    subscription.cadence = Cadence.YEARLY

    assert _errors(subscription) == {
        NON_FIELD_ERRORS: [lifecycle.LOCKED_FIELDS_MESSAGE]
    }


def test_jobs_of_a_subscription_follow_open_repeats(researcher):
    starting = make_repeatable_job(researcher=researcher)
    make_repeat_job(starting, researcher=researcher, status=JobStatus.CANCELLED)
    first = make_repeat_job(starting, researcher=researcher, status=JobStatus.COMPLETE)
    second = make_repeat_job(first, researcher=researcher)

    lines = RepeatLines.load()

    assert lines.line_from(starting.pk) == (starting.pk, first.pk, second.pk)
    assert lines.line_from(second.pk) == (second.pk,)
    assert lines.open_repeat_id(first.pk) == second.pk


def test_subscriptions_are_never_deleted(researcher):
    subscription = make_subscription(
        make_repeatable_job(researcher=researcher),
        created_by=researcher,
        start_date=START_DATE,
    )

    with pytest.raises(PermanentRecordError):
        subscription.delete()
    with pytest.raises(PermanentRecordError):
        Subscription.objects.all().delete()
    assert Subscription.objects.filter(pk=subscription.pk).exists()


def test_serving_jobs_are_never_changed_or_deleted(researcher):
    starting = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        starting, created_by=researcher, start_date=START_DATE
    )
    serving = make_serving_job(
        subscription, _repeat_of(starting, researcher), date(2026, 10, 1)
    )
    serving.due_date = date(2027, 1, 1)

    with pytest.raises(PermanentRecordError):
        serving.save()
    with pytest.raises(PermanentRecordError):
        serving.delete()
    with pytest.raises(PermanentRecordError):
        ServingJob.objects.update(due_date=date(2027, 1, 1))
    with pytest.raises(PermanentRecordError):
        ServingJob.objects.all().delete()
    assert ServingJob.objects.get().due_date == date(2026, 10, 1)
    # Last, because bulk_update's own transaction marks the test's for rollback.
    with pytest.raises(PermanentRecordError):
        ServingJob.objects.bulk_update([serving], ["due_date"])


def test_creating_and_changing_log_numbers_only(researcher, caplog):
    starting = make_repeatable_job(
        researcher=researcher,
        organization_name=ORGANIZATION_MARKER,
        target_address=ADDRESS_MARKER,
    )
    make_contact(
        competitor=JobCompetitor.objects.get(job=starting).competitor,
        researcher=researcher,
        job=starting,
        notes=NOTES_MARKER,
    )
    subscription = _candidate(starting)
    refused = _candidate(
        make_repeatable_job(researcher=researcher), end_date=date(2026, 6, 30)
    )

    with caplog.at_level(logging.DEBUG):
        lifecycle.save_subscription(subscription, user=researcher, today=TODAY)
        subscription.end_date = date(2027, 6, 30)
        lifecycle.save_subscription(subscription, user=researcher, today=TODAY)
        messages = [record.getMessage() for record in caplog.records]
        caplog.clear()
        with pytest.raises(ValidationError):
            lifecycle.save_subscription(refused, user=researcher, today=TODAY)

    subscription.refresh_from_db()
    assert messages == [
        f"Subscription {subscription.pk} created",
        f"Subscription {subscription.pk} changed",
    ]
    assert subscription.created_by == researcher
    assert subscription.end_date == date(2027, 6, 30)
    assert not any(
        marker in message
        for message in messages
        for marker in (ORGANIZATION_MARKER, ADDRESS_MARKER, NOTES_MARKER)
    )
    assert caplog.records == []
    assert refused.pk is None
