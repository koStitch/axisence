"""Straight-line distances from a target point to competitors."""

from collections.abc import Collection

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point

from axisence.providers.models import Competitor

METERS_PER_MILE = 1609.344


def distances_from(
    point: Point | None, competitor_ids: Collection[int]
) -> dict[int, float | None]:
    """Return each competitor's geodesic distance in meters, or None when unknown.

    Measured on the geography columns, so on the WGS 84 ellipsoid. A competitor
    without a location, or a missing point, gives None.
    """
    if not competitor_ids:
        return {}
    if point is None:
        return dict.fromkeys(competitor_ids)
    rows = (
        Competitor.objects.filter(pk__in=competitor_ids)
        .annotate(distance=Distance("location", point))
        .values_list("pk", "distance")
    )
    return {
        competitor_id: None if distance is None else distance.m
        for competitor_id, distance in rows
    }


def format_miles(distance_meters: float | None) -> str:
    if distance_meters is None:
        return "Unknown"
    return f"{distance_meters / METERS_PER_MILE:.2f} mi"
