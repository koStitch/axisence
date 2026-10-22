"""Generate client reports and teasers: check the rules, build and render the document,
store the file, and record it.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from django.db import connection, transaction
from django.utils import timezone

from axisence.accounts.models import User
from axisence.ingestion.models import IngestionRun
from axisence.reports import (
    content,
    listed_competitors,
    rendering,
    storage,
    teaser_content,
)
from axisence.reports.errors import (
    NotOneLetterPageError,
    ReportGenerationError,
    ReportRefusedError,
)
from axisence.reports.listed_competitors import ListedCompetitorRecord
from axisence.reports.models import Report, ReportKind
from axisence.research.jobs import UNCONFIRMED_POINT_MESSAGE
from axisence.research.models import JobPurpose, JobStatus, ResearchJob

logger = logging.getLogger(__name__)

NOT_CLIENT_REPORT_MESSAGE = "Only client report jobs produce a report."
GENERATION_FAILED_MESSAGE = "The report could not be generated."
NOT_PROSPECT_MESSAGE = "Only prospect jobs produce a teaser."
CANCELLED_TEASER_MESSAGE = "A Cancelled job cannot produce a teaser."
TEASER_NOT_ONE_PAGE_MESSAGE = "The teaser does not fit on one page."
TEASER_GENERATION_FAILED_MESSAGE = "The teaser could not be generated."


class _RecordedSources(Protocol):
    """What a built document records beside its file."""

    @property
    def census_vintage(self) -> int | None: ...

    @property
    def tabulation_area_code(self) -> str: ...

    @property
    def ny_state_provider_run(self) -> IngestionRun | None: ...

    @property
    def nyc_health_provider_run(self) -> IngestionRun | None: ...

    @property
    def transit_run(self) -> IngestionRun | None: ...

    @property
    def compared_report(self) -> Report | None: ...

    @property
    def listed_competitors(self) -> Sequence[ListedCompetitorRecord]: ...


def report_kind_for(job: ResearchJob) -> ReportKind:
    """Return the kind of report the job produces now, or refuse."""
    if job.purpose != JobPurpose.CLIENT_REPORT:
        raise ReportRefusedError(NOT_CLIENT_REPORT_MESSAGE)
    if job.status == JobStatus.RESEARCHING:
        return ReportKind.DRAFT
    if job.status == JobStatus.COMPLETE:
        return ReportKind.FINAL
    raise ReportRefusedError(
        f"A {JobStatus(job.status).label} job cannot produce a report."
    )


def is_eligible(job: ResearchJob) -> bool:
    try:
        report_kind_for(job)
    except ReportRefusedError:
        return False
    return True


def check_teaser_eligibility(job: ResearchJob) -> None:
    """Refuse a job that cannot produce a teaser now."""
    if job.purpose != JobPurpose.PROSPECT:
        raise ReportRefusedError(NOT_PROSPECT_MESSAGE)
    if job.status == JobStatus.CANCELLED:
        raise ReportRefusedError(CANCELLED_TEASER_MESSAGE)
    if not job.is_target_point_confirmed:
        raise ReportRefusedError(UNCONFIRMED_POINT_MESSAGE)


def is_teaser_eligible(job: ResearchJob) -> bool:
    try:
        check_teaser_eligibility(job)
    except ReportRefusedError:
        return False
    return True


def generate_report(*, job: ResearchJob, generated_by: User | None) -> Report:
    """Generate, store, and record a new report of the job, as of this moment.

    Raises ReportRefusedError when a rule refuses the generation, and
    ReportGenerationError for any other failure. Neither leaves a row or a file.
    """
    stored = ResearchJob.objects.get(pk=job.pk)
    kind = report_kind_for(stored)

    def build(cut_off: datetime) -> content.BuiltReport:
        return content.build_report(job=stored, kind=kind, cut_off=cut_off)

    def render(built: content.BuiltReport, number: int) -> bytes:
        return rendering.render_pdf(
            rendering.render_report_html(built.content, number=number)
        )

    report = _generate(
        job=stored,
        kind=kind,
        generated_by=generated_by,
        build=build,
        render=render,
        file_name_prefix=storage.REPORT_FILE_NAME_PREFIX,
        document_name="report",
        failure_message=GENERATION_FAILED_MESSAGE,
    )
    logger.info(
        "Research job %d report %d generated (%s)", stored.pk, report.pk, kind.value
    )
    return report


def generate_teaser(*, job: ResearchJob, generated_by: User | None) -> Report:
    """Generate, store, and record a new teaser of the job, as of this moment.

    Raises ReportRefusedError when a rule refuses the generation, and
    ReportGenerationError for any other failure. Neither leaves a row or a file.
    """
    stored = ResearchJob.objects.get(pk=job.pk)
    check_teaser_eligibility(stored)
    contact = teaser_content.load_contact_details()

    def build(cut_off: datetime) -> teaser_content.BuiltTeaser:
        return teaser_content.build_teaser(job=stored, cut_off=cut_off)

    def render(built: teaser_content.BuiltTeaser, number: int) -> bytes:
        html = rendering.render_teaser_html(
            built.content, number=number, contact=contact
        )
        try:
            return rendering.render_one_letter_page_pdf(html)
        except NotOneLetterPageError:
            raise ReportRefusedError(TEASER_NOT_ONE_PAGE_MESSAGE) from None

    teaser = _generate(
        job=stored,
        kind=ReportKind.TEASER,
        generated_by=generated_by,
        build=build,
        render=render,
        file_name_prefix=storage.TEASER_FILE_NAME_PREFIX,
        document_name="teaser",
        failure_message=TEASER_GENERATION_FAILED_MESSAGE,
    )
    logger.info("Research job %d teaser %d generated", stored.pk, teaser.pk)
    return teaser


def generated_message(report: Report) -> str:
    if report.kind == ReportKind.TEASER:
        return f"Teaser #{report.pk} generated."
    if report.kind == ReportKind.DRAFT:
        return f"Draft report #{report.pk} generated."
    return f"Report #{report.pk} generated."


def _generate[BuiltT: _RecordedSources](
    *,
    job: ResearchJob,
    kind: ReportKind,
    generated_by: User | None,
    build: Callable[[datetime], BuiltT],
    render: Callable[[BuiltT, int], bytes],
    file_name_prefix: str,
    document_name: str,
    failure_message: str,
) -> Report:
    """Build, number, render, store, and record a document as of this moment.

    A refusal passes through; any other failure removes the stored file, logs only the
    job number and the error's class name, and raises ReportGenerationError with the
    failure message.
    """
    cut_off = timezone.now()
    stored_file: storage.StoredReportFile | None = None
    try:
        # A savepoint of its own, so a failure never rolls back the job page's
        # changes saved in the same request.
        with transaction.atomic():
            built = build(cut_off)
            number = _reserve_report_number()
            stored_file = storage.write_report_file(
                number, render(built, number), file_name_prefix=file_name_prefix
            )
            report = Report.objects.create(
                pk=number,
                job=job,
                kind=kind,
                generated_at=cut_off,
                generated_by=generated_by,
                compared_report=built.compared_report,
                census_vintage=built.census_vintage,
                tabulation_area_code=built.tabulation_area_code,
                ny_state_provider_run=built.ny_state_provider_run,
                nyc_health_provider_run=built.nyc_health_provider_run,
                transit_run=built.transit_run,
                file_name=stored_file.file_name,
                size_bytes=stored_file.size_bytes,
                sha256=stored_file.sha256,
            )
            listed_competitors.store_listed_competitors(
                report, built.listed_competitors
            )
    except ReportRefusedError:
        raise
    except BaseException as error:
        # The file of a document that was not recorded is removed, never kept.
        if stored_file is not None:
            stored_file.path.unlink(missing_ok=True)
        if not isinstance(error, Exception):
            raise
        # Only the class name: a message can quote the document's content.
        logger.error(
            "Research job %d %s generation failed: %s",
            job.pk,
            document_name,
            type(error).__name__,
        )
        raise ReportGenerationError(failure_message) from None
    return report


def _reserve_report_number() -> int:
    # Every page prints the number, so it is taken before rendering. A failure after
    # this point leaves the number unused, as PostgreSQL never returns sequence
    # values.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT nextval(pg_get_serial_sequence(%s, %s))",
            [Report._meta.db_table, "id"],
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("The report number sequence returned no value.")
    return int(row[0])
