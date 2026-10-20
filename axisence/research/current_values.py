"""The current value of every slot of a competitor, computed from its observations.

A slot is what one value answers: the price of one age group on one schedule, the
availability of one age group, one kind of discount or charge, or one single fact. The
newest contact that observed a slot holds its current value.
"""

import dataclasses
from collections.abc import Collection, Iterable, Mapping
from datetime import date, datetime

from axisence.research.merges import MergeChains
from axisence.research.models import (
    SINGLE_FACT_KINDS,
    Contact,
    Observation,
    ObservationKind,
)


@dataclasses.dataclass(frozen=True, slots=True)
class Slot:
    kind: str
    age_group_id: int | None = None
    schedule_id: int | None = None
    # The discount kind or the charge kind.
    subkind: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class CurrentSlot:
    slot: Slot
    contact: Contact
    # In primary key order.
    observations: tuple[Observation, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class CurrentValues:
    age_groups: tuple[Observation, ...]
    schedules: tuple[Observation, ...]
    slots: Mapping[Slot, CurrentSlot]


def slot_of(observation: Observation) -> Slot | None:
    """Return the slot a value observation answers; definitions answer none."""
    match observation.kind:
        case ObservationKind.PRICE:
            return Slot(
                ObservationKind.PRICE,
                age_group_id=observation.age_group_id,
                schedule_id=observation.schedule_id,
            )
        case ObservationKind.AVAILABILITY:
            return Slot(
                ObservationKind.AVAILABILITY, age_group_id=observation.age_group_id
            )
        case ObservationKind.DISCOUNT:
            return Slot(ObservationKind.DISCOUNT, subkind=observation.discount_kind)
        case ObservationKind.OTHER_CHARGE:
            return Slot(ObservationKind.OTHER_CHARGE, subkind=observation.charge_kind)
    if observation.kind in SINGLE_FACT_KINDS:
        return Slot(observation.kind)
    return None


def compute_current_values(
    observations: Iterable[Observation], *, as_of: datetime | None = None
) -> CurrentValues:
    """Apply the current-value rules; each observation's contact must be loaded.

    1. An observation counts while neither it nor its contact is retracted. As of a
       moment, it counts when its contact was entered by then and neither it nor its
       contact had been retracted by then.
    2. A definition is current until a counting retirement points at it.
    3. A value counts while every definition it refers to is current.
    4. In each slot, the newest contact among the counting values wins, by contact
       date, then entry time, then contact number, with all its values of the slot.
    """
    valid = [observation for observation in observations if _counts(observation, as_of)]
    retired_definition_ids = {
        observation.age_group_id
        for observation in valid
        if observation.kind == ObservationKind.AGE_GROUP_RETIREMENT
    } | {
        observation.schedule_id
        for observation in valid
        if observation.kind == ObservationKind.SCHEDULE_RETIREMENT
    }
    age_groups = sorted(
        (
            observation
            for observation in valid
            if observation.kind == ObservationKind.AGE_GROUP
            and observation.pk not in retired_definition_ids
        ),
        key=lambda observation: (
            observation.youngest_age_months or 0,
            observation.name,
            observation.pk,
        ),
    )
    schedules = sorted(
        (
            observation
            for observation in valid
            if observation.kind == ObservationKind.SCHEDULE
            and observation.pk not in retired_definition_ids
        ),
        key=lambda observation: (
            -(observation.days_per_week or 0),
            observation.name,
            observation.pk,
        ),
    )
    current_definition_ids = {definition.pk for definition in (*age_groups, *schedules)}
    candidates_by_slot: dict[Slot, list[Observation]] = {}
    for observation in valid:
        slot = slot_of(observation)
        if slot is None or any(
            definition_id is not None and definition_id not in current_definition_ids
            for definition_id in (observation.age_group_id, observation.schedule_id)
        ):
            continue
        candidates_by_slot.setdefault(slot, []).append(observation)
    slots = {}
    for slot, candidates in candidates_by_slot.items():
        newest = max(
            (candidate.contact for candidate in candidates), key=_contact_recency
        )
        slots[slot] = CurrentSlot(
            slot=slot,
            contact=newest,
            observations=tuple(
                sorted(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.contact_id == newest.pk
                    ),
                    key=lambda candidate: candidate.pk,
                )
            ),
        )
    return CurrentValues(
        age_groups=tuple(age_groups), schedules=tuple(schedules), slots=slots
    )


def load_current_values(competitor_id: int, chains: MergeChains) -> CurrentValues:
    """Return the competitor's current values, its merged competitors' included."""
    observations = Observation.objects.filter(
        contact__competitor_id__in=chains.member_ids(competitor_id)
    ).select_related("contact", "contact__researcher", "age_group", "schedule")
    return compute_current_values(observations)


def load_current_operating_statuses(
    current_competitor_ids: Collection[int],
    chains: MergeChains,
    *,
    as_of: datetime | None = None,
) -> dict[int, CurrentSlot]:
    """Return the current operating status of each competitor that has one."""
    if not current_competitor_ids:
        return {}
    member_ids = set[int]().union(
        *(chains.member_ids(competitor_id) for competitor_id in current_competitor_ids)
    )
    observations_by_current_id: dict[int, list[Observation]] = {}
    for observation in Observation.objects.filter(
        kind=ObservationKind.OPERATING_STATUS,
        contact__competitor_id__in=member_ids,
    ).select_related("contact"):
        observations_by_current_id.setdefault(
            chains.current_id(observation.contact.competitor_id), []
        ).append(observation)
    statuses = {}
    operating_status_slot = Slot(ObservationKind.OPERATING_STATUS)
    for current_id, observations in observations_by_current_id.items():
        current_slot = compute_current_values(observations, as_of=as_of).slots.get(
            operating_status_slot
        )
        if current_slot is not None:
            statuses[current_id] = current_slot
    return statuses


def _counts(observation: Observation, as_of: datetime | None) -> bool:
    contact = observation.contact
    if as_of is None:
        return observation.retracted_at is None and contact.retracted_at is None
    return (
        contact.entered_at <= as_of
        and (observation.retracted_at is None or observation.retracted_at > as_of)
        and (contact.retracted_at is None or contact.retracted_at > as_of)
    )


def _contact_recency(contact: Contact) -> tuple[date, datetime, int]:
    return (contact.contact_date, contact.entered_at, contact.pk)
