"""The competitors a report lists, recorded with it, and what later reports and repeat
jobs read from them: the compared report and a repeat job's first selection.
"""

import dataclasses
from collections.abc import Sequence

from django.db.models import Exists, OuterRef

from axisence.providers.models import Competitor
from axisence.reports.competitors import LoadedCompetitor
from axisence.reports.models import ListedCompetitor, Report, ReportKind, ReportListing
from axisence.research.current_values import load_current_operating_statuses
from axisence.research.merges import MergeChains
from axisence.research.models import OperatingStatus, ResearchJob

NO_COMPARED_REPORT_MESSAGE = (
    "A repeat job's predecessor always has a final report with listed competitors."
)


@dataclasses.dataclass(frozen=True, slots=True)
class ListedCompetitorRecord:
    competitor_id: int
    listing: ReportListing
    number: int | None
    distance_meters: float | None


def records_of(
    main: Sequence[LoadedCompetitor], closed: Sequence[LoadedCompetitor]
) -> tuple[ListedCompetitorRecord, ...]:
    """Return the main list numbered from 1 in its order, then the reported-closed
    list without numbers.
    """
    return (
        *(
            ListedCompetitorRecord(
                competitor_id=loaded.displayed.competitor.pk,
                listing=ReportListing.MAIN,
                number=number,
                distance_meters=loaded.displayed.distance_meters,
            )
            for number, loaded in enumerate(main, start=1)
        ),
        *(
            ListedCompetitorRecord(
                competitor_id=loaded.displayed.competitor.pk,
                listing=ReportListing.REPORTED_CLOSED,
                number=None,
                distance_meters=loaded.displayed.distance_meters,
            )
            for loaded in closed
        ),
    )


def store_listed_competitors(
    report: Report, records: Sequence[ListedCompetitorRecord]
) -> None:
    """Record the report's listed competitors in one query."""
    ListedCompetitor.objects.bulk_create(
        [
            ListedCompetitor(
                report=report,
                competitor_id=record.competitor_id,
                listing=record.listing,
                number=record.number,
                distance_meters=record.distance_meters,
            )
            for record in records
        ]
    )


def latest_final_report_with_listings(job_id: int) -> Report | None:
    """Return the job's highest-numbered final report that recorded its listed
    competitors, with its job.
    """
    return (
        Report.objects.filter(job_id=job_id, kind=ReportKind.FINAL)
        .filter(Exists(ListedCompetitor.objects.filter(report=OuterRef("pk"))))
        .select_related("job")
        .order_by("-pk")
        .first()
    )


def carried_over_competitor_ids(
    job: ResearchJob, chains: MergeChains
) -> frozenset[int]:
    """Return the competitors a repeat job's first search ticks besides the nearest
    ones: those its predecessor's latest final report listed in its main list,
    resolved through merges, active, not retired, not the job's own competitor, and
    not reported permanently closed now.
    """
    if job.predecessor_id is None:
        return frozenset()
    report = latest_final_report_with_listings(job.predecessor_id)
    if report is None:
        raise RuntimeError(NO_COMPARED_REPORT_MESSAGE)
    resolved_ids = {
        chains.current_id(competitor_id)
        for competitor_id in ListedCompetitor.objects.filter(
            report=report, listing=ReportListing.MAIN
        ).values_list("competitor_id", flat=True)
    }
    if job.own_competitor_id is not None:
        resolved_ids.discard(chains.current_id(job.own_competitor_id))
    current_ids = frozenset(
        Competitor.objects.filter(
            pk__in=resolved_ids, is_active=True, retired_at__isnull=True
        ).values_list("pk", flat=True)
    )
    statuses = load_current_operating_statuses(current_ids, chains)
    return frozenset(
        competitor_id
        for competitor_id in current_ids
        if competitor_id not in statuses
        or not any(
            observation.operating_status == OperatingStatus.PERMANENTLY_CLOSED
            for observation in statuses[competitor_id].observations
        )
    )
