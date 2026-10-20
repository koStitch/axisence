"""The labels a researcher sees beside every listed competitor."""

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from django.db.models import Prefetch, Q, prefetch_related_objects

from axisence.providers.models import (
    Competitor,
    Provider,
    ProviderSource,
    ReviewFlag,
    ReviewFlagState,
)
from axisence.research.current_values import (
    CurrentSlot,
    load_current_operating_statuses,
)
from axisence.research.merges import MergeChains
from axisence.research.models import OperatingStatus

# NY State facility statuses that need no label.
_USUAL_FACILITY_STATUSES = frozenset({"License", "Registration"})
_CLOSED_STATUSES = frozenset(
    {OperatingStatus.TEMPORARILY_CLOSED, OperatingStatus.PERMANENTLY_CLOSED}
)


@dataclasses.dataclass(frozen=True, slots=True)
class CompetitorLabels:
    is_school_based: bool
    is_no_longer_listed: bool
    licensed_capacity: str
    ages_served: tuple[str, ...]
    open_review_flag_ids: tuple[int, ...]
    facility_statuses: tuple[str, ...]
    reported_closure: CurrentSlot | None


@dataclasses.dataclass(frozen=True, slots=True)
class LicensedCapacity:
    # The sum of the published capacities, or None when none is published.
    total: int | None
    # Whether every provider publishes its capacity.
    is_complete: bool


def load_competitor_labels(
    competitors: Sequence[Competitor],
    chains: MergeChains,
    *,
    as_of: datetime | None = None,
) -> dict[int, CompetitorLabels]:
    """Return the labels of current competitors, in three queries for any number.

    Each competitor also gets its active providers as active_providers. With as_of,
    the reported closure is the one current at that moment.
    """
    if not competitors:
        return {}
    prefetch_related_objects(
        list(competitors),
        Prefetch(
            "providers",
            queryset=Provider.objects.filter(missing_since__isnull=True).order_by(
                "source", "source_identifier"
            ),
            to_attr="active_providers",
        ),
    )
    competitor_ids = [competitor.pk for competitor in competitors]
    flag_ids_by_competitor_id: dict[int, list[int]] = {}
    for flag_id, flagged_id, proposed_id in (
        ReviewFlag.objects.filter(state=ReviewFlagState.OPEN)
        .filter(
            Q(flagged_competitor_id__in=competitor_ids)
            | Q(proposed_competitor_id__in=competitor_ids)
        )
        .order_by("pk")
        .values_list("pk", "flagged_competitor_id", "proposed_competitor_id")
    ):
        for competitor_id in {flagged_id, proposed_id}:
            if competitor_id is not None:
                flag_ids_by_competitor_id.setdefault(competitor_id, []).append(flag_id)
    operating_statuses = load_current_operating_statuses(
        competitor_ids, chains, as_of=as_of
    )
    return {
        competitor.pk: _labels(
            competitor,
            getattr(competitor, "active_providers", []),
            tuple(flag_ids_by_competitor_id.get(competitor.pk, ())),
            operating_statuses.get(competitor.pk),
        )
        for competitor in competitors
    }


def _labels(
    competitor: Competitor,
    active_providers: Sequence[Provider],
    open_review_flag_ids: tuple[int, ...],
    operating_status: CurrentSlot | None,
) -> CompetitorLabels:
    reported_closure = None
    if operating_status is not None and any(
        observation.operating_status in _CLOSED_STATUSES
        for observation in operating_status.observations
    ):
        reported_closure = operating_status
    return CompetitorLabels(
        is_school_based=competitor.is_school_based,
        is_no_longer_listed=not competitor.is_active,
        licensed_capacity=_licensed_capacity(active_providers),
        ages_served=ages_served(active_providers),
        open_review_flag_ids=open_review_flag_ids,
        facility_statuses=_facility_statuses(active_providers),
        reported_closure=reported_closure,
    )


def licensed_capacity(active_providers: Sequence[Provider]) -> LicensedCapacity:
    """Return the summed licensed capacity of the active providers."""
    capacities = [provider.total_capacity for provider in active_providers]
    published = [capacity for capacity in capacities if capacity is not None]
    return LicensedCapacity(
        total=sum(published) if published else None,
        is_complete=len(published) == len(capacities),
    )


def _licensed_capacity(active_providers: Sequence[Provider]) -> str:
    capacity = licensed_capacity(active_providers)
    if capacity.total is None:
        return "Unpublished"
    if not capacity.is_complete:
        return f"{capacity.total} (incomplete)"
    return str(capacity.total)


def ages_served(providers: Sequence[Provider]) -> tuple[str, ...]:
    """Return the ages the providers serve as published, without repeats.

    NYC Health publishes a program type and an age range; NY State publishes a
    capacity per age bracket, so a bracket with places counts.
    """
    ages: list[str] = []
    for provider in providers:
        if provider.source == ProviderSource.NYC_HEALTH:
            published = [
                value.strip()
                for value in (
                    provider.nyc_health_program_type,
                    provider.nyc_health_child_age_range,
                )
                if value.strip()
            ]
            provider_ages = [", ".join(published)] if published else []
        else:
            provider_ages = [
                label
                for label, capacity in (
                    ("Infant", provider.ny_state_infant_capacity),
                    ("Toddler", provider.ny_state_toddler_capacity),
                    ("Preschool", provider.ny_state_preschool_capacity),
                    ("School age", provider.ny_state_school_age_capacity),
                )
                if capacity is not None and capacity > 0
            ]
        for age in provider_ages:
            if age not in ages:
                ages.append(age)
    return tuple(ages)


def _facility_statuses(active_providers: Sequence[Provider]) -> tuple[str, ...]:
    statuses: list[str] = []
    for provider in active_providers:
        if provider.source != ProviderSource.NY_STATE:
            continue
        status = provider.ny_state_facility_status.strip()
        if status in _USUAL_FACILITY_STATUSES:
            continue
        label = status or "Status not published"
        if label not in statuses:
            statuses.append(label)
    return tuple(statuses)
