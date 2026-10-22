"""The transit run a client report reads, and its transit access: the nearest subway
stations and the bus lines near the target location and each listed competitor.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime

from django.contrib.gis.geos import Point

from axisence.ingestion.models import IngestionOutcome, IngestionRun, IngestionSource
from axisence.reports import competitors
from axisence.reports.competitors import LoadedCompetitor
from axisence.reports.formatting import format_report_date
from axisence.research.distances import format_miles
from axisence.transit import nearby
from axisence.transit.nearby import NearbyTransit

NO_TRANSIT_RUN_MESSAGE = (
    "Transit data has not been loaded: run ingest_mta_transit first."
)
TARGET_LOCATION_LABEL = "Target location"
NO_LOCATION_TEXT = "No location"
NO_STATION_TEXT = "None within 0.5 mi"
NO_BUS_LINE_TEXT = "None within 0.25 mi"


@dataclasses.dataclass(frozen=True, slots=True)
class TransitTexts:
    # One entry per station group, nearest first.
    subway_entries: tuple[str, ...]
    subway_text: str
    # One entry per bus line, nearest first; the templates keep each entry on one
    # line, so a long list wraps only between entries.
    bus_entries: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class TransitRow:
    # Empty for the target location.
    number: str
    location: str
    texts: TransitTexts


@dataclasses.dataclass(frozen=True, slots=True)
class TransitSection:
    rows: tuple[TransitRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class TransitAccess:
    section: TransitSection
    texts_by_competitor_id: Mapping[int, TransitTexts]


_NO_LOCATION_TEXTS = TransitTexts(
    subway_entries=(NO_LOCATION_TEXT,),
    subway_text=NO_LOCATION_TEXT,
    bus_entries=(NO_LOCATION_TEXT,),
)


def load_latest_transit_run(cut_off: datetime) -> IngestionRun | None:
    """Return the latest succeeded transit run finished by the cut-off."""
    return (
        IngestionRun.objects.filter(
            source=IngestionSource.MTA_TRANSIT,
            outcome=IngestionOutcome.SUCCEEDED,
            finished_at__lte=cut_off,
        )
        .order_by("-finished_at", "-pk")
        .first()
    )


def build_transit_access(
    target_point: Point, main: Sequence[LoadedCompetitor]
) -> TransitAccess:
    """Build the section's rows, the target location first, and each competitor's
    profile texts, in two queries whatever the number of competitors and stops.
    """
    located: list[tuple[LoadedCompetitor, Point]] = []
    for loaded in main:
        location = loaded.displayed.competitor.location
        if location is not None:
            located.append((loaded, location))
    found = nearby.find_nearby_transit(
        [target_point, *(location for _, location in located)]
    )
    texts_by_competitor_id = {
        loaded.displayed.competitor.pk: _texts(transit)
        for (loaded, _), transit in zip(located, found[1:], strict=True)
    }
    for loaded in main:
        texts_by_competitor_id.setdefault(
            loaded.displayed.competitor.pk, _NO_LOCATION_TEXTS
        )
    rows = (
        TransitRow("", TARGET_LOCATION_LABEL, _texts(found[0])),
        *(
            TransitRow(
                str(number),
                competitors.name_text(loaded),
                texts_by_competitor_id[loaded.displayed.competitor.pk],
            )
            for number, loaded in enumerate(main, start=1)
        ),
    )
    return TransitAccess(
        section=TransitSection(rows=rows),
        texts_by_competitor_id=texts_by_competitor_id,
    )


def sources_line(run: IngestionRun) -> str:
    """Return the Sources and method line of the transit data, dated by the run."""
    return (
        "MTA static GTFS schedules of the New York City subway, the Staten Island "
        "Railway, New York City Transit buses, and MTA Bus Company buses, covering "
        f"New York City only: as checked on {format_report_date(run.started_at)}."
    )


def _texts(transit: NearbyTransit) -> TransitTexts:
    subway_entries = tuple(
        f"{station.name}: {', '.join(station.lines)} "
        f"({format_miles(station.distance_meters)})"
        for station in transit.stations
    ) or (NO_STATION_TEXT,)
    bus_entries = tuple(
        f"{line.name} ({format_miles(line.distance_meters)})"
        for line in transit.bus_lines
    ) or (NO_BUS_LINE_TEXT,)
    return TransitTexts(
        subway_entries=subway_entries,
        subway_text="; ".join(subway_entries),
        bus_entries=bus_entries,
    )
