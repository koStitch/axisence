"""Everything a report shows, built as display-ready text from the database as of the
cut-off. Templates receive only these dataclasses, never a model instance.
"""

import dataclasses
from datetime import date, datetime

from django.db.models import Max, Min, Q
from django.utils import timezone

from axisence.ingestion.models import IngestionOutcome, IngestionRun, IngestionSource
from axisence.providers.models import ProviderKind
from axisence.reports import competitors
from axisence.reports.competitors import LoadedCompetitor, VerifiedData
from axisence.reports.demographics import (
    DemographicsSection,
    load_demographics,
    survey_description,
)
from axisence.reports.formatting import (
    format_count,
    format_radius,
    format_report_date,
    format_short_date,
)
from axisence.reports.models import ReportKind
from axisence.reports.prices import (
    REFERENCE_AGES_MONTHS,
    ComparisonCell,
    comparison_cell,
    has_current_price,
)
from axisence.reports.schematic_map import ReportMap, build_report_map
from axisence.research.current_values import CurrentValues
from axisence.research.distances import format_miles
from axisence.research.jobs import tabulation_area_code
from axisence.research.labels import licensed_capacity
from axisence.research.merges import MergeChains
from axisence.research.models import (
    AvailabilityStatus,
    Contact,
    ObservationKind,
    ResearchJob,
    ResearchStatus,
)

_THREE_K_FOR_ALL_NOTE = "3-K for All seats"
_PRE_K_FOR_ALL_NOTE = "Pre-K for All seats"
_THREE_K_FOR_ALL_AGE_MONTHS = 36
_PRE_K_FOR_ALL_AGE_MONTHS = 48
_OPENINGS_STATUSES = frozenset(
    {AvailabilityStatus.OPENINGS, AvailabilityStatus.LIMITED_OPENINGS}
)


@dataclasses.dataclass(frozen=True, slots=True)
class SummaryRow:
    label: str
    value: str


@dataclasses.dataclass(frozen=True, slots=True)
class SummarySection:
    rows: tuple[SummaryRow, ...]
    notes: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class CompetitorEntry:
    number: int
    name: str
    state_labels: tuple[str, ...]
    address: str
    borough: str
    distance: str
    kinds: str
    licensed_capacity: str
    ages_served: str
    facility_statuses: tuple[str, ...]
    is_school_based: bool
    research_outcome: str
    verified: VerifiedData | None


@dataclasses.dataclass(frozen=True, slots=True)
class ComparisonRow:
    number: int
    name: str
    distance: str
    # One cell per reference age, or none when the competitor has no current price.
    cells: tuple[ComparisonCell, ...]
    no_price_text: str


@dataclasses.dataclass(frozen=True, slots=True)
class ClosedCompetitorEntry:
    name: str
    address: str
    distance: str
    closure: str


@dataclasses.dataclass(frozen=True, slots=True)
class SourcesSection:
    ny_state_as_of: str
    nyc_health_as_of: str
    census_line: str


@dataclasses.dataclass(frozen=True, slots=True)
class ReportContent:
    is_draft: bool
    organization_name: str
    report_date: str
    cover_line: str
    summary: SummarySection
    report_map: ReportMap
    competitors: tuple[CompetitorEntry, ...]
    comparison_rows: tuple[ComparisonRow, ...]
    demographics: DemographicsSection
    reported_closed: tuple[ClosedCompetitorEntry, ...]
    sources: SourcesSection


@dataclasses.dataclass(frozen=True, slots=True)
class BuiltReport:
    content: ReportContent
    census_vintage: int | None
    tabulation_area_code: str
    ny_state_provider_run: IngestionRun | None
    nyc_health_provider_run: IngestionRun | None


def build_report(
    *, job: ResearchJob, kind: ReportKind, cut_off: datetime
) -> BuiltReport:
    """Build a report's content as of the cut-off, in a fixed number of queries."""
    is_draft = kind == ReportKind.DRAFT
    chains = MergeChains.load()
    main, closed = competitors.load_competitors(job, chains, cut_off)
    current_by_id = competitors.load_current_values_as_of(main, chains, cut_off)
    if job.target_point is None:
        raise RuntimeError(
            "A Researching or Complete job always has a confirmed target point."
        )
    area_code = tabulation_area_code(job.target_point)
    demographics = load_demographics(area_code)
    runs_by_source = {
        run.source: run
        for run in IngestionRun.objects.filter(
            source__in=(
                IngestionSource.NY_STATE_PROVIDERS,
                IngestionSource.NYC_HEALTH_PROVIDERS,
            ),
            outcome=IngestionOutcome.SUCCEEDED,
            finished_at__lte=cut_off,
        )
        .order_by("source", "-finished_at", "-pk")
        .distinct("source")
    }
    research_period = (
        Contact.objects.filter(job=job, entered_at__lte=cut_off)
        .filter(Q(retracted_at__isnull=True) | Q(retracted_at__gt=cut_off))
        .aggregate(earliest=Min("contact_date"), latest=Max("contact_date"))
    )
    numbered = list(enumerate(main, start=1))
    report_map = build_report_map(
        job.target_point,
        job.search_radius_miles,
        [
            (number, loaded.displayed.competitor.location)
            for number, loaded in numbered
            if loaded.displayed.competitor.location is not None
        ],
    )
    ny_state_run = runs_by_source.get(IngestionSource.NY_STATE_PROVIDERS)
    nyc_health_run = runs_by_source.get(IngestionSource.NYC_HEALTH_PROVIDERS)
    content = ReportContent(
        is_draft=is_draft,
        organization_name=job.organization_name,
        report_date=format_report_date(cut_off),
        cover_line=_cover_line(len(main), area_code, job),
        summary=_summary(
            main,
            closed,
            current_by_id,
            is_draft=is_draft,
            research_period=_research_period_text(
                research_period["earliest"], research_period["latest"]
            ),
        ),
        report_map=report_map,
        competitors=tuple(
            _competitor_entry(
                number,
                loaded,
                current_by_id[loaded.displayed.competitor.pk],
                job=job,
                is_draft=is_draft,
            )
            for number, loaded in numbered
        ),
        comparison_rows=tuple(
            _comparison_row(
                number, loaded, current_by_id[loaded.displayed.competitor.pk]
            )
            for number, loaded in numbered
        ),
        demographics=demographics.section,
        reported_closed=tuple(
            ClosedCompetitorEntry(
                name=competitors.name_text(loaded),
                address=competitors.address_text(loaded),
                distance=format_miles(loaded.displayed.distance_meters),
                closure=competitors.permanent_closure_text(loaded),
            )
            for loaded in closed
        ),
        sources=SourcesSection(
            ny_state_as_of=_provider_as_of(ny_state_run),
            nyc_health_as_of=_provider_as_of(nyc_health_run),
            census_line=(
                "US Census Bureau, American Community Survey 5-year estimates: no "
                "figures shown in this report."
                if demographics.vintage is None
                else f"US Census Bureau, {survey_description(demographics.vintage)}."
            ),
        ),
    )
    return BuiltReport(
        content=content,
        census_vintage=demographics.vintage,
        tabulation_area_code=area_code or "",
        ny_state_provider_run=ny_state_run,
        nyc_health_provider_run=nyc_health_run,
    )


def _cover_line(count: int, area_code: str | None, job: ResearchJob) -> str:
    competitor_word = "competitor" if count == 1 else "competitors"
    area = "" if area_code is None else f" in ZIP Code Tabulation Area {area_code}"
    return (
        f"{count} {competitor_word} near the target location{area}; search radius "
        f"{format_radius(job.search_radius_miles)}"
    )


def _summary(
    main: list[LoadedCompetitor],
    closed: list[LoadedCompetitor],
    current_by_id: dict[int, CurrentValues],
    *,
    is_draft: bool,
    research_period: str,
) -> SummarySection:
    kinds_by_competitor = [competitors.provider_kinds(loaded) for loaded in main]
    statuses = [loaded.displayed.row.research_status for loaded in main]
    rows = [SummaryRow("Competitors", format_count(len(main)))]
    for kind in ProviderKind:
        count = sum(kind in kinds for kinds in kinds_by_competitor)
        if count:
            rows.append(SummaryRow(kind.label, format_count(count)))
    rows.append(
        SummaryRow("Researched", format_count(statuses.count(ResearchStatus.DONE)))
    )
    rows.append(
        SummaryRow(
            "Not reached", format_count(statuses.count(ResearchStatus.UNREACHABLE))
        )
    )
    rows.append(
        SummaryRow(
            "Reported temporarily closed",
            format_count(
                sum(
                    competitors.temporary_closure_contact(loaded) is not None
                    for loaded in main
                )
            ),
        )
    )
    if is_draft:
        rows.append(
            SummaryRow(
                "Research in progress",
                format_count(
                    sum(
                        status in competitors.UNFINISHED_RESEARCH_STATUSES
                        for status in statuses
                    )
                ),
            )
        )
    if closed:
        rows.append(
            SummaryRow(
                "Reported permanently closed, listed under Reported closed",
                format_count(len(closed)),
            )
        )
    rows.append(
        SummaryRow(
            "Total licensed capacity",
            format_count(
                sum(
                    licensed_capacity(loaded.active_providers).total or 0
                    for loaded in main
                    if not loaded.labels.is_no_longer_listed
                )
            ),
        )
    )
    rows.append(
        SummaryRow(
            "Competitors with unpublished, incomplete, or no listed capacity",
            format_count(
                sum(competitors.has_incomplete_capacity(loaded) for loaded in main)
            ),
        )
    )
    availability_statuses = [
        _availability_statuses(current_by_id[loaded.displayed.competitor.pk])
        for loaded in main
    ]
    rows.append(
        SummaryRow(
            "With openings in at least one age group",
            format_count(
                sum(bool(found & _OPENINGS_STATUSES) for found in availability_statuses)
            ),
        )
    )
    rows.append(
        SummaryRow(
            "With a waitlist in at least one age group",
            format_count(
                sum(
                    AvailabilityStatus.WAITLIST in found
                    for found in availability_statuses
                )
            ),
        )
    )
    rows.append(SummaryRow("Research period", research_period))
    return SummarySection(
        rows=tuple(rows),
        notes=(
            "A competitor whose providers are of several kinds counts under each kind.",
            "Licensed capacity is the regulators' maximum, not enrollment or "
            "availability.",
        ),
    )


def _availability_statuses(current: CurrentValues) -> set[str]:
    return {
        observation.availability_status
        for current_slot in current.slots.values()
        if current_slot.slot.kind == ObservationKind.AVAILABILITY
        for observation in current_slot.observations
    }


def _research_period_text(earliest: date | None, latest: date | None) -> str:
    if earliest is None or latest is None:
        return "No contacts recorded for this job"
    if earliest == latest:
        return format_short_date(earliest)
    return f"{format_short_date(earliest)} to {format_short_date(latest)}"


def _competitor_entry(
    number: int,
    loaded: LoadedCompetitor,
    current: CurrentValues,
    *,
    job: ResearchJob,
    is_draft: bool,
) -> CompetitorEntry:
    return CompetitorEntry(
        number=number,
        name=competitors.name_text(loaded),
        state_labels=competitors.state_labels(loaded, job=job, is_draft=is_draft),
        address=competitors.address_text(loaded),
        borough=competitors.borough_text(loaded),
        distance=format_miles(loaded.displayed.distance_meters),
        kinds=competitors.kinds_text(loaded),
        licensed_capacity=competitors.licensed_capacity_text(loaded),
        ages_served=competitors.ages_served_text(loaded),
        facility_statuses=loaded.labels.facility_statuses,
        is_school_based=loaded.labels.is_school_based,
        research_outcome=competitors.research_outcome_text(
            loaded, current, is_draft=is_draft
        ),
        verified=competitors.build_verified_data(current),
    )


def _comparison_row(
    number: int, loaded: LoadedCompetitor, current: CurrentValues
) -> ComparisonRow:
    offers_three_k = competitors.current_yes_answer(
        current, ObservationKind.THREE_K_FOR_ALL
    )
    offers_pre_k = competitors.current_yes_answer(
        current, ObservationKind.PRE_K_FOR_ALL
    )
    name = competitors.name_text(loaded)
    distance = format_miles(loaded.displayed.distance_meters)
    if not has_current_price(current):
        no_price_text = "No verified prices"
        if offers_three_k:
            no_price_text += f"; {_THREE_K_FOR_ALL_NOTE}"
        if offers_pre_k:
            no_price_text += f"; {_PRE_K_FOR_ALL_NOTE}"
        return ComparisonRow(
            number=number,
            name=name,
            distance=distance,
            cells=(),
            no_price_text=no_price_text,
        )
    cells = []
    for age_months in REFERENCE_AGES_MONTHS:
        cell = comparison_cell(current, age_months)
        if age_months == _THREE_K_FOR_ALL_AGE_MONTHS and offers_three_k:
            cell = dataclasses.replace(cell, note=_THREE_K_FOR_ALL_NOTE)
        if age_months == _PRE_K_FOR_ALL_AGE_MONTHS and offers_pre_k:
            cell = dataclasses.replace(cell, note=_PRE_K_FOR_ALL_NOTE)
        cells.append(cell)
    return ComparisonRow(
        number=number,
        name=name,
        distance=distance,
        cells=tuple(cells),
        no_price_text="",
    )


def _provider_as_of(run: IngestionRun | None) -> str:
    if run is not None and run.publisher_rows_updated_at is not None:
        published_on = timezone.localdate(run.publisher_rows_updated_at)
        return f"published data as of {format_short_date(published_on)}"
    if run is not None and run.finished_at is not None:
        return f"loaded on {format_short_date(timezone.localdate(run.finished_at))}"
    return "not loaded"
