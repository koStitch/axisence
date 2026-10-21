"""Tests for the report permissions and what researchers can do with reports."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from axisence.reports.models import Report
from tests.admin_forms import change_form_data
from tests.reports.factories import make_ready_job, make_teaser_ready_job
from tests.research.factories import make_researcher_in_group

pytestmark = pytest.mark.django_db


def test_the_report_model_has_only_view_and_generate_permissions():
    assert set(
        Permission.objects.filter(content_type__app_label="reports").values_list(
            "codename", flat=True
        )
    ) == {"view_report", "generate_report", "generate_teaser"}


def test_a_researcher_generates_and_downloads_a_report(client):
    researcher = make_researcher_in_group()
    client.force_login(researcher)
    job = make_ready_job(researcher=researcher)
    url = reverse("admin:research_researchjob_change", args=(job.pk,))

    page = client.get(url)
    generated = client.post(url, change_form_data(client, url, _generate_report="1"))
    report = Report.objects.get()
    download = client.get(reverse("admin:reports_report_download", args=(report.pk,)))
    # Reading the stream to its end closes the response and its file.
    body = b"".join(download.streaming_content)

    assert 'name="_generate_report"' in page.text
    assert generated.status_code == 302
    assert report.generated_by == researcher
    assert download.status_code == 200
    assert body.startswith(b"%PDF-")


def test_a_researcher_generates_and_downloads_a_teaser(client, contact_settings):
    researcher = make_researcher_in_group()
    client.force_login(researcher)
    job = make_teaser_ready_job(researcher=researcher)
    url = reverse("admin:research_researchjob_change", args=(job.pk,))

    page = client.get(url)
    generated = client.post(url, change_form_data(client, url, _generate_teaser="1"))
    teaser = Report.objects.get()
    download = client.get(reverse("admin:reports_report_download", args=(teaser.pk,)))
    # Reading the stream to its end closes the response and its file.
    body = b"".join(download.streaming_content)

    assert 'name="_generate_teaser"' in page.text
    assert generated.status_code == 302
    assert teaser.generated_by == researcher
    assert download.status_code == 200
    assert body.startswith(b"%PDF-")
    assert (
        f'filename="market-preview-{teaser.pk}.pdf"'
        in (download["Content-Disposition"])
    )
