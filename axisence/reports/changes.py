"""The "Changes since report #N" section of a repeat job's report, built as
display-ready text as of both cut-offs. Templates receive only these dataclasses.

Both sides are computed with the current-value rules from the observations the report
already loaded, as of the compared report's cut-off and as of this one, with the merge
chains of this generation. The compared report's file is never read.
"""

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from datetime import datetime

from django.contrib.gis.db.models.functions import Distance

from axisence.providers.models import Competitor, ProviderKind
from axisence.reports.competitors import (
    LoadedCompetitor,
    display_name_text,
    name_text,
)
from axisence.reports.formatting import (
    format_closure,
    format_money,
    format_month_count,
    format_plain_number,
    format_radius,
    format_recorded,
    format_report_date,
    format_short_date,
)
from axisence.reports.models import ListedCompetitor, Report, ReportListing
from axisence.reports.prices import (
    REFERENCE_AGES_MONTHS,
    comparison_cell,
    format_stated_price,
    has_current_price,
)
from axisence.research.current_values import (
    CurrentSlot,
    CurrentValues,
    compute_current_values,
    contact_recency,
    is_counting,
    load_current_operating_statuses,
)
from axisence.research.distances import METERS_PER_MILE, format_miles
from axisence.research.merges import MergeChains
from axisence.research.models import (
    ChargeKind,
    Contact,
    DiscountKind,
    Observation,
    ObservationKind,
    OperatingStatus,
    ResearchJob,
)
from axisence.research.stated_values import (
    VALUE_SLOT_KINDS,
    loaded_definition,
    slot_stated_value,
    value_slot_sort_key,
)

# A target moved less than this, such as by a re-placed pin, is not a change.
_MOVED_TARGET_MILES = 0.005
_NO_PRICES_TEXT = "No verified prices"
_NONE_OFFERED_CELL = ("None offered",)
_DECLINING_KINDS = frozenset({ObservationKind.DISCOUNT, ObservationKind.OTHER_CHARGE})
_VALUES_COMPARED_NOTE = (
    "Values are compared as stated: a price by its amount, period, and whether the "
    "amount includes discounts; availability by its status; a discount or charge by "
    "its value and frequency. Descriptions, earliest start dates, and expected waits "
    "are not compared."
)
_TUITION_NOTE = (
    "Tuition at the reference ages compares the monthly equivalents of 5-day prices, "
    "as in the tuition comparison, so it bridges changes in a provider's own age "
    "groups and schedules."
)
_SEARCH_SETTINGS_NOTE = (
    "Changes to the search settings can explain competitors added or gone."
)


@dataclasses.dataclass(frozen=True, slots=True)
class ListedChange:
    number: int
    name: str
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class ValueChangeRow:
    value: str
    earlier: tuple[str, ...]
    this: tuple[str, ...]
    change: str


@dataclasses.dataclass(frozen=True, slots=True)
class ContinuingCompetitor:
    heading: str
    verified_line: str
    rows: tuple[ValueChangeRow, ...]
    # Empty when nothing was re-confirmed or left unchecked.
    counts_line: str


@dataclasses.dataclass(frozen=True, slots=True)
class ChangesSection:
    heading: str
    opening: str
    # Empty when the search settings did not change.
    search_settings: tuple[str, ...]
    search_settings_note: str
    added: tuple[ListedChange, ...]
    gone: tuple[ListedChange, ...]
    gone_number_heading: str
    continuing: tuple[ContinuingCompetitor, ...]
    compared_column_heading: str
    notes: tuple[str, ...]


class _SideState(enum.Enum):
    """What one side holds in a value slot."""

    ABSENT = enum.auto()
    PRESENT = enum.auto()
    # A discount or charge recorded as none offered.
    DECLINING = enum.auto()


class _ValueClass(enum.Enum):
    CHANGED = "Changed"
    RECONFIRMED = "Re-confirmed"
    NOT_RECHECKED = "Not re-checked"
    NEW = "New"
    ENDED = "Ended"


# The classes that show as rows, the others only in the counts line.
_LISTED_CLASSES = frozenset({_ValueClass.CHANGED, _ValueClass.NEW, _ValueClass.ENDED})


def compared_report_line(compared_report: Report) -> str:
    """Return the cover line of a report that has a compared report."""
    return (
        f"Follows report #{compared_report.pk} of "
        f"{format_report_date(compared_report.generated_at)}"
    )


def build_changes_section(
    *,
    job: ResearchJob,
    compared_report: Report,
    main: Sequence[LoadedCompetitor],
    observations_by_competitor_id: Mapping[int, Sequence[Observation]],
    current_by_id: Mapping[int, CurrentValues],
    chains: MergeChains,
    cut_off: datetime,
) -> ChangesSection:
    """Compare this report with the compared report, in a fixed number of queries.

    The compared report's job must be loaded.
    """
    compared_cut_off = compared_report.generated_at
    compared_number = compared_report.pk
    entries = list(
        ListedCompetitor.objects.filter(
            report=compared_report, listing=ReportListing.MAIN
        )
        .select_related("competitor")
        .order_by("number")
    )
    numbered = {
        loaded.displayed.competitor.pk: (number, loaded)
        for number, loaded in enumerate(main, start=1)
    }
    resolved_id_by_entry = {
        entry.pk: chains.current_id(entry.competitor_id) for entry in entries
    }
    continuing_ids = {
        resolved for resolved in resolved_id_by_entry.values() if resolved in numbered
    }
    added = tuple(
        ListedChange(
            number=number,
            name=name_text(loaded),
            reason=_added_reason(loaded, compared_cut_off, compared_number),
        )
        for competitor_id, (number, loaded) in numbered.items()
        if competitor_id not in continuing_ids
    )
    gone, has_merge = _gone(entries, resolved_id_by_entry, numbered, chains, cut_off)
    search_settings = _search_settings(job, compared_report.job)
    notes = [_VALUES_COMPARED_NOTE, _TUITION_NOTE]
    if has_merge:
        notes.append(
            f"A competitor merged since report #{compared_number} is compared with "
            "the combined values of the competitors merged into it."
        )
    return ChangesSection(
        heading=f"Changes since report #{compared_number}",
        opening=(
            f"Compared with report #{compared_number} of "
            f"{format_report_date(compared_cut_off)}. Values are compared as they "
            "stood when each report was generated."
        ),
        search_settings=search_settings,
        search_settings_note=_SEARCH_SETTINGS_NOTE if search_settings else "",
        added=added,
        gone=gone,
        gone_number_heading=f"No. in report #{compared_number}",
        continuing=tuple(
            _continuing_competitor(
                number,
                loaded,
                observations_by_competitor_id[competitor_id],
                current_by_id[competitor_id],
                compared_cut_off=compared_cut_off,
                compared_number=compared_number,
                cut_off=cut_off,
            )
            for competitor_id, (number, loaded) in numbered.items()
            if competitor_id in continuing_ids
        ),
        compared_column_heading=f"Report #{compared_number}",
        notes=tuple(notes),
    )


def _added_reason(
    loaded: LoadedCompetitor, compared_cut_off: datetime, compared_number: int
) -> str:
    # A competitor without any provider was never listed by a regulator.
    if loaded.providers and all(
        provider.first_seen_at > compared_cut_off for provider in loaded.providers
    ):
        return f"Newly listed by its regulator since report #{compared_number}"
    return "Newly included"


def _gone(
    entries: Sequence[ListedCompetitor],
    resolved_id_by_entry: Mapping[int, int],
    numbered: Mapping[int, tuple[int, LoadedCompetitor]],
    chains: MergeChains,
    cut_off: datetime,
) -> tuple[tuple[ListedChange, ...], bool]:
    """Return the compared report's main-list entries that this report does not
    continue, in their order, and whether any of them was merged.

    An entry merged into a competitor of this report's main list is gone as merged;
    the others are gone for the first reason that applies to the competitor they
    resolve to.
    """
    other_ids = {
        resolved_id_by_entry[entry.pk]
        for entry in entries
        if resolved_id_by_entry[entry.pk] not in numbered
    }
    competitors: dict[int, Competitor] = {}
    statuses: dict[int, CurrentSlot] = {}
    if other_ids:
        competitors = Competitor.objects.in_bulk(other_ids)
        statuses = load_current_operating_statuses(other_ids, chains, as_of=cut_off)
    gone = []
    has_merge = False
    for entry in entries:
        resolved = resolved_id_by_entry[entry.pk]
        if resolved in numbered:
            if entry.competitor_id == resolved:
                continue
            receiving_number, receiving = numbered[resolved]
            reason = (
                f"Merged into {name_text(receiving)}, number {receiving_number} in "
                "this report"
            )
            has_merge = True
        else:
            reason = _other_gone_reason(competitors[resolved], statuses.get(resolved))
        if entry.number is None:
            raise RuntimeError("A main-list entry always has a number.")
        gone.append(
            ListedChange(
                number=entry.number,
                name=display_name_text(entry.competitor),
                reason=reason,
            )
        )
    return tuple(gone), has_merge


def _other_gone_reason(
    competitor: Competitor, operating_status: CurrentSlot | None
) -> str:
    if operating_status is not None and any(
        observation.operating_status == OperatingStatus.PERMANENTLY_CLOSED
        for observation in operating_status.observations
    ):
        return format_closure("Reported permanently closed", operating_status.contact)
    if not competitor.is_active:
        return "No longer listed by its regulator"
    return "Not included in this report"


def _search_settings(job: ResearchJob, earlier_job: ResearchJob) -> tuple[str, ...]:
    """Return what changed in the search settings, in one query."""
    lines = []
    if earlier_job.search_radius_miles != job.search_radius_miles:
        lines.append(
            "The search radius changed from "
            f"{format_radius(earlier_job.search_radius_miles)} to "
            f"{format_radius(job.search_radius_miles)}."
        )
    added_kinds = [
        kind
        for kind in ProviderKind
        if kind in job.provider_kinds and kind not in earlier_job.provider_kinds
    ]
    removed_kinds = [
        kind
        for kind in ProviderKind
        if kind in earlier_job.provider_kinds and kind not in job.provider_kinds
    ]
    if added_kinds:
        labels = ", ".join(kind.label for kind in added_kinds)
        lines.append(f"Provider kinds added: {labels}.")
    if removed_kinds:
        labels = ", ".join(kind.label for kind in removed_kinds)
        lines.append(f"Provider kinds removed: {labels}.")
    # Measured on the geography columns, like every distance a report prints.
    distance = (
        ResearchJob.objects.filter(pk=earlier_job.pk)
        .annotate(distance=Distance("target_point", job.target_point))
        .values_list("distance", flat=True)
        .get()
    )
    if distance is not None and distance.m >= _MOVED_TARGET_MILES * METERS_PER_MILE:
        lines.append(
            f"The target location moved {format_miles(distance.m)} (straight line)."
        )
    return tuple(lines)


def _continuing_competitor(
    number: int,
    loaded: LoadedCompetitor,
    observations: Sequence[Observation],
    this: CurrentValues,
    *,
    compared_cut_off: datetime,
    compared_number: int,
    cut_off: datetime,
) -> ContinuingCompetitor:
    earlier = compute_current_values(observations, as_of=compared_cut_off)
    verified_again = _verified_again(observations, compared_cut_off, cut_off)
    rows = list(_tuition_rows(earlier, this))
    listed: list[tuple[tuple[int | str, ...], ValueChangeRow]] = []
    counts = dict.fromkeys(_ValueClass, 0)
    for slot in {*earlier.slots, *this.slots}:
        if slot.kind not in VALUE_SLOT_KINDS:
            continue
        earlier_slot = earlier.slots.get(slot)
        this_slot = this.slots.get(slot)
        earlier_state = _side_state(earlier_slot)
        this_state = _side_state(this_slot)
        value_class = _value_class(
            earlier_slot, this_slot, earlier_state, this_state, compared_cut_off
        )
        if value_class is None:
            continue
        counts[value_class] += 1
        if value_class not in _LISTED_CLASSES:
            continue
        # The slot came from one side at least, so a side without it has the other.
        named_slot = this_slot if this_slot is not None else earlier.slots[slot]
        first = named_slot.observations[0]
        listed.append(
            (
                value_slot_sort_key(first),
                ValueChangeRow(
                    value=_value_name(first),
                    earlier=_earlier_cells(value_class, earlier_slot, earlier_state),
                    this=_this_cells(value_class, this_slot, this_state),
                    change=value_class.value,
                ),
            )
        )
    listed.sort(key=lambda item: item[0])
    rows.extend(row for _, row in listed)
    count_parts = [
        _count_text(counts[value_class], description)
        for value_class, description in (
            (_ValueClass.RECONFIRMED, "re-confirmed unchanged"),
            (_ValueClass.NOT_RECHECKED, "not re-checked"),
        )
        if counts[value_class]
    ]
    return ContinuingCompetitor(
        heading=f"{number}. {name_text(loaded)}",
        verified_line=(
            f"Not verified again since report #{compared_number}."
            if verified_again is None
            else f"Verified again: {format_recorded(verified_again)}"
        ),
        rows=tuple(rows),
        counts_line=f"{'; '.join(count_parts)}." if count_parts else "",
    )


def _verified_again(
    observations: Sequence[Observation], compared_cut_off: datetime, cut_off: datetime
) -> Contact | None:
    """Return the newest contact entered between the cut-offs with a counting
    observation, if any.
    """
    contacts = [
        observation.contact
        for observation in observations
        if compared_cut_off < observation.contact.entered_at <= cut_off
        and is_counting(observation, cut_off)
    ]
    return max(contacts, key=contact_recency, default=None)


def _tuition_rows(earlier: CurrentValues, this: CurrentValues) -> list[ValueChangeRow]:
    rows = []
    for age_months in REFERENCE_AGES_MONTHS:
        earlier_text = _tuition_text(earlier, age_months)
        this_text = _tuition_text(this, age_months)
        if earlier_text != this_text:
            rows.append(
                ValueChangeRow(
                    value=f"Tuition at {age_months} months",
                    earlier=(earlier_text,),
                    this=(this_text,),
                    change=_ValueClass.CHANGED.value,
                )
            )
    return rows


def _tuition_text(current: CurrentValues, age_months: int) -> str:
    if not has_current_price(current):
        return _NO_PRICES_TEXT
    return comparison_cell(current, age_months).text


def _side_state(current_slot: CurrentSlot | None) -> _SideState:
    if current_slot is None:
        return _SideState.ABSENT
    if current_slot.slot.kind in _DECLINING_KINDS and all(
        observation.is_none_offered for observation in current_slot.observations
    ):
        return _SideState.DECLINING
    return _SideState.PRESENT


def _value_class(
    earlier_slot: CurrentSlot | None,
    this_slot: CurrentSlot | None,
    earlier_state: _SideState,
    this_state: _SideState,
    compared_cut_off: datetime,
) -> _ValueClass | None:
    """Classify a value slot by both sides; None for a slot neither listed nor
    counted.
    """
    if (
        earlier_slot is not None
        and this_slot is not None
        and earlier_state == this_state
    ):
        # Both present, or both declining.
        if slot_stated_value(earlier_slot.observations) != slot_stated_value(
            this_slot.observations
        ):
            return _ValueClass.CHANGED
        if this_slot.contact.entered_at > compared_cut_off:
            return _ValueClass.RECONFIRMED
        return _ValueClass.NOT_RECHECKED
    if this_state == _SideState.PRESENT:
        return _ValueClass.NEW
    if earlier_state == _SideState.PRESENT:
        return _ValueClass.ENDED
    # Declining on one side and absent on the other.
    return None


def _earlier_cells(
    value_class: _ValueClass, earlier_slot: CurrentSlot | None, state: _SideState
) -> tuple[str, ...]:
    if value_class == _ValueClass.NEW or earlier_slot is None:
        if state == _SideState.DECLINING:
            return _NONE_OFFERED_CELL
        return ("Not recorded",)
    return _value_cells(earlier_slot)


def _this_cells(
    value_class: _ValueClass, this_slot: CurrentSlot | None, state: _SideState
) -> tuple[str, ...]:
    if value_class == _ValueClass.ENDED or this_slot is None:
        if state == _SideState.DECLINING:
            return _NONE_OFFERED_CELL
        return ("No longer current",)
    return _value_cells(this_slot)


def _value_cells(current_slot: CurrentSlot) -> tuple[str, ...]:
    return tuple(_value_cell(observation) for observation in current_slot.observations)


def _value_cell(observation: Observation) -> str:
    match observation.kind:
        case ObservationKind.PRICE:
            return format_stated_price(observation)
        case ObservationKind.AVAILABILITY:
            text = observation.get_availability_status_display()
            if observation.earliest_start_on is not None:
                earliest_start = format_short_date(observation.earliest_start_on)
                text += f", earliest start {earliest_start}"
            if observation.expected_wait_months is not None:
                expected_wait = format_month_count(observation.expected_wait_months)
                text += f", expected wait {expected_wait}"
            return text
        case ObservationKind.DISCOUNT:
            if observation.percent is not None:
                text = f"{format_plain_number(observation.percent)}%"
            elif observation.amount is not None:
                text = format_money(observation.amount)
            else:
                text = "Offered"
            if observation.description:
                text += f" ({observation.description})"
            return text
    if observation.amount is not None:
        text = format_money(observation.amount)
    elif observation.months_of_tuition is not None:
        text = f"{format_month_count(observation.months_of_tuition)} of tuition"
    else:
        text = "Charged"
    if observation.charge_frequency:
        text += f" {observation.get_charge_frequency_display().lower()}"
    if observation.description:
        text += f" ({observation.description})"
    return text


def _value_name(observation: Observation) -> str:
    match observation.kind:
        case ObservationKind.PRICE:
            age_group = loaded_definition(observation.age_group)
            schedule = loaded_definition(observation.schedule)
            return (
                f"{age_group.name} ({age_group.age_range_summary()}), {schedule.name}"
            )
        case ObservationKind.AVAILABILITY:
            age_group = loaded_definition(observation.age_group)
            return f"Availability, {age_group.name} ({age_group.age_range_summary()})"
        case ObservationKind.DISCOUNT:
            return f"{DiscountKind(observation.discount_kind).label} discount"
    return f"{ChargeKind(observation.charge_kind).label} charge"


def _count_text(count: int, description: str) -> str:
    return f"{count} {'value' if count == 1 else 'values'} {description}"
