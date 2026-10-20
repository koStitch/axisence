"""Prices as stated, their monthly equivalents, and the cells of the tuition comparison.
Nothing here is stored.
"""

import dataclasses
from decimal import ROUND_HALF_UP, Decimal

from axisence.reports.formatting import (
    format_dollar_range,
    format_money,
    format_whole_dollars,
)
from axisence.research.current_values import CurrentValues
from axisence.research.models import Observation, ObservationKind, PricePeriod

REFERENCE_AGES_MONTHS = (12, 24, 36, 48)
FIVE_DAYS_PER_WEEK = 5

_WEEKS_PER_YEAR = Decimal(52)
_MONTHS_PER_YEAR = Decimal(12)

_PERIOD_PHRASES: dict[str, str] = {
    PricePeriod.HOURLY: "per hour",
    PricePeriod.DAILY: "per day",
    PricePeriod.WEEKLY: "per week",
    PricePeriod.MONTHLY: "per month",
    PricePeriod.ANNUAL: "per year",
}

NOT_COMPUTABLE = "not computable"


@dataclasses.dataclass(frozen=True, slots=True)
class ComparisonCell:
    text: str
    # A second line, such as the free seats a competitor offers at the age.
    note: str


def monthly_equivalent(price: Observation) -> Decimal | None:
    """Return the price per month in whole dollars, halves up.

    None for an hourly price on a schedule that states no hours. The price's schedule
    must be loaded.
    """
    amount = _amount(price)
    schedule = price.schedule
    if schedule is None or schedule.days_per_week is None:
        raise ValueError("A price always refers to a schedule with its days per week.")
    days_per_week = Decimal(schedule.days_per_week)
    match price.price_period:
        case PricePeriod.MONTHLY:
            monthly = amount
        case PricePeriod.WEEKLY:
            monthly = amount * _WEEKS_PER_YEAR / _MONTHS_PER_YEAR
        case PricePeriod.DAILY:
            monthly = amount * days_per_week * _WEEKS_PER_YEAR / _MONTHS_PER_YEAR
        case PricePeriod.HOURLY:
            if schedule.hours_per_day is None:
                return None
            monthly = (
                amount
                * schedule.hours_per_day
                * days_per_week
                * _WEEKS_PER_YEAR
                / _MONTHS_PER_YEAR
            )
        case PricePeriod.ANNUAL:
            monthly = amount / _MONTHS_PER_YEAR
        case _:
            raise ValueError(f"Unknown price period {price.price_period!r}.")
    return monthly.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def age_group_contains(age_group: Observation, age_months: int) -> bool:
    """Return whether the age lies within the group's limits, both included."""
    youngest = age_group.youngest_age_months
    oldest = age_group.oldest_age_months
    return (
        youngest is not None
        and youngest <= age_months
        and (oldest is None or oldest >= age_months)
    )


def comparison_cell(current: CurrentValues, age_months: int) -> ComparisonCell:
    """Return the monthly equivalents of a competitor's 5-day prices at the age."""
    containing_ids = {
        age_group.pk
        for age_group in current.age_groups
        if age_group_contains(age_group, age_months)
    }
    if not containing_ids:
        return ComparisonCell(text="not offered", note="")
    five_day_ids = {
        schedule.pk
        for schedule in current.schedules
        if schedule.days_per_week == FIVE_DAYS_PER_WEEK
    }
    prices = [
        price
        for current_slot in current.slots.values()
        if current_slot.slot.kind == ObservationKind.PRICE
        for price in current_slot.observations
        if price.age_group_id in containing_ids and price.schedule_id in five_day_ids
    ]
    if not prices:
        return ComparisonCell(text="no 5-day price", note="")
    computable = [
        (amount, bool(price.includes_discounts))
        for price in prices
        if (amount := monthly_equivalent(price)) is not None
    ]
    if not computable:
        return ComparisonCell(text=NOT_COMPUTABLE, note="")
    amounts = [amount for amount, _ in computable]
    text = format_dollar_range(min(amounts), max(amounts))
    # The star comes only from the prices whose amounts the cell shows.
    if any(includes_discounts for _, includes_discounts in computable):
        text += "*"
    return ComparisonCell(text=text, note="")


def has_current_price(current: CurrentValues) -> bool:
    return any(slot.kind == ObservationKind.PRICE for slot in current.slots)


def format_stated_price(price: Observation) -> str:
    """Return a price as stated, such as "$2,450 per month", starred with discounts."""
    text = f"{format_money(_amount(price))} {_PERIOD_PHRASES[price.price_period]}"
    if price.includes_discounts:
        text += "*"
    return text


def format_monthly_equivalent(price: Observation) -> str:
    monthly = monthly_equivalent(price)
    if monthly is None:
        return NOT_COMPUTABLE
    return f"about {format_whole_dollars(monthly)}"


def _amount(price: Observation) -> Decimal:
    if price.amount is None:
        raise ValueError("A price always has an amount.")
    return price.amount
