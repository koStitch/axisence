"""Tests for the research job page's teaser button and the teasers in the admin."""

import logging

import pytest
from django.urls import reverse
from django.utils.html import escape

from axisence.reports import generation, rendering, storage, teaser_content
from axisence.reports.models import Report, ReportKind
from axisence.research.models import JobPurpose, JobStatus
from tests.admin_forms import change_form_data
from tests.reports.factories import (
    JOB_PERMISSIONS,
    make_prospect_job,
    make_ready_job,
    make_report_row,
    make_staff_with,
    make_teaser_ready_job,
)

pytestmark = pytest.mark.django_db

TEASER_BUTTON = 'name="_generate_teaser"'


def _job_url(job):
    return reverse("admin:research_researchjob_change", args=(job.pk,))


def _download_url(report):
    return reverse("admin:reports_report_download", args=(report.pk,))


BUTTON_CASES: tuple[tuple[str, dict[str, object], bool, bool], ...] = (
    ("draft", {}, True, True),
    ("researching", {"status": JobStatus.RESEARCHING}, True, True),
    ("complete", {"status": JobStatus.COMPLETE}, True, True),
    ("cancelled", {"status": JobStatus.CANCELLED}, True, False),
    (
        "unconfirmed",
        {"target_point_confirmed_at": None, "target_point_confirmed_by": None},
        True,
        False,
    ),
    (
        "client report",
        {"purpose": JobPurpose.CLIENT_REPORT, "status": JobStatus.RESEARCHING},
        True,
        False,
    ),
    ("without the permission", {}, False, False),
)


@pytest.mark.parametrize(
    ("fields", "is_superuser", "shown"),
    [(fields, is_superuser, shown) for _, fields, is_superuser, shown in BUTTON_CASES],
    ids=[name for name, *_ in BUTTON_CASES],
)
def test_the_teaser_button_appears_only_for_eligible_jobs_and_permitted_users(
    client, admin_user, fields, is_superuser, shown
):
    # No candidates and blank contact settings: the button depends on neither.
    job = make_prospect_job(researcher=admin_user, **fields)
    client.force_login(
        admin_user if is_superuser else make_staff_with(*JOB_PERMISSIONS)
    )

    page = client.get(_job_url(job))

    assert page.status_code == 200
    assert (TEASER_BUTTON in page.text) is shown


def test_the_teaser_button_generates_after_saving_the_page(
    admin_client, admin_user, contact_settings, monkeypatch
):
    job = make_teaser_ready_job(researcher=admin_user)
    built_teasers = []
    build_teaser = teaser_content.build_teaser

    def recording_build_teaser(**arguments):
        built = build_teaser(**arguments)
        built_teasers.append(built)
        return built

    monkeypatch.setattr(teaser_content, "build_teaser", recording_build_teaser)
    url = _job_url(job)

    response = admin_client.post(
        url,
        change_form_data(
            admin_client,
            url,
            organization_name="Synthetic Renamed Prospect",
            _generate_teaser="1",
        ),
    )

    teaser = Report.objects.get()
    assert response.status_code == 302
    assert response.url == url
    (built,) = built_teasers
    assert built.content.organization_name == "Synthetic Renamed Prospect"
    page = admin_client.get(url).text
    assert f"Teaser #{teaser.pk} generated." in page
    assert "<td>Teaser</td>" in page
    assert _download_url(teaser) in page


def test_a_refused_teaser_shows_its_message(admin_client, admin_user):
    job = make_teaser_ready_job(researcher=admin_user)
    url = _job_url(job)

    response = admin_client.post(
        url, change_form_data(admin_client, url, _generate_teaser="1"), follow=True
    )

    assert (
        escape(
            "Axisence's contact details are not set: AXISENCE_WEBSITE_URL, "
            "AXISENCE_CONTACT_EMAIL, AXISENCE_CONTACT_PHONE."
        )
        in response.text
    )
    assert not Report.objects.exists()


def test_a_failed_teaser_keeps_the_page_changes_and_leaves_nothing(
    admin_client, admin_user, contact_settings, report_directory, caplog, monkeypatch
):
    job = make_teaser_ready_job(researcher=admin_user)
    url = _job_url(job)
    render_teaser_html = rendering.render_teaser_html
    render_one_letter_page_pdf = rendering.render_one_letter_page_pdf
    reserved_numbers = []

    def recording_render_teaser_html(content, *, number, contact):
        reserved_numbers.append(number)
        return render_teaser_html(content, number=number, contact=contact)

    def failing_render(html):
        raise RuntimeError("synthetic content marker")

    monkeypatch.setattr(rendering, "render_teaser_html", recording_render_teaser_html)
    monkeypatch.setattr(rendering, "render_one_letter_page_pdf", failing_render)

    with caplog.at_level(logging.DEBUG):
        response = admin_client.post(
            url,
            change_form_data(
                admin_client,
                url,
                organization_name="Synthetic Renamed Prospect",
                _generate_teaser="1",
            ),
        )

    assert response.status_code == 302
    assert response.url == url
    assert "The teaser could not be generated." in admin_client.get(url).text
    job.refresh_from_db()
    assert job.organization_name == "Synthetic Renamed Prospect"
    assert not Report.objects.exists()
    assert not report_directory.exists() or not any(report_directory.iterdir())
    messages = [record.getMessage() for record in caplog.records]
    assert f"Research job {job.pk} teaser generation failed: RuntimeError" in messages
    assert not any("synthetic content marker" in message for message in messages)
    # The number reserved for the failed teaser stays unused.
    monkeypatch.setattr(
        rendering, "render_one_letter_page_pdf", render_one_letter_page_pdf
    )
    (reserved_number,) = reserved_numbers
    assert generation.generate_teaser(job=job, generated_by=admin_user).pk > (
        reserved_number
    )


def test_a_teaser_post_without_the_permission_is_refused(
    client, admin_user, contact_settings
):
    job = make_teaser_ready_job(researcher=admin_user)
    client.force_login(make_staff_with(*JOB_PERMISSIONS, "view_report"))
    url = _job_url(job)

    response = client.post(url, change_form_data(client, url, _generate_teaser="1"))

    assert response.status_code == 403
    assert not Report.objects.exists()


def test_the_reports_list_shows_and_filters_teasers(admin_client, admin_user):
    make_report_row(make_prospect_job(researcher=admin_user), ReportKind.TEASER)
    make_report_row(make_ready_job(researcher=admin_user), ReportKind.DRAFT)
    changelist_url = reverse("admin:reports_report_changelist")

    filtered = admin_client.get(changelist_url, {"kind__exact": "teaser"})
    unfiltered = admin_client.get(changelist_url)

    assert filtered.context["cl"].result_count == 1
    assert "Teaser" in filtered.text
    assert "?kind__exact=teaser" in unfiltered.text


def test_a_teaser_downloads_under_its_stored_file_name(admin_client, admin_user):
    stored = storage.write_report_file(
        41, b"%PDF-synthetic", file_name_prefix=storage.TEASER_FILE_NAME_PREFIX
    )
    teaser = make_report_row(
        make_prospect_job(researcher=admin_user),
        ReportKind.TEASER,
        pk=41,
        file_name=stored.file_name,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )

    response = admin_client.get(_download_url(teaser))
    # Reading the stream to its end closes the response and its file.
    body = b"".join(response.streaming_content)

    assert response.status_code == 200
    assert 'filename="market-preview-41.pdf"' in response["Content-Disposition"]
    assert body == b"%PDF-synthetic"
