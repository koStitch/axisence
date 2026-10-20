"""The competitors a job lists, resolved through merges, and their stored distances."""

import dataclasses

from django.contrib.gis.db.models.functions import Distance

from axisence.providers.models import Competitor
from axisence.research.distances import distances_from
from axisence.research.merges import MergeChains
from axisence.research.models import JobCompetitor, ResearchJob


@dataclasses.dataclass(frozen=True, slots=True)
class DisplayedJobCompetitor:
    # The row whose research status is shown and edited.
    row: JobCompetitor
    # The current competitor at the end of the row's merge chain.
    competitor: Competitor
    distance_meters: float | None


def load_displayed_job_competitors(
    job: ResearchJob, chains: MergeChains
) -> list[DisplayedJobCompetitor]:
    """Return the job's competitors once each, as current competitors, nearest first.

    Rows of competitors merged into one are shown once, by the row that points at the
    current competitor or else the oldest row. Rows that resolve to the job's own
    competitor are hidden, not deleted. Runs two queries.
    """
    own_competitor_id = (
        None
        if job.own_competitor_id is None
        else chains.current_id(job.own_competitor_id)
    )
    rows_by_competitor_id: dict[int, list[JobCompetitor]] = {}
    for row in JobCompetitor.objects.filter(job=job).order_by("pk"):
        current_id = chains.current_id(row.competitor_id)
        if current_id != own_competitor_id:
            rows_by_competitor_id.setdefault(current_id, []).append(row)
    if not rows_by_competitor_id:
        return []
    competitors = Competitor.objects.filter(pk__in=rows_by_competitor_id)
    if job.target_point is not None:
        competitors = competitors.annotate(
            distance=Distance("location", job.target_point)
        )
    competitors_by_id = {competitor.pk: competitor for competitor in competitors}
    displayed = []
    for competitor_id, rows in rows_by_competitor_id.items():
        row = next((row for row in rows if row.competitor_id == competitor_id), rows[0])
        competitor = competitors_by_id[competitor_id]
        if row.competitor_id == competitor_id:
            distance_meters = row.distance_meters
        else:
            # The row points at a merged competitor, whose stored distance may not be
            # the receiving competitor's.
            live_distance = getattr(competitor, "distance", None)
            distance_meters = None if live_distance is None else live_distance.m
        displayed.append(
            DisplayedJobCompetitor(
                row=row, competitor=competitor, distance_meters=distance_meters
            )
        )
    displayed.sort(
        key=lambda item: (
            item.distance_meters is None,
            item.distance_meters or 0,
            item.competitor.pk,
        )
    )
    return displayed


def recompute_distances(job: ResearchJob) -> None:
    """Store each row's distance from the job's current target point.

    A row keeps measuring to its own competitor, which keeps its last location when
    retired.
    """
    rows = list(JobCompetitor.objects.filter(job=job))
    if not rows:
        return
    distances = distances_from(job.target_point, {row.competitor_id for row in rows})
    for row in rows:
        row.distance_meters = distances.get(row.competitor_id)
    JobCompetitor.objects.bulk_update(rows, ("distance_meters",))
