"""Tests for preparing repeat jobs, the due dates they serve, and the rules for
reopening a serving job.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from axisence.providers.models import Competitor
from axisence.reports.models import Report
from axisence.research import jobs
from axisence.research.merges import MergeChains
from axisence.research.models import (
    Contact,
    JobCompetitor,
    JobPurpose,
    JobStatus,
    ResearchJob,
    TargetLookupOutcome,
    TargetPointSource,
)
from axisence.subscriptions import repeats
from axisence.subscriptions.lines import RepeatLines
from axisence.subscriptions.models import ServingJob
from tests.providers.factories import make_researcher
from tests.reports.factories import make_listed_report, make_ready_job
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_located_competitor,
    point_north_of,
)
from tests.subscriptions.factories import (
    make_repeat_job,
    make_repeatable_job,
    make_serving_job,
    make_subscription,
)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 21)

ORGANIZATION_MARKER = "Synthetic Organization Marker 8801"
ADDRESS_MARKER = "Synthetic Address Marker 8802"
NOTES_MARKER = "synthetic notes marker 8803"


@pytest.fixture
def researcher():
    return make_researcher()


def _prepare(predecessor, user, **fields):
    return repeats.prepare_repeat_job(
        predecessor=predecessor, user=user, today=TODAY, **fields
    )


def _refusal(predecessor, user, **fields):
    with pytest.raises(repeats.RepeatRefusedError) as raised:
        _prepare(predecessor, user, **fields)
    return str(raised.value)


def _complete_with_listed_report(job):
    job.status = JobStatus.COMPLETE
    job.save()
    make_listed_report(job, main=[JobCompetitor.objects.get(job=job).competitor])


def _change_status(job, status):
    jobs.change_status(job=job, status=status, chains=MergeChains.load())


def test_preparing_refusals_in_order(researcher):
    job = make_ready_job(researcher=researcher, purpose=JobPurpose.PROSPECT)
    open_repeat = make_repeat_job(job, researcher=researcher)

    prospect = _refusal(job, researcher)
    job.purpose = JobPurpose.CLIENT_REPORT
    job.save()
    researching = _refusal(job, researcher)
    job.status = JobStatus.COMPLETE
    job.save()
    # A final report from before listed competitors were recorded does not count.
    make_listed_report(job)
    without_listings = _refusal(job, researcher)
    make_listed_report(job, main=[JobCompetitor.objects.get(job=job).competitor])
    repeated = _refusal(job, researcher)
    # The latest job of a running subscription whose every due date is served.
    starting = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        starting,
        created_by=researcher,
        start_date=date(2026, 6, 15),
        end_date=date(2026, 12, 31),
    )
    first = make_repeat_job(starting, researcher=researcher, status=JobStatus.COMPLETE)
    make_serving_job(subscription, first, date(2026, 9, 15))
    latest = make_ready_job(researcher=researcher, predecessor=first)
    make_serving_job(subscription, latest, date(2026, 12, 15))
    latest_researching = _refusal(latest, researcher)
    _complete_with_listed_report(latest)
    no_due_date_left = _refusal(latest, researcher)

    assert prospect == "Only client report jobs can be repeated."
    assert researching == "Only a Complete job can be repeated."
    assert without_listings == "The job has no final report to compare against."
    assert repeated == f"Research job #{open_repeat.pk} already repeats this job."
    assert latest_researching == "Only a Complete job can be repeated."
    assert no_due_date_left == f"Subscription #{subscription.pk} has no due date left."
    assert repeats.preparation_refusal(latest, today=TODAY) == no_due_date_left


def test_a_repeat_job_copies_the_predecessor(researcher, fake_transport):
    own = make_located_competitor(point_north_of(BOROUGH_HALL, 0.05))
    own_now = make_located_competitor(point_north_of(BOROUGH_HALL, 0.06))
    Competitor.objects.filter(pk=own.pk).update(
        retired_at=timezone.now(), merged_into=own_now, is_active=False
    )
    assigned = make_researcher("synthetic-assigned")
    preparing_user = make_researcher("synthetic-preparing")
    candidates = [
        {
            "matched_address": "100 EXAMPLE ST, BROOKLYN, NY, 11217",
            "longitude": "-73.9754",
            "latitude": "40.6826",
        }
    ]
    predecessor = make_repeatable_job(
        researcher=researcher,
        organization_name="Synthetic Repeated Organization",
        target_address="100 Example Street, Brooklyn, NY 11217",
        target_point=point_north_of(BOROUGH_HALL, 0.01),
        target_point_source=TargetPointSource.RESEARCHER,
        geocoding_request_address="100 Example Street, Brooklyn, NY 11217",
        geocoding_matched_address="100 EXAMPLE ST, BROOKLYN, NY, 11217",
        geocoding_outcome=TargetLookupOutcome.SEVERAL_MATCHES,
        geocoding_attempted_at=timezone.now() - timedelta(days=30),
        geocoding_candidates=candidates,
        preselected_competitor_count=7,
        search_radius_miles=Decimal("1.25"),
        provider_kinds=["day_care_center", "family_day_care"],
        own_competitor=own,
        assigned_researcher=assigned,
    )
    job_count = ResearchJob.objects.count()

    prepared = _prepare(predecessor, preparing_user)

    job = ResearchJob.objects.get(pk=prepared.job.pk)
    for field_name in (
        "organization_name",
        "target_address",
        "target_point",
        "target_point_source",
        "geocoding_request_address",
        "geocoding_matched_address",
        "geocoding_outcome",
        "geocoding_attempted_at",
        "geocoding_candidates",
        "preselected_competitor_count",
        "search_radius_miles",
        "provider_kinds",
        "assigned_researcher",
    ):
        assert getattr(job, field_name) == getattr(predecessor, field_name), field_name
    assert job.own_competitor == own_now
    assert job.target_point_confirmed_at is None
    assert job.target_point_confirmed_by is None
    assert job.status == JobStatus.DRAFT
    assert job.purpose == JobPurpose.CLIENT_REPORT
    assert job.created_by == preparing_user
    assert job.predecessor == predecessor
    assert prepared.serving is None
    assert ResearchJob.objects.count() == job_count + 1
    assert not JobCompetitor.objects.filter(job=job).exists()
    assert not Contact.objects.filter(job=job).exists()
    assert not Report.objects.filter(job=job).exists()
    assert fake_transport.requests == []


def test_preparing_runs_in_one_transaction(researcher, monkeypatch):
    starting = make_repeatable_job(researcher=researcher)
    make_subscription(starting, created_by=researcher, start_date=date(2026, 7, 1))
    job_count = ResearchJob.objects.count()

    def failing_create(**fields):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(ServingJob.objects, "create", failing_create)

    with pytest.raises(RuntimeError):
        _prepare(starting, researcher)

    assert ResearchJob.objects.count() == job_count


def test_the_latest_job_of_a_running_subscription_serves_the_earliest_open_due_date(
    researcher,
):
    starting = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        starting, created_by=researcher, start_date=date(2026, 7, 1)
    )

    from_job_page = _prepare(starting, researcher)
    _change_status(from_job_page.job, JobStatus.CANCELLED)
    from_subscription_page = repeats.prepare_repeat_job_for_subscription(
        subscription=subscription, user=researcher, today=TODAY
    )

    for prepared in (from_job_page, from_subscription_page):
        assert prepared.job.predecessor == starting
        assert prepared.serving is not None
        assert prepared.serving.subscription == subscription
        # The Cancelled serving job's due date is served again.
        assert prepared.serving.due_date == date(2026, 10, 1)
        assert repeats.prepared_message(prepared) == (
            f"Research job #{prepared.job.pk} prepared as a repeat of research job "
            f"#{starting.pk} for subscription #{subscription.pk}, due 2026-10-01."
        )


def test_a_repeat_of_any_other_job_serves_no_due_date(researcher):
    ended_latest = make_repeatable_job(researcher=researcher)
    make_subscription(
        ended_latest,
        created_by=researcher,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 5, 1),
    )
    outside = make_repeatable_job(researcher=researcher)

    for predecessor in (ended_latest, outside):
        prepared = _prepare(predecessor, researcher)

        assert prepared.serving is None
        assert repeats.prepared_message(prepared) == (
            f"Research job #{prepared.job.pk} prepared as a repeat of research job "
            f"#{predecessor.pk}."
        )
    assert not ServingJob.objects.exists()


def test_the_subscription_page_refuses_an_ended_subscription_first(researcher, caplog):
    starting = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        starting,
        created_by=researcher,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 5, 1),
    )
    latest = make_ready_job(researcher=researcher, predecessor=starting)
    job_count = ResearchJob.objects.count()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(repeats.RepeatRefusedError) as from_page:
            repeats.prepare_repeat_job_for_subscription(
                subscription=subscription, user=researcher, today=TODAY
            )
        _complete_with_listed_report(latest)
        from_service = _refusal(latest, researcher, for_subscription=subscription)

    assert str(from_page.value) == f"Subscription #{subscription.pk} has ended."
    assert from_service == f"Subscription #{subscription.pk} has ended."
    assert ResearchJob.objects.count() == job_count
    assert caplog.records == []


def test_a_corrected_starting_job_is_followed_on_the_next_attempt(
    researcher, monkeypatch
):
    corrected_start = make_repeatable_job(researcher=researcher)
    earlier_start = make_repeatable_job(researcher=researcher)
    subscription = make_subscription(
        corrected_start, created_by=researcher, start_date=date(2026, 7, 1)
    )
    job_count = ResearchJob.objects.count()
    original_load = RepeatLines.load
    load_count = 0

    def load():
        # The first read sees the line before the correction, as if it still
        # ended at the earlier starting job.
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            return RepeatLines({corrected_start.pk: earlier_start.pk})
        return original_load()

    # The class the preparing service reads, patched for every reader.
    monkeypatch.setattr(RepeatLines, "load", load)

    prepared = repeats.prepare_repeat_job_for_subscription(
        subscription=subscription, user=researcher, today=TODAY
    )

    assert prepared.job.predecessor == corrected_start
    assert prepared.serving is not None
    assert prepared.serving.subscription == subscription
    assert prepared.serving.due_date == date(2026, 10, 1)
    assert not ResearchJob.objects.filter(predecessor=earlier_start).exists()
    assert ResearchJob.objects.count() == job_count + 1


def test_reopening_a_cancelled_serving_job_is_refused(researcher):
    served_again = make_repeatable_job(researcher=researcher)
    first_subscription = make_subscription(
        served_again, created_by=researcher, start_date=date(2026, 7, 1)
    )
    cancelled = make_repeat_job(
        served_again, researcher=researcher, status=JobStatus.CANCELLED
    )
    make_serving_job(first_subscription, cancelled, date(2026, 10, 1))
    other_predecessor = make_repeatable_job(researcher=researcher)
    other = make_repeat_job(other_predecessor, researcher=researcher)
    make_serving_job(first_subscription, other, date(2026, 10, 1))

    ended = make_repeatable_job(researcher=researcher)
    ended_subscription = make_subscription(
        ended,
        created_by=researcher,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 3, 1),
    )
    after_the_end = make_repeat_job(
        ended, researcher=researcher, status=JobStatus.CANCELLED
    )
    make_serving_job(ended_subscription, after_the_end, date(2026, 4, 15))

    both = make_repeatable_job(researcher=researcher)
    both_subscription = make_subscription(
        both, created_by=researcher, start_date=date(2026, 7, 1)
    )
    cancelled_repeat = make_repeat_job(
        both, researcher=researcher, status=JobStatus.CANCELLED
    )
    make_serving_job(both_subscription, cancelled_repeat, date(2026, 10, 1))
    open_repeat = make_repeat_job(both, researcher=researcher)
    make_serving_job(both_subscription, open_repeat, date(2026, 10, 1))

    messages = []
    for job in (cancelled, after_the_end, cancelled_repeat):
        with pytest.raises(jobs.ResearchJobError) as raised:
            _change_status(job, JobStatus.RESEARCHING)
        job.refresh_from_db()
        assert job.status == JobStatus.CANCELLED
        messages.append(str(raised.value))

    assert messages == [
        f"Research job #{other.pk} serves due date 2026-10-01 of subscription "
        f"#{first_subscription.pk}.",
        f"Due date 2026-04-15 lies after the end date of subscription "
        f"#{ended_subscription.pk}.",
        f"Research job #{open_repeat.pk} already repeats research job #{both.pk}.",
    ]


def test_preparing_logs_numbers_only(researcher, caplog):
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
    subscription = make_subscription(
        starting, created_by=researcher, start_date=date(2026, 7, 1)
    )
    outside = make_repeatable_job(
        researcher=researcher, organization_name=ORGANIZATION_MARKER
    )

    with caplog.at_level(logging.DEBUG):
        serving = _prepare(starting, researcher)
        plain = _prepare(outside, researcher)
        messages = [record.getMessage() for record in caplog.records]
        caplog.clear()
        _refusal(outside, researcher)

    assert messages == [
        f"Research job {serving.job.pk} prepared as a repeat of research job "
        f"{starting.pk} for subscription {subscription.pk}, due 2026-10-01",
        f"Research job {plain.job.pk} prepared as a repeat of research job "
        f"{outside.pk}",
    ]
    assert not any(
        marker in message
        for message in messages
        for marker in (ORGANIZATION_MARKER, ADDRESS_MARKER, NOTES_MARKER)
    )
    assert caplog.records == []
