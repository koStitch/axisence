"""Group providers into competitors and keep each competitor's summary current.

Grouping places only providers without a competitor. A placed provider changes its
competitor only through a researcher action in review.py, and grouping never merges
existing competitors.
"""

import collections
import dataclasses
from collections.abc import Collection, Sequence
from typing import Any

from django.contrib.gis.geos import Point

from axisence.ingestion.models import IngestionRun
from axisence.providers.models import (
    Competitor,
    CompetitorDecision,
    CompetitorDecisionAction,
    Provider,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
    ReviewFlagState,
)
from axisence.providers.normalization import name_keys_are_similar

WRITE_BATCH_SIZE = 1000
SUMMARY_CHUNK_SIZE = 2000

SUMMARY_FIELD_NAMES = (
    "display_name",
    "street_address",
    "borough",
    "location",
    "is_active",
    "is_school_based",
)


@dataclasses.dataclass(slots=True)
class GroupingCounts:
    competitors_created: int
    review_flags_raised: int


@dataclasses.dataclass(frozen=True, slots=True)
class CompetitorSummary:
    display_name: str
    street_address: str
    borough: str
    location: Point | None
    is_active: bool
    is_school_based: bool

    def as_fields(self) -> dict[str, Any]:
        """Return the summary as Competitor field values."""
        return {name: getattr(self, name) for name in SUMMARY_FIELD_NAMES}


def summarize_competitor(members: Sequence[Provider]) -> CompetitorSummary:
    """Describe a competitor by its representative member and its members' activity."""
    representative = min(members, key=_representative_order)
    return CompetitorSummary(
        display_name=representative.display_name,
        street_address=representative.street_address,
        borough=representative.borough,
        location=representative.location,
        is_active=any(member.is_active for member in members),
        is_school_based=any(member.is_school_based for member in members),
    )


def refresh_competitor_summaries(competitor_ids: Collection[int] | None = None) -> int:
    """Store the current summary of every current competitor, or of the given ones.

    Returns how many competitors changed. A retired competitor keeps its last summary.
    """
    competitors = Competitor.objects.filter(retired_at__isnull=True).prefetch_related(
        "providers"
    )
    if competitor_ids is not None:
        competitors = competitors.filter(pk__in=competitor_ids)
    changed = []
    for competitor in competitors.iterator(chunk_size=SUMMARY_CHUNK_SIZE):
        members = list(competitor.providers.all())
        if members and _apply_summary(competitor, summarize_competitor(members)):
            changed.append(competitor)
    Competitor.objects.bulk_update(
        changed, SUMMARY_FIELD_NAMES, batch_size=WRITE_BATCH_SIZE
    )
    return len(changed)


def different_operator_pairs() -> set[frozenset[int]]:
    """Return the competitor pairs a researcher declared different operators."""
    decisions = CompetitorDecision.objects.filter(
        action=CompetitorDecisionAction.DIFFERENT_OPERATORS
    ).values_list("from_competitor_id", "to_competitor_id")
    return {
        frozenset((from_id, to_id))
        for from_id, to_id in decisions
        if from_id is not None and to_id is not None
    }


class _NewCompetitor:
    """A competitor this run creates, before it has a primary key."""

    def __init__(self) -> None:
        self.members: list[Provider] = []
        self.competitor_id: int | None = None


type _Handle = int | _NewCompetitor


def assign_competitors(run: IngestionRun) -> GroupingCounts:
    """Place every provider without a competitor, raising flags for a researcher.

    A provider joins the one competitor whose member has equal address, zip, and name
    keys. Without one it gets a competitor of its own; with several it gets one of its
    own and an equal-keys flag towards each. A similar name at the same address and
    zip raises one similar-name flag per competitor pair.
    """
    excluded_pairs = different_operator_pairs()
    open_pairs = {
        frozenset(pair)
        for pair in ReviewFlag.objects.filter(
            state=ReviewFlagState.OPEN, proposed_competitor__isnull=False
        ).values_list("flagged_competitor_id", "proposed_competitor_id")
    }
    index: dict[tuple[str, str], list[tuple[str, _Handle]]] = collections.defaultdict(
        list
    )
    placed_keys = (
        Provider.objects.filter(competitor__isnull=False)
        .exclude(address_key="")
        .exclude(zip_key="")
        .exclude(name_key="")
        .values_list("address_key", "zip_key", "name_key", "competitor_id")
    )
    for address_key, zip_key, name_key, competitor_id in placed_keys:
        index[(address_key, zip_key)].append((name_key, competitor_id))

    unplaced = list(
        Provider.objects.filter(competitor__isnull=True).order_by(
            "source", "source_identifier"
        )
    )
    new_competitors: list[_NewCompetitor] = []
    placements: list[tuple[Provider, _Handle]] = []
    queued_flags: list[tuple[ReviewFlagReason, Provider, _Handle, _Handle]] = []
    queued_pairs: set[frozenset[_Handle]] = set()

    def new_competitor() -> _NewCompetitor:
        created = _NewCompetitor()
        new_competitors.append(created)
        return created

    def queue_flag(
        reason: ReviewFlagReason,
        provider: Provider,
        flagged: _Handle,
        proposed: _Handle,
    ) -> None:
        pair = frozenset((flagged, proposed))
        if pair in excluded_pairs or pair in open_pairs or pair in queued_pairs:
            return
        queued_pairs.add(pair)
        queued_flags.append((reason, provider, flagged, proposed))

    for provider in unplaced:
        if not (provider.address_key and provider.zip_key and provider.name_key):
            # Without all three keys nothing can match, as for every omitted address.
            own_competitor = new_competitor()
            own_competitor.members.append(provider)
            placements.append((provider, own_competitor))
            continue
        entries = index[(provider.address_key, provider.zip_key)]
        equal_handles = list(
            dict.fromkeys(
                entry_handle
                for entry_name_key, entry_handle in entries
                if entry_name_key == provider.name_key
            )
        )
        if len(equal_handles) == 1:
            handle = equal_handles[0]
        else:
            handle = new_competitor()
            for equal_handle in equal_handles:
                queue_flag(
                    ReviewFlagReason.EQUAL_KEYS_IN_SEVERAL_COMPETITORS,
                    provider,
                    handle,
                    equal_handle,
                )
        if isinstance(handle, _NewCompetitor):
            handle.members.append(provider)
        for entry_name_key, entry_handle in entries:
            if (
                entry_handle != handle
                and entry_name_key != provider.name_key
                and name_keys_are_similar(provider.name_key, entry_name_key)
            ):
                queue_flag(
                    ReviewFlagReason.SIMILAR_NAME, provider, handle, entry_handle
                )
        entries.append((provider.name_key, handle))
        placements.append((provider, handle))

    competitors = [
        Competitor(**summarize_competitor(created.members).as_fields())
        for created in new_competitors
    ]
    Competitor.objects.bulk_create(competitors, batch_size=WRITE_BATCH_SIZE)
    for created, competitor in zip(new_competitors, competitors, strict=True):
        created.competitor_id = competitor.pk
    for provider, handle in placements:
        provider.competitor_id = _competitor_id(handle)
    Provider.objects.bulk_update(unplaced, ("competitor",), batch_size=WRITE_BATCH_SIZE)
    ReviewFlag.objects.bulk_create(
        [
            ReviewFlag(
                reason=reason,
                provider=provider,
                flagged_competitor_id=_competitor_id(flagged),
                proposed_competitor_id=_competitor_id(proposed),
                raised_by_run=run,
            )
            for reason, provider, flagged, proposed in queued_flags
        ],
        batch_size=WRITE_BATCH_SIZE,
    )
    return GroupingCounts(
        competitors_created=len(competitors), review_flags_raised=len(queued_flags)
    )


def _representative_order(member: Provider) -> tuple[bool, bool, bool, bool, str]:
    # Active first, then a published street address, then a location, then NYC
    # Health, whose center rows carry the operator's own name and address.
    return (
        not member.is_active,
        member.street_address == "",
        member.location is None,
        member.source != ProviderSource.NYC_HEALTH,
        member.source_identifier,
    )


def _apply_summary(competitor: Competitor, summary: CompetitorSummary) -> bool:
    changed = False
    for name, value in summary.as_fields().items():
        if getattr(competitor, name) != value:
            setattr(competitor, name, value)
            changed = True
    return changed


def _competitor_id(handle: _Handle) -> int:
    if isinstance(handle, _NewCompetitor):
        if handle.competitor_id is None:
            raise RuntimeError("A new competitor was used before it was saved.")
        return handle.competitor_id
    return handle
