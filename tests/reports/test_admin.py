"""Tests for the report admin pages and the research job page's report additions."""

import pytest
from django.contrib import admin
from django.urls import reverse

from axisence.reports import content, storage
from axisence.reports.models import Report, ReportKind
from axisence.research.models import JobPurpose, JobStatus
from tests.admin_forms import change_form_data
from tests.reports.factories import (
    JOB_PERMISSIONS,
    make_ready_job,
    make_report_job,
    make_report_row,
    make_staff_with,
)

pytestmark = pytest.mark.django_db

GENERATE_BUTTON = 'name="_generate_report"'


def _job_url(job):
    return reverse("admin:research_researchjob_change", args=(job.pk,))


def _download_url(report):
    return reverse("admin:reports_report_download", args=(report.pk,))


BUTTON_CASES: tuple[tuple[str, dict[str, str], bool, bool], ...] = (
    ("researching", {}, True, True),
    ("complete", {"status": JobStatus.COMPLETE}, True, True),
    ("prospect", {"purpose": JobPurpose.PROSPECT}, True, False),
    ("draft", {"status": JobStatus.DRAFT}, True, False),
    ("cancelled", {"status": JobStatus.CANCELLED}, True, False),
    ("without the permission", {}, False, False),
)


@pytest.mark.parametrize(
    ("fields", "is_superuser", "shown"),
    [(fields, is_superuser, shown) for _, fields, is_superuser, shown in BUTTON_CASES],
    ids=[name for name, *_ in BUTTON_CASES],
)
def test_the_button_appears_only_for_eligible_jobs_and_permitted_users(
    client, admin_user, fields, is_superuser, shown
):
    job = make_ready_job(researcher=admin_user, **fields)
    client.force_login(
        admin_user if is_superuser else make_staff_with(*JOB_PERMISSIONS)
    )

    page = client.get(_job_url(job))

    assert page.status_code == 200
    assert (GENERATE_BUTTON in page.text) is shown


def test_the_button_generates_after_saving_the_page(
    admin_client, admin_user, monkeypatch
):
    job = make_ready_job(researcher=admin_user)
    built_reports = []
    build_report = content.build_report

    def recording_build_report(**arguments):
        built = build_report(**arguments)
        built_reports.append(built)
        return built

    monkeypatch.setattr(content, "build_report", recording_build_report)
    url = _job_url(job)

    response = admin_client.post(
        url,
        change_form_data(
            admin_client,
            url,
            **{
                "job_competitors-0-research_status": "unreachable",
                "_generate_report": "1",
            },
        ),
    )

    report = Report.objects.get()
    assert response.status_code == 302
    assert response.url == url
    (built,) = built_reports
    assert built.content.competitors[0].research_outcome.startswith("Not reached")
    assert f"Draft report #{report.pk} generated." in admin_client.get(url).text


def test_a_refused_generation_shows_its_message(admin_client, admin_user):
    job = make_report_job(researcher=admin_user)
    url = _job_url(job)

    response = admin_client.post(
        url, change_form_data(admin_client, url, _generate_report="1"), follow=True
    )

    assert "This job lists no competitors." in response.text
    assert not Report.objects.exists()


def test_a_post_without_the_permission_is_refused(client, admin_user):
    job = make_ready_job(researcher=admin_user)
    client.force_login(make_staff_with(*JOB_PERMISSIONS))
    url = _job_url(job)

    response = client.post(url, change_form_data(client, url, _generate_report="1"))

    assert response.status_code == 403
    assert not Report.objects.exists()


def test_the_reports_section_lists_reports_newest_first(
    admin_client, client, admin_user
):
    job = make_ready_job(researcher=admin_user)
    older = make_report_row(job, ReportKind.DRAFT)
    newer = make_report_row(job, ReportKind.FINAL)
    client.force_login(make_staff_with(*JOB_PERMISSIONS))

    page = admin_client.get(_job_url(job)).text
    without_view_permission = client.get(_job_url(job)).text

    assert page.index(f"<td>Report #{newer.pk}</td>") < page.index(
        f"<td>Report #{older.pk}</td>"
    )
    assert "<td>Final</td>" in page
    assert "<td>Draft</td>" in page
    assert _download_url(newer) in page
    assert f"<td>Report #{newer.pk}</td>" in without_view_permission
    assert _download_url(newer) not in without_view_permission


def test_the_reports_page_renders_filters_and_searches(admin_client, admin_user):
    job = make_ready_job(researcher=admin_user)
    report = make_report_row(job)
    make_report_row(make_ready_job(researcher=admin_user))

    changelist = admin_client.get(
        reverse("admin:reports_report_changelist"),
        {"q": str(job.pk), "kind__exact": "draft"},
    )
    unmatched_search = admin_client.get(
        reverse("admin:reports_report_changelist"), {"q": "not a number"}
    )
    change_page = admin_client.get(
        reverse("admin:reports_report_change", args=(report.pk,))
    )

    assert changelist.status_code == 200
    assert changelist.context["cl"].result_count == 1
    assert str(report) in changelist.text
    assert unmatched_search.context["cl"].result_count == 0
    assert change_page.status_code == 200
    assert _download_url(report) in change_page.text


def test_reports_cannot_be_added_changed_or_deleted_in_the_admin(
    rf, admin_client, admin_user
):
    report = make_report_row(make_ready_job(researcher=admin_user))
    request = rf.get("/admin/")
    request.user = admin_user
    model_admin = admin.site.get_model_admin(Report)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, report) is False
    assert model_admin.has_delete_permission(request, report) is False
    assert (
        admin_client.post(
            reverse("admin:reports_report_change", args=(report.pk,)), {}
        ).status_code
        == 403
    )
    assert (
        admin_client.get(
            reverse("admin:reports_report_delete", args=(report.pk,))
        ).status_code
        == 403
    )


def test_the_download_serves_the_stored_pdf(admin_client, client, admin_user):
    stored = storage.write_report_file(
        41, b"%PDF-synthetic", file_name_prefix=storage.REPORT_FILE_NAME_PREFIX
    )
    report = make_report_row(
        make_ready_job(researcher=admin_user),
        pk=41,
        file_name=stored.file_name,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    client.force_login(make_staff_with(*JOB_PERMISSIONS))

    response = admin_client.get(_download_url(report))
    # Reading the stream to its end closes the response and its file.
    body = b"".join(response.streaming_content)
    refused = client.get(_download_url(report))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment")
    assert 'filename="report-41.pdf"' in response["Content-Disposition"]
    assert body == b"%PDF-synthetic"
    assert refused.status_code == 403


def test_a_missing_file_shows_a_message_and_returns(admin_client, admin_user):
    job = make_ready_job(researcher=admin_user)
    report = make_report_row(job)
    job_page = f"http://testserver{_job_url(job)}"

    response = admin_client.get(_download_url(report), HTTP_REFERER=job_page)

    assert response.status_code == 302
    assert response.url == job_page
    assert (
        f"The file of report #{report.pk} is no longer stored."
        in admin_client.get(_job_url(job)).text
    )
