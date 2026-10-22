"""Tests for due dates, fixed from the start date, and how far a subscription has
come.
"""

import itertools
from datetime import date, timedelta

import pytest

from axisence.reports.models import ReportKind
from axisence.research.models import JobStatus
from axisence.subscriptions.models import Cadence, Subscription
from axisence.subscriptions.schedule import (
    DueState,
    due_dates,
    due_state_on,
    is_running,
    load_progress,
)
from tests.providers.factories import make_researcher
from tests.reports.factories import make_report_job, make_report_row
from tests.subscriptions.factories import (
    make_repeatable_job,
    make_serving_job,
    make_subscription,
)

TODAY = date(2026, 10, 21)


def _first_due_dates(subscription, count):
    return list(itertools.islice(due_dates(subscription), count))


def test_due_dates_count_from_the_start_date_and_clamp_to_the_month_end():
    quarterly = Subscription(cadence=Cadence.QUARTERLY, start_date=date(2026, 1, 31))
    yearly = Subscription(cadence=Cadence.YEARLY, start_date=date(2028, 2, 29))

    assert _first_due_dates(quarterly, 3) == [
        date(2026, 4, 30),
        date(2026, 7, 31),
        date(2026, 10, 31),
    ]
    assert _first_due_dates(yearly, 2) == [date(2029, 2, 28), date(2030, 2, 28)]


def test_due_dates_stop_after_the_end_date():
    on_a_due_date = Subscription(
        cadence=Cadence.QUARTERLY,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 7, 15),
    )
    before_a_due_date = Subscription(
        cadence=Cadence.TWICE_A_YEAR,
        start_date=date(2026, 1, 15),
        end_date=date(2027, 1, 14),
    )

    assert list(due_dates(on_a_due_date)) == [date(2026, 4, 15), date(2026, 7, 15)]
    assert list(due_dates(before_a_due_date)) == [date(2026, 7, 15)]


def test_cadences_store_whole_months():
    assert {cadence.value: cadence.label for cadence in Cadence} == {
        3: "Quarterly",
        6: "Twice a year",
        12: "Yearly",
    }


def test_running_and_ended():
    def subscription(end_date):
        return Subscription(
            cadence=Cadence.QUARTERLY, start_date=date(2026, 1, 15), end_date=end_date
        )

    assert is_running(subscription(None), TODAY)
    assert is_running(subscription(TODAY), TODAY)
    assert not is_running(subscription(TODAY - timedelta(days=1)), TODAY)


def test_due_soon_and_overdue_with_a_fixed_today():
    assert due_state_on(None, TODAY) is None
    assert due_state_on(TODAY - timedelta(days=1), TODAY) == DueState.OVERDUE
    assert due_state_on(TODAY, TODAY) == DueState.DUE_SOON
    assert due_state_on(TODAY + timedelta(days=14), TODAY) == DueState.DUE_SOON
    assert due_state_on(TODAY + timedelta(days=15), TODAY) is None


@pytest.mark.django_db
def test_next_due_date_skips_delivered_dates_and_ignores_cancelled_serving_jobs():
    researcher = make_researcher()
    subscription = make_subscription(
        make_repeatable_job(researcher=researcher),
        created_by=researcher,
        start_date=date(2026, 1, 15),
    )
    delivered = make_report_job(researcher=researcher, status=JobStatus.COMPLETE)
    make_report_row(delivered, ReportKind.FINAL)
    make_serving_job(subscription, delivered, date(2026, 4, 15))
    # A Cancelled serving job's final report delivers nothing.
    cancelled = make_report_job(researcher=researcher, status=JobStatus.CANCELLED)
    make_report_row(cancelled, ReportKind.FINAL)
    make_serving_job(subscription, cancelled, date(2026, 7, 15))
    researching = make_report_job(researcher=researcher)
    make_serving_job(subscription, researching, date(2026, 10, 15))

    progress = load_progress([subscription], today=TODAY)[subscription.pk]

    assert progress.is_running
    assert set(progress.open_serving_jobs) == {date(2026, 4, 15), date(2026, 10, 15)}
    assert progress.delivered_due_dates == {date(2026, 4, 15)}
    assert progress.next_due_date == date(2026, 7, 15)
    assert progress.due_state == DueState.OVERDUE


@pytest.mark.django_db
def test_an_ended_subscription_with_an_undelivered_due_date_stays_overdue():
    researcher = make_researcher()
    subscription = make_subscription(
        make_repeatable_job(researcher=researcher),
        created_by=researcher,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 5, 1),
    )

    progress = load_progress([subscription], today=TODAY)[subscription.pk]

    assert not progress.is_running
    assert progress.next_due_date == date(2026, 4, 15)
    assert progress.due_state == DueState.OVERDUE
