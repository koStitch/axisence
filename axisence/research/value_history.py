"""The history of a competitor's prices, availability, discounts, and other charges on
the call sheet, as runs of one stated value.

Only counting observations are used: retracted observations and the observations of
retracted contacts are left out, while observations of retired definitions stay. No
note, role, or researcher appears.
"""

import dataclasses
from collections.abc import Iterable, Mapping, Sequence

from axisence.research.current_values import (
    Slot,
    contact_recency,
    is_counting,
    slot_of,
)
from axisence.research.models import (
    ChargeKind,
    Contact,
    DiscountKind,
    Observation,
    ObservationKind,
)
from axisence.research.stated_values import (
    VALUE_SLOT_KINDS,
    StatedValue,
    loaded_definition,
    slot_stated_value,
    value_slot_sort_key,
)

# A retired definition, as the kind of definition and its observation number.
type _DefinitionKey = tuple[str, int | None]


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryRun:
    # The values of the run's newest contact, one per line.
    values: tuple[str, ...]
    first_recorded: str
    last_confirmed: str
    contact_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class HistorySlot:
    heading: str
    retirement_lines: tuple[str, ...]
    # Newest first.
    runs: tuple[HistoryRun, ...]


def build_value_history(observations: Iterable[Observation]) -> tuple[HistorySlot, ...]:
    """Return the history of every value slot that has a counting observation, in slot
    order. Each observation's contact and definitions must be loaded.
    """
    counting = [
        observation for observation in observations if is_counting(observation, None)
    ]
    retiring_contacts: dict[_DefinitionKey, list[Contact]] = {}
    for observation in counting:
        if observation.kind == ObservationKind.AGE_GROUP_RETIREMENT:
            key: _DefinitionKey = ("age_group", observation.age_group_id)
        elif observation.kind == ObservationKind.SCHEDULE_RETIREMENT:
            key = ("schedule", observation.schedule_id)
        else:
            continue
        retiring_contacts.setdefault(key, []).append(observation.contact)
    # The earliest of several retirements of one definition is shown.
    retirements = {
        key: min(contacts, key=contact_recency)
        for key, contacts in retiring_contacts.items()
    }
    observations_by_slot: dict[Slot, list[Observation]] = {}
    for observation in counting:
        slot = slot_of(observation)
        if slot is not None and observation.kind in VALUE_SLOT_KINDS:
            observations_by_slot.setdefault(slot, []).append(observation)
    ordered = sorted(
        observations_by_slot.items(),
        key=lambda item: value_slot_sort_key(item[1][0]),
    )
    return tuple(
        HistorySlot(
            heading=_heading(slot_observations[0]),
            retirement_lines=_retirement_lines(slot, retirements),
            runs=_runs(slot_observations),
        )
        for slot, slot_observations in ordered
    )


def _heading(observation: Observation) -> str:
    match observation.kind:
        case ObservationKind.PRICE:
            age_group = loaded_definition(observation.age_group)
            schedule = loaded_definition(observation.schedule)
            return (
                f"Price for {age_group.name} ({age_group.age_range_summary()}) on "
                f"{schedule.name}"
            )
        case ObservationKind.AVAILABILITY:
            age_group = loaded_definition(observation.age_group)
            return (
                f"Availability for {age_group.name} ({age_group.age_range_summary()})"
            )
        case ObservationKind.DISCOUNT:
            return f"Discount: {DiscountKind(observation.discount_kind).label}"
    return f"Other charge: {ChargeKind(observation.charge_kind).label}"


def _retirement_lines(
    slot: Slot, retirements: Mapping[_DefinitionKey, Contact]
) -> tuple[str, ...]:
    # A discount or charge slot names no definition, so it matches no retirement.
    lines = []
    for label, key in (
        ("Age group", ("age_group", slot.age_group_id)),
        ("Schedule", ("schedule", slot.schedule_id)),
    ):
        contact = retirements.get(key)
        if contact is not None:
            lines.append(f"{label} retired {_recorded(contact)}")
    return tuple(lines)


def _runs(observations: Sequence[Observation]) -> tuple[HistoryRun, ...]:
    """Join consecutive contacts with equal stated values, and return the runs newest
    first.
    """
    observations_by_contact_id: dict[int, list[Observation]] = {}
    contacts: dict[int, Contact] = {}
    for observation in observations:
        observations_by_contact_id.setdefault(observation.contact_id, []).append(
            observation
        )
        contacts[observation.contact_id] = observation.contact
    runs: list[list[Contact]] = []
    previous_value: frozenset[StatedValue] | None = None
    for contact in sorted(contacts.values(), key=contact_recency):
        value = slot_stated_value(observations_by_contact_id[contact.pk])
        if runs and value == previous_value:
            runs[-1].append(contact)
        else:
            runs.append([contact])
        previous_value = value
    return tuple(
        HistoryRun(
            values=tuple(
                observation.slot_value_summary()
                for observation in sorted(
                    observations_by_contact_id[run[-1].pk],
                    key=lambda observation: observation.pk,
                )
            ),
            first_recorded=_recorded(run[0]),
            last_confirmed=_recorded(run[-1]),
            contact_count=len(run),
        )
        for run in reversed(runs)
    )


def _recorded(contact: Contact) -> str:
    # Worded as the call sheet's definition rows word their contact.
    return f"{contact.contact_date.isoformat()}, {contact.get_method_display()}"
