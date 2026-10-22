"""The competitors a report presents, as of its cut-off: the main list, the reported
closures, their labels, and their profiles.

The texts that take a competitor's own parts also serve teasers, whose competitors are
search candidates.
"""

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from django.db.models import prefetch_related_objects

from axisence.providers.models import Competitor, Provider, ProviderKind
from axisence.reports.errors import ReportRefusedError
from axisence.reports.formatting import (
    EN_DASH,
    format_age_range,
    format_clock_time,
    format_closure,
    format_count,
    format_days_per_week,
    format_hours_per_day,
    format_money,
    format_month,
    format_month_count,
    format_plain_number,
    format_recorded,
    format_short_date,
)
from axisence.reports.prices import format_monthly_equivalent, format_stated_price
from axisence.research.current_values import (
    CurrentSlot,
    CurrentValues,
    Slot,
)
from axisence.research.distances import METERS_PER_MILE
from axisence.research.job_competitors import (
    DisplayedJobCompetitor,
    load_displayed_job_competitors,
)
from axisence.research.labels import (
    CompetitorLabels,
    LicensedCapacity,
    licensed_capacity,
    load_competitor_labels,
)
from axisence.research.merges import MergeChains
from axisence.research.models import (
    ChargeKind,
    Contact,
    DiscountKind,
    Observation,
    ObservationKind,
    OperatingStatus,
    ResearchJob,
    ResearchStatus,
)

NO_COMPETITORS_MESSAGE = "This job lists no competitors."

UNFINISHED_RESEARCH_STATUSES = frozenset(
    {ResearchStatus.TO_DO, ResearchStatus.IN_PROGRESS}
)
_YES_NO_FACTS = (
    (ObservationKind.THREE_K_FOR_ALL, "Offers 3-K for All seats"),
    (ObservationKind.PRE_K_FOR_ALL, "Offers Pre-K for All seats"),
    (ObservationKind.ACCEPTS_VOUCHERS, "Accepts child care vouchers"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class LoadedCompetitor:
    displayed: DisplayedJobCompetitor
    labels: CompetitorLabels
    providers: tuple[Provider, ...]
    active_providers: tuple[Provider, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ValueLine:
    text: str
    recorded: str


@dataclasses.dataclass(frozen=True, slots=True)
class PriceRow:
    schedule: str
    stated_price: str
    monthly_equivalent: str
    recorded: str


@dataclasses.dataclass(frozen=True, slots=True)
class PriceGroup:
    heading: str
    rows: tuple[PriceRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class FactLine:
    label: str
    value: str
    recorded: str


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedData:
    age_groups: tuple[ValueLine, ...]
    schedules: tuple[ValueLine, ...]
    price_groups: tuple[PriceGroup, ...]
    availability: tuple[ValueLine, ...]
    discounts: tuple[ValueLine, ...]
    other_charges: tuple[ValueLine, ...]
    facts: tuple[FactLine, ...]


def load_competitors(
    job: ResearchJob, chains: MergeChains, cut_off: datetime
) -> tuple[list[LoadedCompetitor], list[LoadedCompetitor]]:
    """Return the main list and the permanently closed ones, both in displayed order.

    Runs six queries for any number of competitors.
    """
    displayed = load_displayed_job_competitors(job, chains)
    if not displayed:
        raise ReportRefusedError(NO_COMPETITORS_MESSAGE)
    competitors = [item.competitor for item in displayed]
    labels = load_competitor_labels(competitors, chains, as_of=cut_off)
    prefetch_related_objects(competitors, "providers")
    main = []
    closed = []
    for item in displayed:
        competitor = item.competitor
        loaded = LoadedCompetitor(
            displayed=item,
            labels=labels[competitor.pk],
            providers=tuple(competitor.providers.all()),
            active_providers=tuple(getattr(competitor, "active_providers", ())),
        )
        if is_permanently_closed(loaded):
            closed.append(loaded)
        else:
            main.append(loaded)
    return main, closed


def load_observations(
    main: Sequence[LoadedCompetitor], chains: MergeChains
) -> dict[int, list[Observation]]:
    """Return every observation of each competitor and of the competitors merged into
    it, by current competitor, in one query, with their contacts and definitions
    loaded.
    """
    if not main:
        return {}
    current_ids = [loaded.displayed.competitor.pk for loaded in main]
    member_ids = set[int]().union(
        *(chains.member_ids(current_id) for current_id in current_ids)
    )
    observations_by_current_id: dict[int, list[Observation]] = {
        current_id: [] for current_id in current_ids
    }
    for observation in Observation.objects.filter(
        contact__competitor_id__in=member_ids
    ).select_related("contact", "age_group", "schedule"):
        observations_by_current_id[
            chains.current_id(observation.contact.competitor_id)
        ].append(observation)
    return observations_by_current_id


def is_reported_permanently_closed(labels: CompetitorLabels) -> bool:
    return (
        _reported_closure_contact(labels, OperatingStatus.PERMANENTLY_CLOSED)
        is not None
    )


def is_permanently_closed(loaded: LoadedCompetitor) -> bool:
    return is_reported_permanently_closed(loaded.labels)


def permanent_closure_text(loaded: LoadedCompetitor) -> str:
    contact = _reported_closure_contact(
        loaded.labels, OperatingStatus.PERMANENTLY_CLOSED
    )
    if contact is None:
        raise ValueError("The competitor is not reported permanently closed.")
    return format_closure("Reported permanently closed", contact)


def temporary_closure_contact(loaded: LoadedCompetitor) -> Contact | None:
    """Return the contact of a current temporary closure, if any."""
    return _reported_closure_contact(loaded.labels, OperatingStatus.TEMPORARILY_CLOSED)


def display_name_text(competitor: Competitor) -> str:
    return competitor.display_name.strip() or "Name not published"


def name_text(loaded: LoadedCompetitor) -> str:
    return display_name_text(loaded.displayed.competitor)


def address_text(loaded: LoadedCompetitor) -> str:
    address = loaded.displayed.competitor.street_address.strip()
    if address:
        return address
    if any(provider.is_address_omitted for provider in loaded.providers):
        return "Address withheld by the regulator"
    return "Address not published"


def borough_text(loaded: LoadedCompetitor) -> str:
    return loaded.displayed.competitor.get_borough_display()


def kinds_of(providers: Sequence[Provider]) -> tuple[ProviderKind, ...]:
    """Return the providers' kinds, once each, in ProviderKind order."""
    present = {provider.kind for provider in providers}
    return tuple(kind for kind in ProviderKind if kind in present)


def kinds_label(kinds: Sequence[ProviderKind]) -> str:
    return ", ".join(kind.label for kind in kinds)


def provider_kinds(loaded: LoadedCompetitor) -> tuple[ProviderKind, ...]:
    """Return the kinds of the active providers, or of all when none is active."""
    return kinds_of(loaded.active_providers or loaded.providers)


def kinds_text(loaded: LoadedCompetitor) -> str:
    return kinds_label(provider_kinds(loaded))


def is_capacity_incomplete(capacity: LicensedCapacity) -> bool:
    """Return whether the capacity is unpublished or only partly published."""
    return capacity.total is None or not capacity.is_complete


def has_incomplete_capacity(loaded: LoadedCompetitor) -> bool:
    """Return whether the capacity is unpublished, incomplete, or not listed."""
    if loaded.labels.is_no_longer_listed:
        return True
    return is_capacity_incomplete(licensed_capacity(loaded.active_providers))


def capacity_text(capacity: LicensedCapacity) -> str:
    if capacity.total is None:
        return "Unpublished"
    if not capacity.is_complete:
        return f"{format_count(capacity.total)} (incomplete)"
    return format_count(capacity.total)


def licensed_capacity_text(loaded: LoadedCompetitor) -> str:
    if loaded.labels.is_no_longer_listed:
        return "Not listed"
    return capacity_text(licensed_capacity(loaded.active_providers))


def ages_served_text(loaded: LoadedCompetitor) -> str:
    if loaded.labels.ages_served:
        return "; ".join(loaded.labels.ages_served)
    if loaded.labels.is_no_longer_listed:
        return "Not listed"
    return "Not published"


def state_labels(
    loaded: LoadedCompetitor, *, job: ResearchJob, is_draft: bool
) -> tuple[str, ...]:
    """Return the competitor's labels, in their fixed order."""
    labels = []
    temporary_closure = temporary_closure_contact(loaded)
    if temporary_closure is not None:
        labels.append(format_closure("Reported temporarily closed", temporary_closure))
    if loaded.labels.is_no_longer_listed:
        labels.append("No longer listed by its regulator")
    research_status = loaded.displayed.row.research_status
    if research_status == ResearchStatus.UNREACHABLE:
        labels.append("Not reached for this report")
    if is_draft and research_status in UNFINISHED_RESEARCH_STATUSES:
        labels.append("Research in progress")
    distance_meters = loaded.displayed.distance_meters
    if (
        distance_meters is not None
        and distance_meters > float(job.search_radius_miles) * METERS_PER_MILE
    ):
        labels.append("Outside the search radius")
    if loaded.labels.is_school_based:
        labels.append("School-based")
    labels.extend(loaded.labels.facility_statuses)
    return tuple(labels)


def research_outcome_text(
    loaded: LoadedCompetitor, current: CurrentValues, *, is_draft: bool
) -> str:
    match loaded.displayed.row.research_status:
        case ResearchStatus.DONE:
            outcome = "Researched"
        case ResearchStatus.UNREACHABLE:
            outcome = "Not reached"
        case _:
            outcome = "Research in progress" if is_draft else "Not researched"
    if not has_verified_information(current):
        outcome += ", no verified information"
    return outcome


def has_verified_information(current: CurrentValues) -> bool:
    """Return whether anything but an operating status is current.

    "Operating" is never shown, and a closure appears only as a label.
    """
    return bool(
        current.age_groups
        or current.schedules
        or any(slot.kind != ObservationKind.OPERATING_STATUS for slot in current.slots)
    )


def build_verified_data(current: CurrentValues) -> VerifiedData | None:
    """Return the competitor's verified values, each with how and when recorded."""
    if not has_verified_information(current):
        return None
    return VerifiedData(
        age_groups=tuple(
            ValueLine(
                text=f"{age_group.name}: {_age_range(age_group)}",
                recorded=format_recorded(age_group.contact),
            )
            for age_group in current.age_groups
        ),
        schedules=tuple(
            ValueLine(
                text=_schedule_text(schedule),
                recorded=format_recorded(schedule.contact),
            )
            for schedule in current.schedules
        ),
        price_groups=_price_groups(current),
        availability=_availability(current),
        discounts=tuple(
            ValueLine(
                text=_discount_text(observation),
                recorded=format_recorded(observation.contact),
            )
            for kind in DiscountKind
            for observation in _observations(
                current, Slot(ObservationKind.DISCOUNT, subkind=kind.value)
            )
        ),
        other_charges=tuple(
            ValueLine(
                text=_charge_text(observation),
                recorded=format_recorded(observation.contact),
            )
            for kind in ChargeKind
            for observation in _observations(
                current, Slot(ObservationKind.OTHER_CHARGE, subkind=kind.value)
            )
        ),
        facts=_facts(current),
    )


def current_yes_answer(current: CurrentValues, kind: ObservationKind) -> bool:
    """Return whether the current answer of a yes-or-no fact is Yes."""
    return any(
        observation.yes_no_answer is True
        for observation in _observations(current, Slot(kind))
    )


def _reported_closure_contact(
    labels: CompetitorLabels, operating_status: OperatingStatus
) -> Contact | None:
    closure = labels.reported_closure
    if closure is None or not any(
        observation.operating_status == operating_status
        for observation in closure.observations
    ):
        return None
    return closure.contact


def _observations(current: CurrentValues, slot: Slot) -> tuple[Observation, ...]:
    current_slot: CurrentSlot | None = current.slots.get(slot)
    return () if current_slot is None else current_slot.observations


def _stored[T](value: T | None) -> T:
    # The check constraints require these columns for their kind.
    if value is None:
        raise ValueError("An observation lacks a value its kind requires.")
    return value


def _age_range(age_group: Observation) -> str:
    return format_age_range(
        _stored(age_group.youngest_age_months), age_group.oldest_age_months
    )


def _schedule_text(schedule: Observation) -> str:
    text = f"{schedule.name}: {format_days_per_week(_stored(schedule.days_per_week))}"
    if schedule.hours_per_day is not None:
        text += f", {format_hours_per_day(schedule.hours_per_day)}"
    return text


def _price_groups(current: CurrentValues) -> tuple[PriceGroup, ...]:
    schedule_positions = {
        schedule.pk: position for position, schedule in enumerate(current.schedules)
    }
    prices_by_age_group_id: dict[int, list[Observation]] = {}
    for current_slot in current.slots.values():
        if current_slot.slot.kind != ObservationKind.PRICE:
            continue
        for price in current_slot.observations:
            prices_by_age_group_id.setdefault(_stored(price.age_group_id), []).append(
                price
            )
    groups = []
    for age_group in current.age_groups:
        prices = prices_by_age_group_id.get(age_group.pk)
        if not prices:
            continue
        prices.sort(
            key=lambda price: (
                schedule_positions[_stored(price.schedule_id)],
                price.pk,
            )
        )
        groups.append(
            PriceGroup(
                heading=f"{age_group.name} ({_age_range(age_group)})",
                rows=tuple(
                    PriceRow(
                        schedule=_stored(price.schedule).name,
                        stated_price=format_stated_price(price),
                        monthly_equivalent=format_monthly_equivalent(price),
                        recorded=format_recorded(price.contact),
                    )
                    for price in prices
                ),
            )
        )
    return tuple(groups)


def _availability(current: CurrentValues) -> tuple[ValueLine, ...]:
    lines = []
    for age_group in current.age_groups:
        for observation in _observations(
            current, Slot(ObservationKind.AVAILABILITY, age_group_id=age_group.pk)
        ):
            text = f"{age_group.name}: {observation.get_availability_status_display()}"
            if observation.earliest_start_on is not None:
                earliest_start = format_short_date(observation.earliest_start_on)
                text += f"; earliest start {earliest_start}"
            if observation.expected_wait_months is not None:
                expected_wait = format_month_count(observation.expected_wait_months)
                text += f"; expected wait {expected_wait}"
            lines.append(
                ValueLine(text=text, recorded=format_recorded(observation.contact))
            )
    return tuple(lines)


def _discount_text(observation: Observation) -> str:
    label = observation.get_discount_kind_display()
    if observation.is_none_offered:
        return f"{label}: None offered"
    if observation.percent is not None:
        value = f"{format_plain_number(observation.percent)}%"
    elif observation.amount is not None:
        value = format_money(observation.amount)
    else:
        value = ""
    return _offer_text(label, value, "", observation.description, "Offered")


def _charge_text(observation: Observation) -> str:
    label = observation.get_charge_kind_display()
    if observation.is_none_offered:
        return f"{label}: None offered"
    if observation.amount is not None:
        value = format_money(observation.amount)
    elif observation.months_of_tuition is not None:
        value = f"{format_month_count(observation.months_of_tuition)} of tuition"
    else:
        value = ""
    frequency = (
        observation.get_charge_frequency_display().lower()
        if observation.charge_frequency
        else ""
    )
    return _offer_text(label, value, frequency, observation.description, "Charged")


def _offer_text(
    label: str, value: str, frequency: str, description: str, unvalued_word: str
) -> str:
    """Join a discount's or charge's parts; one with no value or description says so."""
    if value:
        text = f"{label}: {value}"
    elif description:
        text = label
    else:
        text = f"{label}: {unvalued_word}"
    if frequency:
        text += f" {frequency}"
    if description:
        text += f" ({description})"
    return text


def _facts(current: CurrentValues) -> tuple[FactLine, ...]:
    facts = []

    def add(kind: ObservationKind, label: str, value: str) -> None:
        current_slot = current.slots[Slot(kind)]
        facts.append(
            FactLine(
                label=label, value=value, recorded=format_recorded(current_slot.contact)
            )
        )

    def single(kind: ObservationKind) -> Observation | None:
        observations = _observations(current, Slot(kind))
        return observations[0] if observations else None

    if (hours := single(ObservationKind.WEEKDAY_HOURS)) is not None:
        opens_at = format_clock_time(_stored(hours.opens_at))
        closes_at = format_clock_time(_stored(hours.closes_at))
        add(
            ObservationKind.WEEKDAY_HOURS,
            "Weekday hours",
            f"{opens_at}{EN_DASH}{closes_at}",
        )
    if (weeks := single(ObservationKind.WEEKS_OPEN)) is not None:
        add(
            ObservationKind.WEEKS_OPEN,
            "Weeks open per year",
            format_count(_stored(weeks.weeks_open_per_year)),
        )
    if (booking := single(ObservationKind.MINIMUM_BOOKING)) is not None:
        add(
            ObservationKind.MINIMUM_BOOKING,
            "Minimum booking",
            format_days_per_week(_stored(booking.days_per_week)),
        )
    if (fee_rise := single(ObservationKind.LAST_FEE_RISE)) is not None:
        add(
            ObservationKind.LAST_FEE_RISE,
            "Last fee rise",
            format_month(_stored(fee_rise.last_fee_rise_on)),
        )
    if (places := single(ObservationKind.STATED_PLACES)) is not None:
        add(
            ObservationKind.STATED_PLACES,
            "Places, as stated by the provider, not licensed capacity",
            format_count(_stored(places.stated_places)),
        )
    if (inspection := single(ObservationKind.INSPECTION_STATUS)) is not None:
        text = inspection.inspection_status
        if inspection.inspected_on is not None:
            text += f", inspected {format_short_date(inspection.inspected_on)}"
        add(ObservationKind.INSPECTION_STATUS, "Inspection status, as stated", text)
    if (parking := single(ObservationKind.PARKING)) is not None:
        text = parking.get_parking_display()
        if parking.description:
            text += f" ({parking.description})"
        add(ObservationKind.PARKING, "Parking", text)
    for kind, label in _YES_NO_FACTS:
        if (answer := single(kind)) is not None:
            add(kind, label, "Yes" if answer.yes_no_answer else "No")
    if (website := single(ObservationKind.WEBSITE)) is not None:
        add(ObservationKind.WEBSITE, "Website address", website.website_url)
    if (description := single(ObservationKind.DESCRIPTION)) is not None:
        add(
            ObservationKind.DESCRIPTION,
            "Description or unique selling point",
            description.description,
        )
    return tuple(facts)
