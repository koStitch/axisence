"""Everything a teaser shows, built as display-ready text from public data as of the
cut-off. The only research fact read is a reported permanent closure, which leaves a
competitor out. Templates receive only these dataclasses, never a model instance.
"""

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from django.conf import settings

from axisence.ingestion.models import IngestionRun, IngestionSource
from axisence.providers.models import Competitor, Provider, ProviderKind
from axisence.reports import competitors, provider_runs
from axisence.reports.demographics import (
    DemographicsSection,
    load_teaser_demographics,
    survey_description,
)
from axisence.reports.errors import ReportRefusedError
from axisence.reports.formatting import (
    format_count,
    format_radius,
    format_report_date,
    format_short_date,
)
from axisence.reports.listed_competitors import ListedCompetitorRecord
from axisence.reports.models import Report
from axisence.reports.schematic_map import MarkSizes, ReportMap, build_report_map
from axisence.research.distances import format_miles
from axisence.research.jobs import tabulation_area_code
from axisence.research.labels import (
    CompetitorLabels,
    licensed_capacity,
    load_competitor_labels,
)
from axisence.research.merges import MergeChains
from axisence.research.models import ResearchJob
from axisence.research.search import find_competitors

NAMED_COMPETITOR_COUNT = 8
NO_COUNTED_COMPETITORS_MESSAGE = (
    "No competitors of the job's kinds lie within its search radius."
)

_SEVERAL_KINDS_NOTE = (
    "A competitor whose providers are of several kinds counts under each kind."
)
_CAPACITY_NOTE = (
    "Licensed capacity is the regulators' maximum, not enrollment or availability."
)
_OWN_PROGRAM_LINE = "Excludes your own program."
_WITHHELD_ADDRESS_LINE = (
    "Programs whose address the regulator withholds are not counted."
)
_NO_CENSUS_FIGURES_LINE = (
    "US Census Bureau, American Community Survey 5-year estimates: no figures shown "
    "in this preview."
)
# Home-based kinds, whose regulator can withhold the address; such a program has no
# location, so no search finds it.
_HOME_BASED_KINDS = frozenset(
    {ProviderKind.FAMILY_DAY_CARE, ProviderKind.GROUP_FAMILY_DAY_CARE}
)
# The sizes teaser_map.html draws its marks at. The map prints 2.8in wide, where
# 33-unit text prints at 6.65pt, just above the smallest text the page allows.
_MAP_MARK_SIZES = MarkSizes(
    marker_radius=27.0,
    anchor_radius=7.0,
    dot_radius=9.0,
    target_half_side=20.0,
    text_font_size=33.0,
    scale_label_raise=16.0,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ContactDetails:
    website_url: str
    email: str
    email_url: str
    phone: str


@dataclasses.dataclass(frozen=True, slots=True)
class KindCount:
    label: str
    count: str


@dataclasses.dataclass(frozen=True, slots=True)
class MarketSummary:
    headline: str
    kinds_counted: str
    # One per kind of the job, zeros included.
    kind_counts: tuple[KindCount, ...]
    # Empty unless a counted competitor counts under several of the job's kinds.
    several_kinds_note: str
    total_capacity: str
    incomplete_capacity: str
    capacity_note: str
    # Empty unless the job has an own competitor.
    own_program_line: str


@dataclasses.dataclass(frozen=True, slots=True)
class NamedCompetitor:
    number: int
    name: str
    is_school_based: bool
    kinds: str
    distance: str
    licensed_capacity: str


@dataclasses.dataclass(frozen=True, slots=True)
class TeaserSources:
    ny_state_as_of: str
    nyc_health_as_of: str
    census_line: str
    # Empty unless the job's kinds include a home-based kind.
    withheld_address_line: str


@dataclasses.dataclass(frozen=True, slots=True)
class TeaserContent:
    organization_name: str
    preview_date: str
    summary: MarketSummary
    market_map: ReportMap
    named_competitors: tuple[NamedCompetitor, ...]
    # Empty when every counted competitor is named.
    more_competitors_line: str
    demographics: DemographicsSection
    sources: TeaserSources
    # The earlier provider as-of date; empty when either source has no run.
    public_data_as_of: str


@dataclasses.dataclass(frozen=True, slots=True)
class BuiltTeaser:
    content: TeaserContent
    census_vintage: int | None
    tabulation_area_code: str
    ny_state_provider_run: IngestionRun | None
    nyc_health_provider_run: IngestionRun | None
    # A teaser compares with nothing, records no listed competitors, and reads no
    # transit.
    compared_report: Report | None = None
    listed_competitors: tuple[ListedCompetitorRecord, ...] = ()
    transit_run: IngestionRun | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _CountedCompetitor:
    competitor: Competitor
    distance_meters: float
    labels: CompetitorLabels
    active_providers: tuple[Provider, ...]


def load_contact_details() -> ContactDetails:
    """Return Axisence's contact details for the call to action.

    Refuses while any setting is blank, naming the blank variables, never a value.
    """
    values = (
        ("AXISENCE_WEBSITE_URL", settings.AXISENCE_WEBSITE_URL),
        ("AXISENCE_CONTACT_EMAIL", settings.AXISENCE_CONTACT_EMAIL),
        ("AXISENCE_CONTACT_PHONE", settings.AXISENCE_CONTACT_PHONE),
    )
    blank_names = [name for name, value in values if not value.strip()]
    if blank_names:
        raise ReportRefusedError(
            f"Axisence's contact details are not set: {', '.join(blank_names)}."
        )
    return ContactDetails(
        website_url=settings.AXISENCE_WEBSITE_URL,
        email=settings.AXISENCE_CONTACT_EMAIL,
        email_url=f"mailto:{settings.AXISENCE_CONTACT_EMAIL}",
        phone=settings.AXISENCE_CONTACT_PHONE,
    )


def build_teaser(*, job: ResearchJob, cut_off: datetime) -> BuiltTeaser:
    """Build a teaser's content as of the cut-off, in a fixed number of queries.

    Refuses a job without counted competitors, and a Census figure list that no longer
    matches the stored labels.
    """
    target = job.target_point
    if target is None:
        raise RuntimeError("A teaser's job always has a confirmed target point.")
    counted = _counted_competitors(job, cut_off)
    # Before the demographics, so this refusal comes ahead of the figure list's.
    if not counted:
        raise ReportRefusedError(NO_COUNTED_COMPETITORS_MESSAGE)
    named = counted[:NAMED_COMPETITOR_COUNT]
    area_code = tabulation_area_code(target)
    demographics = load_teaser_demographics(area_code)
    runs_by_source = provider_runs.load_latest_provider_runs(cut_off)
    ny_state_run = runs_by_source.get(IngestionSource.NY_STATE_PROVIDERS)
    nyc_health_run = runs_by_source.get(IngestionSource.NYC_HEALTH_PROVIDERS)
    content = TeaserContent(
        organization_name=job.organization_name,
        preview_date=format_report_date(cut_off),
        summary=_summary(job, counted),
        market_map=build_report_map(
            target,
            job.search_radius_miles,
            [
                (number, item.competitor.location)
                for number, item in enumerate(named, start=1)
                if item.competitor.location is not None
            ],
            [
                item.competitor.location
                for item in counted[NAMED_COMPETITOR_COUNT:]
                if item.competitor.location is not None
            ],
            mark_sizes=_MAP_MARK_SIZES,
        ),
        named_competitors=tuple(
            _named_competitor(number, item)
            for number, item in enumerate(named, start=1)
        ),
        more_competitors_line=_more_competitors_line(len(counted) - len(named)),
        demographics=demographics.section,
        sources=TeaserSources(
            ny_state_as_of=provider_runs.as_of_text(ny_state_run),
            nyc_health_as_of=provider_runs.as_of_text(nyc_health_run),
            census_line=(
                _NO_CENSUS_FIGURES_LINE
                if demographics.vintage is None
                else f"US Census Bureau, {survey_description(demographics.vintage)}."
            ),
            # Over the stored strings: a set of ProviderKind members and strs would
            # not type-check as an intersection.
            withheld_address_line=(
                _WITHHELD_ADDRESS_LINE
                if any(kind in _HOME_BASED_KINDS for kind in job.provider_kinds)
                else ""
            ),
        ),
        public_data_as_of=_public_data_as_of(ny_state_run, nyc_health_run),
    )
    return BuiltTeaser(
        content=content,
        census_vintage=demographics.vintage,
        tabulation_area_code=area_code or "",
        ny_state_provider_run=ny_state_run,
        nyc_health_provider_run=nyc_health_run,
    )


def _counted_competitors(
    job: ResearchJob, cut_off: datetime
) -> list[_CountedCompetitor]:
    """Return the search candidates without reported permanent closures, nearest
    first.
    """
    chains = MergeChains.load()
    found = find_competitors(job, chains)
    labels = load_competitor_labels(
        [competitor for competitor, _ in found], chains, as_of=cut_off
    )
    counted = []
    for competitor, distance_meters in found:
        competitor_labels = labels[competitor.pk]
        if competitors.is_reported_permanently_closed(competitor_labels):
            continue
        counted.append(
            _CountedCompetitor(
                competitor=competitor,
                distance_meters=distance_meters,
                labels=competitor_labels,
                active_providers=tuple(getattr(competitor, "active_providers", ())),
            )
        )
    return counted


def _summary(job: ResearchJob, counted: Sequence[_CountedCompetitor]) -> MarketSummary:
    job_kinds = [kind for kind in ProviderKind if kind in job.provider_kinds]
    # Only the job's kinds count: a center's school-age program, for example, is left
    # out of the counts, though the named list shows it among the center's kinds.
    job_kinds_by_competitor = [
        [
            kind
            for kind in competitors.kinds_of(item.active_providers)
            if kind in job_kinds
        ]
        for item in counted
    ]
    capacities = [licensed_capacity(item.active_providers) for item in counted]
    radius = format_radius(job.search_radius_miles)
    if len(counted) == 1:
        headline = f"1 competitor within {radius} of your location"
    else:
        headline = (
            f"{format_count(len(counted))} competitors within {radius} of your location"
        )
    total_capacity = sum(capacity.total or 0 for capacity in capacities)
    incomplete_count = sum(
        competitors.is_capacity_incomplete(capacity) for capacity in capacities
    )
    return MarketSummary(
        headline=headline,
        kinds_counted=f"Kinds counted: {competitors.kinds_label(job_kinds)}.",
        kind_counts=tuple(
            KindCount(
                label=kind.label,
                count=format_count(
                    sum(kind in kinds for kinds in job_kinds_by_competitor)
                ),
            )
            for kind in job_kinds
        ),
        several_kinds_note=(
            _SEVERAL_KINDS_NOTE
            if any(len(kinds) > 1 for kinds in job_kinds_by_competitor)
            else ""
        ),
        total_capacity=f"Total licensed capacity: {format_count(total_capacity)}",
        incomplete_capacity=(
            "Competitors with unpublished or incomplete capacity: "
            f"{format_count(incomplete_count)}"
        ),
        capacity_note=_CAPACITY_NOTE,
        own_program_line=(
            _OWN_PROGRAM_LINE if job.own_competitor_id is not None else ""
        ),
    )


def _named_competitor(number: int, item: _CountedCompetitor) -> NamedCompetitor:
    return NamedCompetitor(
        number=number,
        name=competitors.display_name_text(item.competitor),
        is_school_based=item.labels.is_school_based,
        kinds=competitors.kinds_label(competitors.kinds_of(item.active_providers)),
        distance=format_miles(item.distance_meters),
        licensed_capacity=competitors.capacity_text(
            licensed_capacity(item.active_providers)
        ),
    )


def _more_competitors_line(count: int) -> str:
    if count == 0:
        return ""
    if count == 1:
        return "1 more competitor within the radius appears as a dot on the map."
    return (
        f"{format_count(count)} more competitors within the radius appear as dots on "
        "the map."
    )


def _public_data_as_of(
    ny_state_run: IngestionRun | None, nyc_health_run: IngestionRun | None
) -> str:
    ny_state_date = provider_runs.as_of_date(ny_state_run)
    nyc_health_date = provider_runs.as_of_date(nyc_health_run)
    if ny_state_date is None or nyc_health_date is None:
        return ""
    return format_short_date(min(ny_state_date, nyc_health_date))
