"""Upsert the combined stops and their lines, and mark absent stops inactive.

A stop the feeds no longer list is marked inactive, never deleted, and becomes active
again when they list it again.
"""

import dataclasses
import itertools

from django.contrib.gis.geos import Point

from axisence.ingestion.errors import IngestionError
from axisence.ingestion.models import IngestionRun
from axisence.transit.combining import CombinedFeeds, DesiredStop, natural_order_key
from axisence.transit.models import TransitLine, TransitMode, TransitStop

WRITE_BATCH_SIZE = 1000

# A run may mark at most one tenth of a mode's active stops inactive.
MASS_DISAPPEARANCE_DIVISOR = 10


@dataclasses.dataclass(slots=True)
class StoreCounts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    marked_inactive: int = 0
    reactivated: int = 0
    guard_override_used: bool = False


def store_transit_stops(
    run: IngestionRun, combined: CombinedFeeds, *, allow_mass_disappearance: bool
) -> StoreCounts:
    """Upsert the lines and stops, link them, and mark absent stops inactive.

    Runs inside the caller's transaction. The mass-disappearance guard of each mode
    raises before any write. Untouched stops are not written.
    """
    stored = {
        (stop.mode, stop.published_stop_id): stop
        for stop in TransitStop.objects.prefetch_related("lines")
    }
    desired_keys = {
        (stop.mode.value, stop.published_stop_id) for stop in combined.stops
    }
    to_mark_inactive = [
        stop
        for key, stop in stored.items()
        if stop.is_active
        and key not in desired_keys
        and key not in combined.untouched_stop_keys
    ]
    counts = StoreCounts(
        marked_inactive=len(to_mark_inactive),
        guard_override_used=_check_mass_disappearance(
            list(stored.values()),
            to_mark_inactive,
            allow_mass_disappearance=allow_mass_disappearance,
        ),
    )
    line_ids = _store_lines(combined)
    _store_stops(run, combined.stops, stored, counts)
    stop_ids = {
        (mode, published_stop_id): pk
        for pk, mode, published_stop_id in TransitStop.objects.values_list(
            "pk", "mode", "published_stop_id"
        )
    }
    _store_line_links(combined.stops, stop_ids, line_ids)
    for batch in itertools.batched(
        [stop.pk for stop in to_mark_inactive], WRITE_BATCH_SIZE, strict=False
    ):
        TransitStop.objects.filter(pk__in=batch).update(is_active=False)
    return counts


def _check_mass_disappearance(
    stored: list[TransitStop],
    to_mark_inactive: list[TransitStop],
    *,
    allow_mass_disappearance: bool,
) -> bool:
    """Refuse marking more than a tenth of a mode's active stops inactive unless
    allowed, and return whether the allowance was needed.
    """
    guard_override_used = False
    for mode in TransitMode:
        active = sum(stop.is_active and stop.mode == mode for stop in stored)
        marked = sum(stop.mode == mode for stop in to_mark_inactive)
        if active and marked * MASS_DISAPPEARANCE_DIVISOR > active:
            if not allow_mass_disappearance:
                raise IngestionError(
                    f"Refusing to mark {marked} of {active} active "
                    f"{mode.label.lower()} stops inactive, which is more than 10%. "
                    "Run the command again with --allow-mass-disappearance if the "
                    "MTA really removed them."
                )
            guard_override_used = True
    return guard_override_used


def _store_lines(combined: CombinedFeeds) -> dict[tuple[str, str], int]:
    """Upsert the subway lines with their sort orders and rank every stored and new bus
    line name in natural order, then return every line's key by mode and name.
    """
    bus_line_names = combined.bus_line_names | set(
        TransitLine.objects.filter(mode=TransitMode.BUS).values_list("name", flat=True)
    )
    ranked_bus_line_names = sorted(
        bus_line_names, key=lambda name: (natural_order_key(name), name)
    )
    TransitLine.objects.bulk_create(
        [
            *(
                TransitLine(mode=TransitMode.SUBWAY, name=name, sort_position=position)
                for name, position in combined.subway_line_sort_positions.items()
            ),
            *(
                TransitLine(mode=TransitMode.BUS, name=name, sort_position=rank)
                for rank, name in enumerate(ranked_bus_line_names, start=1)
            ),
        ],
        batch_size=WRITE_BATCH_SIZE,
        update_conflicts=True,
        unique_fields=("mode", "name"),
        update_fields=("sort_position",),
    )
    return {
        (mode, name): pk
        for pk, mode, name in TransitLine.objects.values_list("pk", "mode", "name")
    }


def _store_stops(
    run: IngestionRun,
    desired_stops: tuple[DesiredStop, ...],
    stored: dict[tuple[str, str], TransitStop],
    counts: StoreCounts,
) -> None:
    upserts: list[TransitStop] = []
    for desired in desired_stops:
        longitude = float(desired.coordinates.longitude)
        latitude = float(desired.coordinates.latitude)
        current = stored.get((desired.mode.value, desired.published_stop_id))
        if current is None:
            counts.created += 1
            first_seen_run_id = run.pk
        else:
            changed = (
                current.name != desired.name
                or current.location.coords != (longitude, latitude)
                or current.station_group_key != desired.station_group_key
                or {line.name for line in current.lines.all()} != desired.line_names
            )
            if changed:
                counts.updated += 1
            else:
                counts.unchanged += 1
            if not current.is_active:
                counts.reactivated += 1
            first_seen_run_id = current.first_seen_run_id
        # The primary key stays unset: the conflict target finds the stored row.
        upserts.append(
            TransitStop(
                mode=desired.mode,
                published_stop_id=desired.published_stop_id,
                name=desired.name,
                location=Point(longitude, latitude, srid=4326),
                station_group_key=desired.station_group_key,
                is_active=True,
                first_seen_run_id=first_seen_run_id,
                last_seen_run=run,
            )
        )
    TransitStop.objects.bulk_create(
        upserts,
        batch_size=WRITE_BATCH_SIZE,
        update_conflicts=True,
        unique_fields=("mode", "published_stop_id"),
        update_fields=(
            "name",
            "location",
            "station_group_key",
            "is_active",
            "last_seen_run",
        ),
    )


def _store_line_links(
    desired_stops: tuple[DesiredStop, ...],
    stop_ids: dict[tuple[str, str], int],
    line_ids: dict[tuple[str, str], int],
) -> None:
    """Make each desired stop's links to its lines exactly its desired lines."""
    through = TransitStop.lines.through
    desired_stop_ids: set[int] = set()
    desired_links: set[tuple[int, int]] = set()
    for stop in desired_stops:
        stop_id = stop_ids[(stop.mode.value, stop.published_stop_id)]
        desired_stop_ids.add(stop_id)
        desired_links.update(
            (stop_id, line_ids[(stop.mode.value, name)]) for name in stop.line_names
        )
    stored_links = {
        (stop_id, line_id): link_id
        for link_id, stop_id, line_id in through.objects.values_list(
            "pk", "transitstop_id", "transitline_id"
        )
        if stop_id in desired_stop_ids
    }
    for batch in itertools.batched(
        [
            link_id
            for link, link_id in stored_links.items()
            if link not in desired_links
        ],
        WRITE_BATCH_SIZE,
        strict=False,
    ):
        through.objects.filter(pk__in=batch).delete()
    through.objects.bulk_create(
        [
            through(transitstop_id=stop_id, transitline_id=line_id)
            for stop_id, line_id in desired_links - stored_links.keys()
        ],
        batch_size=WRITE_BATCH_SIZE,
    )
