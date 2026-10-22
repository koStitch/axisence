"""Tests for generating, storing, and recording teasers, and for their refusals."""

import hashlib
import io
import logging
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from axisence.demographics.models import CensusVariable
from axisence.ingestion.models import IngestionSource
from axisence.reports import generation, rendering
from axisence.reports.errors import ReportGenerationError, ReportRefusedError
from axisence.reports.models import Report, ReportKind
from axisence.research.models import (
    JobCompetitor,
    JobPurpose,
    JobStatus,
    PermanentRecordError,
    ResearchJob,
)
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    SYNTHETIC_CONTACT_EMAIL,
    SYNTHETIC_CONTACT_PHONE,
    SYNTHETIC_WEBSITE_URL,
    add_candidate,
    add_job_competitor,
    make_area,
    make_census_2024,
    make_prospect_job,
    make_provider_run,
    make_ready_job,
    make_teaser_ready_job,
)
from tests.research.factories import BOROUGH_HALL, METERS_PER_MILE, make_job

pytestmark = pytest.mark.django_db

TWO_PAGES = (
    "<!doctype html><html><head><style>@page { size: letter portrait; }</style>"
    '</head><body><p style="break-after: page">1</p><p>2</p></body></html>'
)


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def census_area():
    area = make_area("11201", BOROUGH_HALL)
    make_census_2024(area)
    return area


def _stored_files(report_directory):
    if not report_directory.exists():
        return []
    return sorted(path.name for path in report_directory.iterdir())


def test_a_teaser_is_generated_stored_and_recorded(
    researcher, contact_settings, census_area, report_directory, caplog
):
    now = timezone.now()
    ny_state_run = make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    nyc_health_run = make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=3)
    )
    job = make_teaser_ready_job(researcher=researcher)

    with caplog.at_level(logging.INFO, logger="axisence.reports"):
        teaser = generation.generate_teaser(job=job, generated_by=researcher)

    teaser.refresh_from_db()
    stored = (report_directory / teaser.file_name).read_bytes()
    assert not JobCompetitor.objects.filter(job=job).exists()
    assert teaser.kind == ReportKind.TEASER
    assert teaser.job == job
    assert teaser.generated_by == researcher
    assert now <= teaser.generated_at <= timezone.now()
    assert teaser.census_vintage == 2024
    assert teaser.tabulation_area_code == "11201"
    assert teaser.ny_state_provider_run == ny_state_run
    assert teaser.nyc_health_provider_run == nyc_health_run
    # The job's factory loaded a transit run, which a teaser never reads.
    assert teaser.transit_run is None
    assert teaser.file_name == f"market-preview-{teaser.pk}.pdf"
    assert teaser.size_bytes == len(stored)
    assert teaser.sha256 == hashlib.sha256(stored).hexdigest()
    assert stored.startswith(b"%PDF-")
    assert f"Research job {job.pk} teaser {teaser.pk} generated" in [
        record.getMessage() for record in caplog.records
    ]
    assert generation.generated_message(teaser) == f"Teaser #{teaser.pk} generated."
    with pytest.raises(PermanentRecordError):
        teaser.save()
    with pytest.raises(PermanentRecordError):
        teaser.delete()


def test_teasers_and_reports_share_one_sequence_and_regenerating_keeps_both(
    researcher, contact_settings, report_directory
):
    prospect_job = make_teaser_ready_job(researcher=researcher)

    first_teaser = generation.generate_teaser(job=prospect_job, generated_by=researcher)
    report = generation.generate_report(
        job=make_ready_job(researcher=researcher), generated_by=researcher
    )
    second_teaser = generation.generate_teaser(
        job=prospect_job, generated_by=researcher
    )

    assert first_teaser.pk < report.pk < second_teaser.pk
    assert _stored_files(report_directory) == sorted(
        (first_teaser.file_name, report.file_name, second_teaser.file_name)
    )
    assert list(
        Report.objects.filter(kind=ReportKind.TEASER)
        .order_by("pk")
        .values_list("pk", flat=True)
    ) == [first_teaser.pk, second_teaser.pk]


@pytest.mark.parametrize(
    "status", [JobStatus.DRAFT, JobStatus.RESEARCHING, JobStatus.COMPLETE]
)
def test_every_eligible_status_produces_a_teaser(researcher, contact_settings, status):
    job = make_teaser_ready_job(researcher=researcher, status=status)

    teaser = generation.generate_teaser(job=job, generated_by=researcher)

    assert teaser.kind == ReportKind.TEASER


def test_refusals_come_in_order_and_leave_nothing(
    researcher, census_area, report_directory, settings, monkeypatch
):
    job = make_job(
        researcher=researcher,
        point=BOROUGH_HALL,
        confirmed=False,
        status=JobStatus.CANCELLED,
    )
    jobs = ResearchJob.objects.filter(pk=job.pk)
    labels = CensusVariable.objects.filter(vintage=2024, code="DP05_0005")
    expected_label = labels.get().estimate_label
    render_teaser_html = rendering.render_teaser_html
    recorded_numbers = []

    def two_page_html(content, *, number, contact):
        recorded_numbers.append(number)
        return TWO_PAGES

    def make_prospect():
        jobs.update(purpose=JobPurpose.PROSPECT)

    def reopen_as_draft():
        jobs.update(status=JobStatus.DRAFT)

    def confirm_target_point():
        jobs.update(
            target_point_confirmed_at=timezone.now(),
            target_point_confirmed_by=researcher,
        )

    def set_website_and_email():
        settings.AXISENCE_WEBSITE_URL = SYNTHETIC_WEBSITE_URL
        settings.AXISENCE_CONTACT_EMAIL = SYNTHETIC_CONTACT_EMAIL

    def set_phone():
        settings.AXISENCE_CONTACT_PHONE = SYNTHETIC_CONTACT_PHONE

    def add_candidate_and_change_label():
        add_candidate(0.2)
        labels.update(
            estimate_label="Estimate!!SEX AND AGE!!Total population!!Under 6 years"
        )

    def restore_label_and_render_two_pages():
        labels.update(estimate_label=expected_label)
        monkeypatch.setattr(rendering, "render_teaser_html", two_page_html)

    def render_one_page():
        monkeypatch.setattr(rendering, "render_teaser_html", render_teaser_html)

    steps = (
        ("Only prospect jobs produce a teaser.", make_prospect),
        ("A Cancelled job cannot produce a teaser.", reopen_as_draft),
        ("Confirm the target point first.", confirm_target_point),
        (
            "Axisence's contact details are not set: AXISENCE_WEBSITE_URL, "
            "AXISENCE_CONTACT_EMAIL, AXISENCE_CONTACT_PHONE.",
            set_website_and_email,
        ),
        (
            "Axisence's contact details are not set: AXISENCE_CONTACT_PHONE.",
            set_phone,
        ),
        (
            "No competitors of the job's kinds lie within its search radius.",
            add_candidate_and_change_label,
        ),
        (
            "The Census figure list for vintage 2024 does not match the stored labels.",
            restore_label_and_render_two_pages,
        ),
        ("The teaser does not fit on one page.", render_one_page),
    )

    for message, fix in steps:
        with pytest.raises(ReportRefusedError) as raised:
            generation.generate_teaser(job=job, generated_by=researcher)

        assert str(raised.value) == message
        for value in (
            SYNTHETIC_WEBSITE_URL,
            SYNTHETIC_CONTACT_EMAIL,
            SYNTHETIC_CONTACT_PHONE,
        ):
            assert value not in str(raised.value)
        assert not Report.objects.exists()
        assert _stored_files(report_directory) == []
        fix()

    teaser = generation.generate_teaser(job=job, generated_by=researcher)

    (refused_number,) = recorded_numbers
    assert teaser.pk > refused_number


def test_a_failure_logs_only_its_class_and_job_and_leaves_nothing(
    researcher, contact_settings, report_directory, caplog, monkeypatch
):
    job = make_teaser_ready_job(researcher=researcher)

    def failing_render(html):
        raise RuntimeError("synthetic content marker")

    monkeypatch.setattr(rendering, "render_one_letter_page_pdf", failing_render)

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ReportGenerationError) as raised,
    ):
        generation.generate_teaser(job=job, generated_by=researcher)

    assert str(raised.value) == "The teaser could not be generated."
    messages = [record.getMessage() for record in caplog.records]
    assert f"Research job {job.pk} teaser generation failed: RuntimeError" in messages
    assert not any("synthetic content marker" in message for message in messages)
    assert not Report.objects.exists()
    assert _stored_files(report_directory) == []


def test_a_prospect_job_switched_to_client_report_keeps_its_teasers(
    researcher, contact_settings
):
    job = make_prospect_job(researcher=researcher, status=JobStatus.RESEARCHING)
    add_job_competitor(job, add_candidate(0.2), distance_meters=0.2 * METERS_PER_MILE)
    generation.generate_teaser(job=job, generated_by=researcher)
    ResearchJob.objects.filter(pk=job.pk).update(purpose=JobPurpose.CLIENT_REPORT)

    report = generation.generate_report(job=job, generated_by=researcher)

    assert report.kind == ReportKind.DRAFT
    assert [report.kind for report in Report.objects.order_by("pk")] == [
        ReportKind.TEASER,
        ReportKind.DRAFT,
    ]
    with pytest.raises(ReportRefusedError) as raised:
        generation.generate_teaser(job=job, generated_by=researcher)
    assert str(raised.value) == "Only prospect jobs produce a teaser."


def test_the_command_generates_a_teaser(researcher, contact_settings, report_directory):
    job = make_teaser_ready_job(researcher=researcher)
    buffer = io.StringIO()

    call_command("generate_teaser", str(job.pk), stdout=buffer)

    teaser = Report.objects.get()
    assert buffer.getvalue().strip() == (
        f"Teaser #{teaser.pk} generated: market-preview-{teaser.pk}.pdf"
    )
    assert teaser.generated_by is None
    assert (report_directory / teaser.file_name).is_file()


@pytest.mark.parametrize("case", ["client report", "missing job"])
def test_the_command_fails_with_the_message(researcher, case):
    job = make_ready_job(researcher=researcher)
    if case == "client report":
        job_id = job.pk
        message = "Only prospect jobs produce a teaser."
    else:
        job_id = job.pk + 1000
        message = f"Research job {job_id} does not exist."

    with pytest.raises(CommandError) as raised:
        call_command("generate_teaser", str(job_id), stdout=io.StringIO())

    assert str(raised.value) == message
