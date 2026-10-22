"""The competitor search of a job and the saving of its selection."""

import dataclasses
import logging
from collections.abc import Callable, Collection, Mapping

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db import transaction
from django.db.models import Exists, OuterRef

from axisence.providers.models import Competitor, Provider
from axisence.research.distances import distances_from
from axisence.research.job_competitors import load_displayed_job_competitors
from axisence.research.jobs import UNCONFIRMED_POINT_MESSAGE, ResearchJobError
from axisence.research.labels import CompetitorLabels, load_competitor_labels
from axisence.research.merges import MergeChains
from axisence.research.models import JobCompetitor, JobStatus, ResearchJob

logger = logging.getLogger(__name__)

SEARCH_UNAVAILABLE_MESSAGE = (
    "The search is available while the job is Draft or Researching."
)


@dataclasses.dataclass(frozen=True, slots=True)
class SearchRow:
    competitor: Competitor
    distance_meters: float | None
    is_selected: bool


@dataclasses.dataclass(frozen=True, slots=True)
class SearchPage:
    results: tuple[SearchRow, ...]
    # Selected competitors that are not among the results.
    kept_selections: tuple[SearchRow, ...]
    labels: Mapping[int, CompetitorLabels]

    @property
    def displayed_competitor_ids(self) -> frozenset[int]:
        return frozenset(
            row.competitor.pk for row in (*self.results, *self.kept_selections)
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SelectionChanges:
    added: int
    removed: int


def find_competitors(
    job: ResearchJob, chains: MergeChains
) -> list[tuple[Competitor, float]]:
    """Return the job's candidate competitors with their distances, nearest first.

    A candidate is active, current, located within the radius, and has an active
    provider of one of the job's kinds, in any borough and either dataset. The job's
    own competitor is never a candidate.
    """
    if job.target_point is None:
        return []
    kinds_match = Exists(
        Provider.objects.filter(
            competitor=OuterRef("pk"),
            missing_since__isnull=True,
            kind__in=job.provider_kinds,
        )
    )
    competitors = (
        Competitor.objects.filter(
            is_active=True,
            retired_at__isnull=True,
            location__isnull=False,
            location__dwithin=(
                job.target_point,
                D(mi=float(job.search_radius_miles)),
            ),
        )
        .filter(kinds_match)
        .annotate(distance=Distance("location", job.target_point))
        .order_by("distance", "pk")
    )
    if job.own_competitor_id is not None:
        competitors = competitors.exclude(pk=chains.current_id(job.own_competitor_id))
    return [(competitor, competitor.distance.m) for competitor in competitors]


def build_search_page(
    job: ResearchJob,
    chains: MergeChains,
    *,
    carried_over_ids: Callable[[], Collection[int]] | None = None,
) -> SearchPage:
    """Return the results, the selections outside them, and every listed label.

    A job without any selected row starts with its nearest competitors ticked,
    skipping reported closures; afterwards the stored selection is shown as it is. A
    job without any selected row also ticks the competitors carried_over_ids returns,
    which a repeat job carries over from its predecessor's report, and shows those
    outside the results among the kept selections. carried_over_ids is called only for
    a job without a selected row.
    """
    found = find_competitors(job, chains)
    result_ids = {competitor.pk for competitor, _ in found}
    has_selection = JobCompetitor.objects.filter(job=job).exists()
    carried: frozenset[int] = frozenset()
    if has_selection:
        displayed = load_displayed_job_competitors(job, chains)
        selected_ids = {item.competitor.pk for item in displayed}
        kept = [
            (item.competitor, item.distance_meters)
            for item in displayed
            if item.competitor.pk not in result_ids
        ]
    else:
        if carried_over_ids is not None:
            carried = frozenset(carried_over_ids())
        kept = _located_competitors(job, carried - result_ids)
    labels = load_competitor_labels(
        [competitor for competitor, _ in found]
        + [competitor for competitor, _ in kept],
        chains,
    )
    if not has_selection:
        open_ids = [
            competitor.pk
            for competitor, _ in found
            if labels[competitor.pk].reported_closure is None
        ]
        selected_ids = set(open_ids[: job.preselected_competitor_count]) | carried
    return SearchPage(
        results=tuple(
            SearchRow(
                competitor=competitor,
                distance_meters=distance_meters,
                is_selected=competitor.pk in selected_ids,
            )
            for competitor, distance_meters in found
        ),
        kept_selections=tuple(
            SearchRow(competitor=competitor, distance_meters=distance, is_selected=True)
            for competitor, distance in kept
        ),
        labels=labels,
    )


def _located_competitors(
    job: ResearchJob, competitor_ids: Collection[int]
) -> list[tuple[Competitor, float | None]]:
    """Return the competitors with their distances from the target, unknown last."""
    if not competitor_ids:
        return []
    located = [
        (competitor, None if competitor.distance is None else competitor.distance.m)
        for competitor in Competitor.objects.filter(pk__in=competitor_ids).annotate(
            distance=Distance("location", job.target_point)
        )
    ]
    return sorted(
        located,
        key=lambda item: (item[1] is None, item[1] or 0.0, item[0].pk),
    )


@transaction.atomic
def save_selection(
    *,
    job: ResearchJob,
    displayed_competitor_ids: Collection[int],
    selected_competitor_ids: Collection[int],
    added_competitor: Competitor | None,
    chains: MergeChains,
) -> SelectionChanges:
    """Store which displayed competitors are selected, and add one by name.

    A newly selected competitor gets its distance now. A deselected competitor loses
    every row that resolves to it, even when job-linked contacts exist; the contacts
    keep their job link.
    """
    job = ResearchJob.objects.select_for_update().get(pk=job.pk)
    if job.status not in (JobStatus.DRAFT, JobStatus.RESEARCHING):
        raise ResearchJobError(SEARCH_UNAVAILABLE_MESSAGE)
    if not job.is_target_point_confirmed:
        raise ResearchJobError(UNCONFIRMED_POINT_MESSAGE)
    own_competitor_id = (
        None
        if job.own_competitor_id is None
        else chains.current_id(job.own_competitor_id)
    )
    displayed_ids = frozenset(displayed_competitor_ids)
    selected_ids = frozenset(selected_competitor_ids) & displayed_ids
    rows = list(JobCompetitor.objects.filter(job=job))
    competitor_id_by_row_id = {
        row.pk: chains.current_id(row.competitor_id) for row in rows
    }
    selected_before = frozenset(competitor_id_by_row_id.values())
    new_ids = sorted(
        competitor_id
        for competitor_id in selected_ids - selected_before
        if competitor_id != own_competitor_id
    )
    distances = distances_from(job.target_point, new_ids)
    JobCompetitor.objects.bulk_create(
        [
            JobCompetitor(
                job=job,
                competitor_id=competitor_id,
                distance_meters=distances[competitor_id],
            )
            for competitor_id in new_ids
        ]
    )
    # The own competitor's rows are hidden, never deleted.
    removed_ids = (selected_before & displayed_ids) - selected_ids - {own_competitor_id}
    JobCompetitor.objects.filter(
        pk__in=[
            row_id
            for row_id, competitor_id in competitor_id_by_row_id.items()
            if competitor_id in removed_ids
        ]
    ).delete()
    added_count = len(new_ids)
    if added_competitor is not None:
        _add_by_name(
            job=job,
            competitor=Competitor.objects.get(pk=added_competitor.pk),
            own_competitor_id=own_competitor_id,
            selected_ids=(selected_before - removed_ids) | frozenset(new_ids),
        )
        added_count += 1
    logger.info(
        "Research job %d selection saved: %d added, %d removed",
        job.pk,
        added_count,
        len(removed_ids),
    )
    return SelectionChanges(added=added_count, removed=len(removed_ids))


def _add_by_name(
    *,
    job: ResearchJob,
    competitor: Competitor,
    own_competitor_id: int | None,
    selected_ids: frozenset[int],
) -> None:
    if not competitor.is_active or competitor.retired_at is not None:
        raise ResearchJobError("Only an active competitor can be added.")
    if competitor.pk == own_competitor_id:
        raise ResearchJobError("The target's own competitor cannot be its competitor.")
    if competitor.pk in selected_ids:
        raise ResearchJobError("The competitor is already selected.")
    JobCompetitor.objects.create(
        job=job,
        competitor=competitor,
        distance_meters=distances_from(job.target_point, [competitor.pk])[
            competitor.pk
        ],
    )
