"""Tests for generating, storing, and recording reports, and for their refusals."""

import hashlib
import io
import logging
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from axisence.demographics.models import CensusVariable
from axisence.ingestion.models import IngestionOutcome, IngestionSource
from axisence.reports import generation, rendering, storage
from axisence.reports.errors import ReportGenerationError, ReportRefusedError
from axisence.reports.models import Report, ReportKind
from axisence.research import jobs
from axisence.research.merges import MergeChains
from axisence.research.models import (
    JobCompetitor,
    JobPurpose,
    JobStatus,
    PermanentRecordError,
)
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    make_area,
    make_census_2024,
    make_provider_run,
    make_ready_job,
)
from tests.research.factories import BOROUGH_HALL

pytestmark = pytest.mark.django_db


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


def test_researching_gives_a_draft_and_complete_a_final_report(
    researcher, census_area, report_directory, caplog
):
    now = timezone.now()
    ny_state_run = make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS, finished_at=now - timedelta(days=2)
    )
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS,
        finished_at=now - timedelta(days=1),
        outcome=IngestionOutcome.FAILED,
    )
    make_provider_run(
        IngestionSource.NY_STATE_PROVIDERS, finished_at=now + timedelta(days=1)
    )
    nyc_health_run = make_provider_run(
        IngestionSource.NYC_HEALTH_PROVIDERS, finished_at=now - timedelta(days=3)
    )
    job = make_ready_job(researcher=researcher)

    with caplog.at_level(logging.INFO, logger="axisence.reports"):
        draft = generation.generate_report(job=job, generated_by=researcher)
    jobs.change_status(job=job, status=JobStatus.COMPLETE, chains=MergeChains.load())
    final = generation.generate_report(job=job, generated_by=researcher)

    assert [report.kind for report in Report.objects.order_by("pk")] == [
        ReportKind.DRAFT,
        ReportKind.FINAL,
    ]
    assert final.pk > draft.pk
    assert generation.generated_message(draft) == f"Draft report #{draft.pk} generated."
    assert generation.generated_message(final) == f"Report #{final.pk} generated."
    draft.refresh_from_db()
    stored = (report_directory / draft.file_name).read_bytes()
    assert draft.job == job
    assert draft.generated_by == researcher
    assert now <= draft.generated_at <= timezone.now()
    assert draft.census_vintage == 2024
    assert draft.tabulation_area_code == "11201"
    assert draft.ny_state_provider_run == ny_state_run
    assert draft.nyc_health_provider_run == nyc_health_run
    assert draft.file_name == f"report-{draft.pk}.pdf"
    assert draft.size_bytes == len(stored)
    assert draft.sha256 == hashlib.sha256(stored).hexdigest()
    assert f"Research job {job.pk} report {draft.pk} generated (draft)" in [
        record.getMessage() for record in caplog.records
    ]


def test_generating_again_creates_a_new_report(researcher, report_directory):
    job = make_ready_job(researcher=researcher)

    first = generation.generate_report(job=job, generated_by=researcher)
    second = generation.generate_report(job=job, generated_by=researcher)

    assert first.pk != second.pk
    assert _stored_files(report_directory) == sorted(
        (first.file_name, second.file_name)
    )


def _prospect(job):
    job.purpose = JobPurpose.PROSPECT
    job.save()


def _status(status):
    def change(job):
        job.status = status
        job.save()

    return change


def _without_competitors(job):
    # Job competitors have no deletion guard; the job keeps its own record.
    JobCompetitor.objects.filter(job=job).delete()


def _changed_label(job):
    CensusVariable.objects.filter(vintage=2024, code="DP05_0005").update(
        estimate_label="Estimate!!SEX AND AGE!!Total population!!Under 6 years"
    )


REFUSALS = (
    ("prospect", _prospect, "Only client report jobs produce a report."),
    ("draft", _status(JobStatus.DRAFT), "A Draft job cannot produce a report."),
    (
        "cancelled",
        _status(JobStatus.CANCELLED),
        "A Cancelled job cannot produce a report.",
    ),
    ("no competitors", _without_competitors, "This job lists no competitors."),
    (
        "changed label",
        _changed_label,
        "The Census figure list for vintage 2024 does not match the stored labels.",
    ),
)


@pytest.mark.parametrize(
    ("change", "message"),
    [(change, message) for _, change, message in REFUSALS],
    ids=[name for name, *_ in REFUSALS],
)
def test_refusals_name_what_to_fix_and_leave_nothing(
    researcher, census_area, report_directory, change, message
):
    job = make_ready_job(researcher=researcher)
    change(job)

    with pytest.raises(ReportRefusedError) as raised:
        generation.generate_report(job=job, generated_by=researcher)

    assert str(raised.value) == message
    assert not Report.objects.exists()
    assert _stored_files(report_directory) == []


def test_a_failure_logs_only_its_class_and_job_and_leaves_nothing(
    researcher, report_directory, caplog, monkeypatch
):
    job = make_ready_job(researcher=researcher)

    def failing_render_pdf(html):
        raise RuntimeError("synthetic content marker")

    monkeypatch.setattr(rendering, "render_pdf", failing_render_pdf)

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ReportGenerationError) as raised,
    ):
        generation.generate_report(job=job, generated_by=researcher)

    assert str(raised.value) == "The report could not be generated."
    messages = [record.getMessage() for record in caplog.records]
    assert f"Research job {job.pk} report generation failed: RuntimeError" in messages
    assert not any("synthetic content marker" in message for message in messages)
    assert not Report.objects.exists()
    assert _stored_files(report_directory) == []


def test_a_database_failure_after_writing_removes_the_file(
    researcher, report_directory, monkeypatch
):
    job = make_ready_job(researcher=researcher)
    Report.objects.create(
        pk=900000,
        job=job,
        kind=ReportKind.DRAFT,
        generated_at=timezone.now(),
        file_name="report-existing.pdf",
        size_bytes=0,
        sha256="0" * 64,
    )
    monkeypatch.setattr(generation, "_reserve_report_number", lambda: 900000)

    with pytest.raises(ReportGenerationError):
        generation.generate_report(job=job, generated_by=researcher)

    assert not (report_directory / "report-900000.pdf").exists()
    # The failed insert rolled back only its own savepoint.
    assert Report.objects.count() == 1


def test_an_existing_report_file_is_never_overwritten(report_directory):
    report_directory.mkdir(parents=True)
    existing = report_directory / "report-7.pdf"
    existing.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        storage.write_report_file(7, b"new")

    assert existing.read_bytes() == b"old"
    assert _stored_files(report_directory) == ["report-7.pdf"]


def test_reports_are_never_changed_or_deleted(researcher):
    job = make_ready_job(researcher=researcher)
    report = Report.objects.create(
        job=job,
        kind=ReportKind.DRAFT,
        generated_at=timezone.now(),
        file_name="report-synthetic.pdf",
        size_bytes=0,
        sha256="0" * 64,
    )
    report.kind = ReportKind.FINAL

    with pytest.raises(PermanentRecordError):
        report.save()
    with pytest.raises(PermanentRecordError):
        report.delete()
    with pytest.raises(PermanentRecordError):
        Report.objects.update(kind=ReportKind.FINAL)
    with pytest.raises(PermanentRecordError):
        Report.objects.all().delete()
    assert Report.objects.get().kind == ReportKind.DRAFT
    # Last, because bulk_update's own transaction marks the test's for rollback.
    with pytest.raises(PermanentRecordError):
        Report.objects.bulk_update([report], ["kind"])


def test_the_command_generates_a_report(researcher, report_directory):
    job = make_ready_job(researcher=researcher)
    buffer = io.StringIO()

    call_command("generate_report", str(job.pk), stdout=buffer)

    report = Report.objects.get()
    assert buffer.getvalue().strip() == (
        f"Report #{report.pk} generated: report-{report.pk}.pdf"
    )
    assert report.generated_by is None
    assert (report_directory / report.file_name).is_file()


@pytest.mark.parametrize("case", ["prospect", "missing job"])
def test_the_command_fails_with_the_message(researcher, case):
    job = make_ready_job(researcher=researcher)
    if case == "prospect":
        _prospect(job)
        job_id = job.pk
        message = "Only client report jobs produce a report."
    else:
        job_id = job.pk + 1000
        message = f"Research job {job_id} does not exist."

    with pytest.raises(CommandError) as raised:
        call_command("generate_report", str(job_id), stdout=io.StringIO())

    assert str(raised.value) == message
