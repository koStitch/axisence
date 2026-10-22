"""What a value says, compared to tell a change from a re-confirmation, and the order in
which value slots are listed.

The call sheet's history and a repeat report's changes both use these definitions, so
they never disagree about what changed. Descriptions, earliest start dates, and
expected waits are not part of a stated value.
"""

from collections.abc import Iterable
from decimal import Decimal

from axisence.research.models import (
    ChargeKind,
    DiscountKind,
    Observation,
    ObservationKind,
)

type StatedValue = tuple[Decimal | str | bool | None, ...]

# Kinds whose slots the history and the changes compare.
VALUE_SLOT_KINDS: frozenset[str] = frozenset(
    {
        ObservationKind.PRICE,
        ObservationKind.AVAILABILITY,
        ObservationKind.DISCOUNT,
        ObservationKind.OTHER_CHARGE,
    }
)

# Discounts and charges are listed in the order their kinds are declared.
_DISCOUNT_KIND_POSITIONS = {
    kind.value: position for position, kind in enumerate(DiscountKind)
}
_CHARGE_KIND_POSITIONS = {
    kind.value: position for position, kind in enumerate(ChargeKind)
}


def stated_value(observation: Observation) -> StatedValue:
    """Return what the value states, compared between contacts."""
    match observation.kind:
        case ObservationKind.PRICE:
            return (
                observation.amount,
                observation.price_period,
                observation.includes_discounts,
            )
        case ObservationKind.AVAILABILITY:
            return (observation.availability_status,)
        case ObservationKind.DISCOUNT:
            return (
                observation.is_none_offered,
                observation.amount,
                observation.percent,
            )
        case ObservationKind.OTHER_CHARGE:
            return (
                observation.is_none_offered,
                observation.amount,
                observation.months_of_tuition,
                observation.charge_frequency,
            )
    raise ValueError(f"Observations of kind {observation.kind!r} have no stated value.")


def slot_stated_value(observations: Iterable[Observation]) -> frozenset[StatedValue]:
    """Return the stated value of one contact's values of a slot, compared as a set."""
    return frozenset(stated_value(observation) for observation in observations)


def loaded_definition(definition: Observation | None) -> Observation:
    """Return a price's or availability's age group or schedule, which the check
    constraints always require.
    """
    if definition is None:
        raise ValueError("A price or availability always refers to its definitions.")
    return definition


def value_slot_sort_key(observation: Observation) -> tuple[int | str, ...]:
    """Return the position of the observation's slot: prices, availability, discounts,
    then other charges; prices and availability by age group, prices then by schedule;
    discounts and charges in the order of their kinds.

    The observation's definitions must be loaded.
    """
    match observation.kind:
        case ObservationKind.PRICE:
            age_group = loaded_definition(observation.age_group)
            schedule = loaded_definition(observation.schedule)
            return (
                0,
                age_group.youngest_age_months or 0,
                age_group.name,
                age_group.pk,
                -(schedule.days_per_week or 0),
                schedule.name,
                schedule.pk,
            )
        case ObservationKind.AVAILABILITY:
            age_group = loaded_definition(observation.age_group)
            return (
                1,
                age_group.youngest_age_months or 0,
                age_group.name,
                age_group.pk,
            )
        case ObservationKind.DISCOUNT:
            return (2, _DISCOUNT_KIND_POSITIONS[observation.discount_kind])
        case ObservationKind.OTHER_CHARGE:
            return (3, _CHARGE_KIND_POSITIONS[observation.charge_kind])
    raise ValueError(f"Observations of kind {observation.kind!r} have no value slot.")
