"""Research job lifecycle: creation, the target lookup and confirmation, and status.

The target and the search settings change only while a job is Draft. Every change of
a Draft job's point clears its confirmation and recomputes the stored distances.
"""

import logging
from collections.abc import Collection
from decimal import Decimal

from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from axisence.accounts.models import User
from axisence.demographics.models import TabulationArea
from axisence.ingestion import http_session
from axisence.providers.models import Competitor
from axisence.research import geocoding
from axisence.research.job_competitors import (
    load_displayed_job_competitors,
    recompute_distances,
)
from axisence.research.merges import MergeChains
from axisence.research.models import (
    JobStatus,
    ResearchJob,
    ResearchStatus,
    TargetLookupOutcome,
    TargetPointSource,
)

logger = logging.getLogger(__name__)


class ResearchJobError(Exception):
    """A job action that cannot be applied; its message is shown in the admin."""


UNCONFIRMED_POINT_MESSAGE = "Confirm the target point first."

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.DRAFT: frozenset({JobStatus.RESEARCHING, JobStatus.CANCELLED}),
    JobStatus.RESEARCHING: frozenset({JobStatus.COMPLETE, JobStatus.CANCELLED}),
    JobStatus.COMPLETE: frozenset({JobStatus.RESEARCHING}),
    JobStatus.CANCELLED: frozenset({JobStatus.RESEARCHING}),
}

# Editable in every status.
_JOB_FIELD_NAMES = (
    "purpose",
    "organization_name",
    "assigned_researcher",
    "own_competitor",
)
# Editable only while Draft.
_DRAFT_FIELD_NAMES = frozenset(
    {
        "target_address",
        "target_point",
        "preselected_competitor_count",
        "search_radius_miles",
        "provider_kinds",
    }
)
_TARGET_POINT_FIELD_NAMES = (
    "target_point",
    "target_point_source",
    "target_point_confirmed_at",
    "target_point_confirmed_by",
)
_LOOKUP_FIELD_NAMES = (
    "geocoding_request_address",
    "geocoding_matched_address",
    "geocoding_outcome",
    "geocoding_attempted_at",
    "geocoding_candidates",
)
SEARCH_SETTING_FIELD_NAMES = (
    "preselected_competitor_count",
    "search_radius_miles",
    "provider_kinds",
)
_UNFINISHED_RESEARCH_STATUSES = frozenset(
    {ResearchStatus.TO_DO, ResearchStatus.IN_PROGRESS}
)


@transaction.atomic
def create_job(*, job: ResearchJob, researcher: User) -> None:
    """Store a new Draft job and look its target address up."""
    job.created_by = researcher
    job.status = JobStatus.DRAFT
    job.save()
    look_up_target(job=job)


def look_up_target(*, job: ResearchJob) -> TargetLookupOutcome:
    """Look the job's address up, propose what it finds, and clear the confirmation.

    One in-state match becomes the target point; several are listed for the
    researcher to pick; anything else leaves no point.
    """
    request_address = job.target_address.strip()
    with http_session.build_lookup_session() as session:
        result = geocoding.look_up_address(session, request_address)
    previous_point = job.target_point
    job.geocoding_request_address = request_address
    job.geocoding_outcome = result.outcome
    job.geocoding_attempted_at = timezone.now()
    _clear_confirmation(job)
    job.target_point = None
    job.target_point_source = ""
    job.geocoding_matched_address = ""
    job.geocoding_candidates = []
    if result.outcome == TargetLookupOutcome.MATCHED:
        match = result.matches[0]
        job.target_point = _point(
            match.coordinates.longitude, match.coordinates.latitude
        )
        job.target_point_source = TargetPointSource.GEOCODER
        job.geocoding_matched_address = match.matched_address
    elif result.outcome == TargetLookupOutcome.SEVERAL_MATCHES:
        job.geocoding_candidates = [
            {
                "matched_address": match.matched_address,
                "longitude": str(match.coordinates.longitude),
                "latitude": str(match.coordinates.latitude),
            }
            for match in result.matches
        ]
    job.save(update_fields=(*_TARGET_POINT_FIELD_NAMES, *_LOOKUP_FIELD_NAMES))
    if job.target_point != previous_point:
        recompute_distances(job)
    logger.info("Research job %d target lookup: %s", job.pk, result.outcome.value)
    return result.outcome


@transaction.atomic
def save_job_changes(
    *,
    job: ResearchJob,
    changed_field_names: Collection[str],
    confirm_target_point: bool,
    look_up_requested: bool,
    candidate_index: int | None,
    researcher: User,
) -> None:
    """Save an edited job, applying the target rules.

    A changed address or a requested lookup looks the address up again and ignores
    the posted point and confirmation, which the researcher has not seen for the new
    proposal. Otherwise a picked geocoder match, a moved or placed pin, or the
    confirmation checkbox set the point and its confirmation.
    """
    stored = ResearchJob.objects.select_for_update().get(pk=job.pk)
    changed = frozenset(changed_field_names)
    # The posted job carries the target as the form left it; the stored row is the
    # authority for what it was.
    posted_point = job.target_point
    _copy_target(source=stored, destination=job)
    if stored.status != JobStatus.DRAFT:
        if (
            changed & _DRAFT_FIELD_NAMES
            or look_up_requested
            or candidate_index is not None
        ):
            raise ResearchJobError(
                "The target and the search settings change only while the job is Draft."
            )
        job.save(update_fields=_JOB_FIELD_NAMES)
        return
    if "target_address" in changed or look_up_requested:
        job.save(
            update_fields=(
                *_JOB_FIELD_NAMES,
                "target_address",
                *SEARCH_SETTING_FIELD_NAMES,
            )
        )
        look_up_target(job=job)
        return
    if candidate_index is not None:
        candidates = stored.geocoding_candidates
        if not 0 <= candidate_index < len(candidates):
            raise ResearchJobError("That match is no longer listed.")
        candidate = candidates[candidate_index]
        job.target_point = _point(
            Decimal(candidate["longitude"]), Decimal(candidate["latitude"])
        )
        job.target_point_source = TargetPointSource.GEOCODER
        job.geocoding_matched_address = candidate["matched_address"]
        _clear_confirmation(job)
    elif "target_point" in changed:
        job.target_point = posted_point
        job.target_point_source = (
            "" if posted_point is None else TargetPointSource.RESEARCHER
        )
        _clear_confirmation(job)
        if confirm_target_point and posted_point is not None:
            _confirm(job, researcher)
    elif confirm_target_point:
        if job.target_point is not None and not job.is_target_point_confirmed:
            _confirm(job, researcher)
    elif job.is_target_point_confirmed:
        _clear_confirmation(job)
    job.save(
        update_fields=(
            *_JOB_FIELD_NAMES,
            *SEARCH_SETTING_FIELD_NAMES,
            *_TARGET_POINT_FIELD_NAMES,
            "geocoding_matched_address",
        )
    )
    if job.target_point != stored.target_point:
        recompute_distances(job)


@transaction.atomic
def change_status(*, job: ResearchJob, status: str, chains: MergeChains) -> None:
    """Move the job to another status along the allowed transitions."""
    stored = ResearchJob.objects.select_for_update().get(pk=job.pk)
    if status not in JobStatus.values:
        raise ResearchJobError("Unknown status.")
    current = JobStatus(stored.status)
    target = JobStatus(status)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ResearchJobError(f"A {current.label} job cannot become {target.label}.")
    if target == JobStatus.RESEARCHING and not stored.is_target_point_confirmed:
        raise ResearchJobError(UNCONFIRMED_POINT_MESSAGE)
    if target == JobStatus.COMPLETE and any(
        displayed.row.research_status in _UNFINISHED_RESEARCH_STATUSES
        for displayed in load_displayed_job_competitors(stored, chains)
    ):
        raise ResearchJobError("Set every competitor to Done or Unreachable first.")
    stored.status = target
    stored.save(update_fields=("status",))
    job.status = target
    logger.info(
        "Research job %d status changed from %s to %s",
        job.pk,
        current.value,
        target.value,
    )


def available_transitions(job: ResearchJob) -> list[tuple[str, str]]:
    """Return the statuses the job can move to, with their button labels."""
    allowed = ALLOWED_TRANSITIONS[job.status]
    transitions = []
    if JobStatus.RESEARCHING in allowed:
        label = "Start research" if job.status == JobStatus.DRAFT else "Reopen research"
        transitions.append((JobStatus.RESEARCHING.value, label))
    if JobStatus.COMPLETE in allowed:
        transitions.append((JobStatus.COMPLETE.value, "Mark complete"))
    if JobStatus.CANCELLED in allowed:
        transitions.append((JobStatus.CANCELLED.value, "Cancel job"))
    return transitions


def set_own_competitor(*, job: ResearchJob, competitor: Competitor) -> None:
    """Make the competitor the target itself, which hides it from the job's lists."""
    if competitor.retired_at is not None:
        raise ResearchJobError(
            "A retired competitor cannot be the job's own competitor."
        )
    job.own_competitor = competitor
    job.save(update_fields=("own_competitor",))
    logger.info("Research job %d own competitor set to %d", job.pk, competitor.pk)


def tabulation_area_code(point: Point) -> str | None:
    """Return the code of the tabulation area containing the point, if any."""
    return (
        TabulationArea.objects.filter(boundary__contains=point)
        .order_by("code")
        .values_list("code", flat=True)
        .first()
    )


def _point(longitude: Decimal, latitude: Decimal) -> Point:
    return Point(float(longitude), float(latitude), srid=4326)


def _copy_target(*, source: ResearchJob, destination: ResearchJob) -> None:
    for field_name in (*_TARGET_POINT_FIELD_NAMES, "geocoding_matched_address"):
        setattr(destination, field_name, getattr(source, field_name))


def _confirm(job: ResearchJob, researcher: User) -> None:
    job.target_point_confirmed_at = timezone.now()
    job.target_point_confirmed_by = researcher


def _clear_confirmation(job: ResearchJob) -> None:
    job.target_point_confirmed_at = None
    job.target_point_confirmed_by = None
