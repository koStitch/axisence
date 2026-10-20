"""Generate a client report: check the rules, build and render it, store the file, and
record it.
"""

import logging

from django.db import connection, transaction
from django.utils import timezone

from axisence.accounts.models import User
from axisence.reports import content, rendering, storage
from axisence.reports.errors import ReportGenerationError, ReportRefusedError
from axisence.reports.models import Report, ReportKind
from axisence.research.models import JobPurpose, JobStatus, ResearchJob

logger = logging.getLogger(__name__)

NOT_CLIENT_REPORT_MESSAGE = "Only client report jobs produce a report."
GENERATION_FAILED_MESSAGE = "The report could not be generated."


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


def generate_report(*, job: ResearchJob, generated_by: User | None) -> Report:
    """Generate, store, and record a new report of the job, as of this moment.

    Raises ReportRefusedError when a rule refuses the generation, and
    ReportGenerationError for any other failure. Neither leaves a row or a file.
    """
    stored = ResearchJob.objects.get(pk=job.pk)
    kind = report_kind_for(stored)
    cut_off = timezone.now()
    stored_file: storage.StoredReportFile | None = None
    try:
        # A savepoint of its own, so a failure never rolls back the job page's
        # changes saved in the same request.
        with transaction.atomic():
            built = content.build_report(job=stored, kind=kind, cut_off=cut_off)
            number = _reserve_report_number()
            html = rendering.render_report_html(built.content, number=number)
            stored_file = storage.write_report_file(number, rendering.render_pdf(html))
            report = Report.objects.create(
                pk=number,
                job=stored,
                kind=kind,
                generated_at=cut_off,
                generated_by=generated_by,
                census_vintage=built.census_vintage,
                tabulation_area_code=built.tabulation_area_code,
                ny_state_provider_run=built.ny_state_provider_run,
                nyc_health_provider_run=built.nyc_health_provider_run,
                file_name=stored_file.file_name,
                size_bytes=stored_file.size_bytes,
                sha256=stored_file.sha256,
            )
    except ReportRefusedError:
        raise
    except BaseException as error:
        # The file of a report that was not recorded is removed, never kept.
        if stored_file is not None:
            stored_file.path.unlink(missing_ok=True)
        if not isinstance(error, Exception):
            raise
        # Only the class name: a message can quote report content.
        logger.error(
            "Research job %d report generation failed: %s",
            stored.pk,
            type(error).__name__,
        )
        raise ReportGenerationError(GENERATION_FAILED_MESSAGE) from None
    logger.info(
        "Research job %d report %d generated (%s)", stored.pk, report.pk, kind.value
    )
    return report


def generated_message(report: Report) -> str:
    if report.kind == ReportKind.DRAFT:
        return f"Draft report #{report.pk} generated."
    return f"Report #{report.pk} generated."


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
