"""Tests for the compared report each draft and final report of a repeat job records,
and where the admin shows it.
"""

import pytest
from django.urls import reverse

from axisence.reports import generation
from axisence.reports.models import ListedCompetitor, ReportKind
from axisence.research.models import JobCompetitor, JobPurpose, JobStatus
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    add_candidate,
    add_job_competitor,
    make_listed_report,
    make_ready_job,
    make_report_job,
    make_report_row,
    set_contact_settings,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_located_competitor,
    point_north_of,
)
from tests.subscriptions.factories import make_repeat_job

pytestmark = pytest.mark.django_db


@pytest.fixture
def researcher():
    return make_researcher()


def _repeat_of(predecessor, researcher, **fields):
    """Return a Researching repeat job listing the predecessor's competitor."""
    repeat_job = make_repeat_job(predecessor, researcher=researcher, **fields)
    add_job_competitor(
        repeat_job,
        JobCompetitor.objects.get(job=predecessor).competitor,
        distance_meters=0.2 * METERS_PER_MILE,
    )
    return repeat_job


def _predecessor_competitor(predecessor):
    return JobCompetitor.objects.get(job=predecessor).competitor


def test_reports_of_a_repeat_job_record_the_predecessors_latest_final_report_with_listings(  # noqa: E501
    researcher,
):
    predecessor = make_ready_job(researcher=researcher, status=JobStatus.COMPLETE)
    competitor = _predecessor_competitor(predecessor)
    make_listed_report(predecessor, main=[competitor])
    compared = make_listed_report(predecessor, main=[competitor])
    # Neither a final report without listed competitors nor a draft is compared with.
    make_listed_report(predecessor)
    make_listed_report(predecessor, kind=ReportKind.DRAFT, main=[competitor])
    repeat_job = _repeat_of(predecessor, researcher)

    draft = generation.generate_report(job=repeat_job, generated_by=researcher)
    repeat_job.status = JobStatus.COMPLETE
    repeat_job.save()
    final = generation.generate_report(job=repeat_job, generated_by=researcher)

    assert draft.compared_report == compared
    assert final.compared_report == compared


def test_other_reports_and_teasers_record_no_compared_report(researcher, settings):
    set_contact_settings(settings)
    job = make_ready_job(researcher=researcher)
    predecessor = make_ready_job(researcher=researcher, status=JobStatus.COMPLETE)
    make_listed_report(predecessor, main=[_predecessor_competitor(predecessor)])
    prospect = make_repeat_job(predecessor, researcher=researcher)
    prospect.purpose = JobPurpose.PROSPECT
    prospect.save()
    add_candidate(0.3)

    report = generation.generate_report(job=job, generated_by=researcher)
    teaser = generation.generate_teaser(job=prospect, generated_by=researcher)
    prospect.refresh_from_db()

    assert report.compared_report is None
    assert teaser.compared_report is None
    assert not ListedCompetitor.objects.filter(report=teaser).exists()
    assert prospect.predecessor == predecessor


def test_the_reports_list_and_page_show_the_compared_report(admin_client, researcher):
    predecessor = make_ready_job(researcher=researcher, status=JobStatus.COMPLETE)
    compared = make_listed_report(
        predecessor, main=[_predecessor_competitor(predecessor)]
    )
    report = make_report_row(
        make_repeat_job(predecessor, researcher=researcher), compared_report=compared
    )

    changelist = admin_client.get(reverse("admin:reports_report_changelist")).text
    change_page = admin_client.get(
        reverse("admin:reports_report_change", args=(report.pk,))
    ).text

    assert "Compared with" in changelist
    assert f'<td class="field-compared_with">Report #{compared.pk}</td>' in changelist
    # The compared report itself compares with nothing: the admin's empty value.
    assert '<td class="field-compared_with">-</td>' in changelist
    assert "Compared report" in change_page
    assert reverse("admin:reports_report_change", args=(compared.pk,)) in change_page


def test_the_job_pages_reports_section_shows_compared_with(admin_client, researcher):
    predecessor = make_report_job(researcher=researcher, status=JobStatus.COMPLETE)
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    compared = make_listed_report(predecessor, main=[competitor])
    repeat_job = make_repeat_job(predecessor, researcher=researcher)
    report = make_report_row(repeat_job, compared_report=compared)

    page = admin_client.get(
        reverse("admin:research_researchjob_change", args=(repeat_job.pk,))
    ).text

    assert "<th>Compared with</th>" in page
    assert f"<td>Report #{report.pk}</td>" in page
    assert f"<td>Report #{compared.pk}</td>" in page
