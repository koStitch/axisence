"""Tests for the subscription pages and the research job page's repeat additions."""

import calendar
from datetime import date, timedelta

import pytest
from django.contrib import admin
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from axisence.reports.models import ReportKind
from axisence.research.models import JobStatus, ResearchJob
from axisence.subscriptions import lifecycle
from axisence.subscriptions.models import Cadence, Subscription
from tests.admin_forms import change_form_data
from tests.reports.factories import (
    JOB_PERMISSIONS,
    make_listed_report,
    make_ready_job,
    make_report_row,
    make_staff_with,
)
from tests.research.factories import make_researcher_in_group
from tests.subscriptions.factories import (
    make_repeat_job,
    make_repeatable_job,
    make_serving_job,
    make_subscription,
)

pytestmark = pytest.mark.django_db

PREPARE_BUTTON = 'name="_prepare_repeat_job"'
SUBSCRIPTION_PREPARE_BUTTON = '<input type="submit" value="Prepare repeat job">'


def _job_url(job):
    return reverse("admin:research_researchjob_change", args=(job.pk,))


def _subscription_url(subscription):
    return reverse("admin:subscriptions_subscription_change", args=(subscription.pk,))


def _prepare_url(subscription):
    return reverse(
        "admin:subscriptions_subscription_prepare_repeat_job", args=(subscription.pk,)
    )


def _changelist_url():
    return reverse("admin:subscriptions_subscription_changelist")


def _months_before(day, months):
    month_index = day.year * 12 + day.month - 1 - months
    year, month_offset = divmod(month_index, 12)
    month = month_offset + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _subscription_with_one_served_due_date(user):
    """Return a running subscription whose only due date a repeat job serves, and that
    repeat job, Complete with a listed final report.
    """
    today = timezone.localdate()
    starting = make_repeatable_job(researcher=user)
    subscription = make_subscription(
        starting,
        created_by=user,
        start_date=_months_before(today, 4),
        end_date=today + timedelta(days=1),
    )
    served = make_repeatable_job(researcher=user, predecessor=starting)
    make_serving_job(subscription, served, _months_before(today, 1))
    return subscription, served


def test_the_job_page_shows_repeats_repeated_by_and_subscription(
    admin_client, admin_user
):
    starting = make_repeatable_job(researcher=admin_user)
    subscription = make_subscription(
        starting, created_by=admin_user, start_date=date(2026, 7, 1)
    )
    cancelled = make_repeat_job(
        starting, researcher=admin_user, status=JobStatus.CANCELLED
    )
    serving = make_repeat_job(starting, researcher=admin_user, status=JobStatus.DRAFT)
    make_serving_job(subscription, serving, date(2026, 10, 1))

    starting_page = admin_client.get(_job_url(starting)).text
    repeat_page = admin_client.get(_job_url(serving)).text

    assert "<h2>Repeats</h2>" in starting_page
    assert "Repeats: " not in starting_page
    assert starting_page.index(
        f'<a href="{_job_url(serving)}">research job #{serving.pk}</a> (Draft)'
    ) < starting_page.index(
        f'<a href="{_job_url(cancelled)}">research job #{cancelled.pk}</a> (Cancelled)'
    )
    assert (
        f'Repeats: <a href="{_job_url(starting)}">research job #{starting.pk}</a>'
        in repeat_page
    )
    assert (
        f'Subscription: <a href="{_subscription_url(subscription)}">subscription '
        f"#{subscription.pk}</a>, due 2026-10-01" in repeat_page
    )


def _researching(user):
    return make_ready_job(researcher=user)


def _without_listings(user):
    job = make_ready_job(researcher=user, status=JobStatus.COMPLETE)
    make_listed_report(job)
    return job


def _with_open_repeat(user):
    job = make_repeatable_job(researcher=user)
    make_repeat_job(job, researcher=user)
    return job


def _without_due_date_left(user):
    _, served = _subscription_with_one_served_due_date(user)
    return served


def _eligible(user):
    return make_repeatable_job(researcher=user)


# The job to show, the permissions of a staff user (None for the superuser), and
# whether the button shows.
BUTTON_CASES = {
    "eligible": (_eligible, None, True),
    "researching": (_researching, None, False),
    "no listings": (_without_listings, None, False),
    "open repeat": (_with_open_repeat, None, False),
    "no due date left": (_without_due_date_left, None, False),
    "without the permission": (_eligible, JOB_PERMISSIONS, False),
    "without the change permission": (
        _eligible,
        ("prepare_repeat_job", "add_researchjob", "view_researchjob"),
        False,
    ),
}


@pytest.mark.parametrize("case", list(BUTTON_CASES))
def test_the_prepare_button_appears_only_when_preparing_would_succeed(
    client, admin_user, case
):
    build_job, permissions, shown = BUTTON_CASES[case]
    job = build_job(admin_user)
    client.force_login(
        admin_user if permissions is None else make_staff_with(*permissions)
    )

    page = client.get(_job_url(job))

    assert page.status_code == 200
    assert (PREPARE_BUTTON in page.text) is shown


def test_the_prepare_button_saves_the_page_first_then_prepares(
    admin_client, admin_user
):
    job = make_repeatable_job(researcher=admin_user)
    url = _job_url(job)

    response = admin_client.post(
        url,
        change_form_data(
            admin_client,
            url,
            organization_name="Synthetic Renamed Organization",
            _prepare_repeat_job="1",
        ),
        follow=True,
    )

    repeat_job = ResearchJob.objects.get(predecessor=job)
    job.refresh_from_db()
    assert response.redirect_chain[-1][0] == _job_url(repeat_job)
    assert job.organization_name == "Synthetic Renamed Organization"
    assert repeat_job.organization_name == "Synthetic Renamed Organization"
    assert (
        f"Research job #{repeat_job.pk} prepared as a repeat of research job "
        f"#{job.pk}." in response.text
    )


REFUSED_POSTS = {
    "job page without adding jobs": ("job", (*JOB_PERMISSIONS, "prepare_repeat_job")),
    "job page without preparing": ("job", (*JOB_PERMISSIONS, "add_researchjob")),
    "subscription page without adding jobs": (
        "subscription",
        ("view_subscription", "view_researchjob", "prepare_repeat_job"),
    ),
    "subscription page without preparing": (
        "subscription",
        ("view_subscription", "view_researchjob", "add_researchjob"),
    ),
    "subscription page without viewing subscriptions": (
        "subscription",
        ("view_researchjob", "prepare_repeat_job", "add_researchjob"),
    ),
}


@pytest.mark.parametrize("case", list(REFUSED_POSTS))
def test_a_prepare_post_without_the_permissions_is_refused(client, admin_user, case):
    page, permissions = REFUSED_POSTS[case]
    job = make_repeatable_job(researcher=admin_user)
    subscription = make_subscription(
        job, created_by=admin_user, start_date=date(2026, 7, 1)
    )
    client.force_login(make_staff_with(*permissions))
    job_count = ResearchJob.objects.count()

    if page == "job":
        url = _job_url(job)
        response = client.post(
            url, change_form_data(client, url, _prepare_repeat_job="1")
        )
    else:
        response = client.post(_prepare_url(subscription))

    assert response.status_code == 403
    assert ResearchJob.objects.count() == job_count


def test_the_subscription_list_orders_filters_and_searches(admin_client, admin_user):
    today = timezone.localdate()

    def subscription(start_date, **fields):
        return make_subscription(
            make_repeatable_job(researcher=admin_user),
            created_by=admin_user,
            start_date=start_date,
            **fields,
        )

    without_due_date = subscription(
        today - timedelta(days=10), end_date=today - timedelta(days=5)
    )
    far = subscription(_months_before(today, 1))
    overdue = subscription(_months_before(today, 4))
    due_soon = subscription(_months_before(today + timedelta(days=7), 3))
    yearly = subscription(_months_before(today, 6), cadence=Cadence.YEARLY)

    def listed(**query):
        response = admin_client.get(_changelist_url(), query)
        assert response.status_code == 200
        return list(response.context["cl"].result_list)

    assert listed() == [overdue, due_soon, far, yearly, without_due_date]
    assert listed(due_state="overdue") == [overdue]
    assert listed(due_state="due_soon") == [due_soon]
    assert listed(cadence__exact=str(Cadence.YEARLY.value)) == [yearly]
    assert listed(q=str(far.starting_job_id)) == [far]
    page = admin_client.get(_changelist_url()).text
    assert '<td class="field-due_state">Overdue</td>' in page
    assert '<td class="field-due_state">Due soon</td>' in page
    assert '<td class="field-next_due_date">-</td>' in page


def test_the_superuser_adds_and_changes_subscriptions_on_the_form(
    admin_client, admin_user
):
    starting = make_repeatable_job(researcher=admin_user)
    add_url = reverse("admin:subscriptions_subscription_add")

    refused = admin_client.post(
        add_url,
        {
            "starting_job": make_ready_job(researcher=admin_user).pk,
            "cadence": Cadence.QUARTERLY,
            "start_date": "2026-07-01",
        },
    )
    added = admin_client.post(
        add_url,
        {
            "starting_job": starting.pk,
            "cadence": Cadence.QUARTERLY,
            "start_date": "2026-07-01",
            "_continue": "1",
        },
        follow=True,
    )
    subscription = Subscription.objects.get()
    unlocked_page = admin_client.get(_subscription_url(subscription)).text
    make_serving_job(
        subscription,
        make_repeat_job(starting, researcher=admin_user),
        date(2026, 10, 1),
    )
    locked_page = admin_client.get(_subscription_url(subscription)).text

    assert refused.status_code == 200
    assert lifecycle.STARTING_JOB_MESSAGE in refused.text
    assert added.redirect_chain[-1][0] == _subscription_url(subscription)
    assert [str(message) for message in added.context["messages"]] == [
        f'The subscription “<a href="{_subscription_url(subscription)}">Subscription '
        f"#{subscription.pk}</a>” was added successfully. You may edit it again below."
    ]
    assert subscription.created_by == admin_user
    assert 'name="cadence"' in unlocked_page
    assert 'name="start_date"' in unlocked_page
    for field_name in ("starting_job", "cadence", "start_date"):
        assert f'name="{field_name}"' not in locked_page
    assert 'name="end_date"' in locked_page


def test_the_subscription_page_lists_due_dates_with_their_jobs_and_reports(
    admin_client, client, admin_user
):
    today = timezone.localdate()
    starting = make_repeatable_job(researcher=admin_user)
    subscription = make_subscription(
        starting, created_by=admin_user, start_date=_months_before(today, 4)
    )
    served = make_repeatable_job(researcher=admin_user, predecessor=starting)
    final_report = make_report_row(served, ReportKind.FINAL)
    make_serving_job(subscription, served, _months_before(today, 1))
    client.force_login(make_staff_with("view_subscription", "view_researchjob"))
    download_url = reverse("admin:reports_report_download", args=(final_report.pk,))

    page = admin_client.get(_subscription_url(subscription)).text
    without_view_report = client.get(_subscription_url(subscription)).text

    assert "<h2>Due dates</h2>" in page
    assert (
        f'<td><a href="{_job_url(served)}">Research job #{served.pk}</a></td>'
        "\n                <td>Complete</td>" in page
    )
    assert "<td>Not prepared</td>" in page
    assert f"Report #{final_report.pk}" in page
    assert download_url in page
    assert f"Report #{final_report.pk}" in without_view_report
    assert download_url not in without_view_report


def test_researchers_view_subscriptions_read_only_and_prepare(client, admin_user):
    researcher = make_researcher_in_group()
    starting = make_repeatable_job(researcher=admin_user)
    subscription = make_subscription(
        starting, created_by=admin_user, start_date=date(2026, 7, 1)
    )
    client.force_login(researcher)

    changelist = client.get(_changelist_url())
    page = client.get(_subscription_url(subscription))
    changed = client.post(_subscription_url(subscription), {"cadence": Cadence.YEARLY})
    adding = client.get(reverse("admin:subscriptions_subscription_add"))
    prepared = client.post(_prepare_url(subscription), follow=True)

    repeat_job = ResearchJob.objects.get(predecessor=starting)
    assert changelist.status_code == 200
    assert reverse("admin:subscriptions_subscription_add") not in changelist.text
    assert page.status_code == 200
    assert SUBSCRIPTION_PREPARE_BUTTON in page.text
    assert 'name="cadence"' not in page.text
    assert changed.status_code == 403
    assert adding.status_code == 403
    assert prepared.redirect_chain[-1][0] == _job_url(repeat_job)
    assert repeat_job.created_by == researcher
    assert repeat_job.serving.subscription == subscription


def test_nobody_can_delete_a_subscription(rf, admin_client, admin_user):
    subscription = make_subscription(
        make_repeatable_job(researcher=admin_user),
        created_by=admin_user,
        start_date=date(2026, 7, 1),
    )
    request = rf.get("/admin/")
    request.user = admin_user

    assert (
        admin.site.get_model_admin(Subscription).has_delete_permission(
            request, subscription
        )
        is False
    )
    assert (
        admin_client.get(
            reverse("admin:subscriptions_subscription_delete", args=(subscription.pk,))
        ).status_code
        == 403
    )


def _subscription_with_delivered_due_date(user):
    today = timezone.localdate()
    starting = make_repeatable_job(researcher=user)
    subscription = make_subscription(
        starting, created_by=user, start_date=_months_before(today, 4)
    )
    served = make_repeatable_job(researcher=user, predecessor=starting)
    make_serving_job(subscription, served, _months_before(today, 1))


def test_the_subscription_list_runs_a_fixed_number_of_queries(
    admin_client, admin_user, django_assert_num_queries
):
    _subscription_with_delivered_due_date(admin_user)
    # A first request fills the caches every later request shares.
    admin_client.get(_changelist_url())
    with CaptureQueriesContext(connection) as small_list:
        assert admin_client.get(_changelist_url()).status_code == 200

    for _ in range(5):
        _subscription_with_delivered_due_date(admin_user)

    with django_assert_num_queries(len(small_list.captured_queries)):
        assert admin_client.get(_changelist_url()).status_code == 200
