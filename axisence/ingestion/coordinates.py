"""Coordinates and the NY State bounding box that every source's locations are checked
against.
"""

import dataclasses
from decimal import Decimal

NEW_YORK_STATE_MINIMUM_LONGITUDE = Decimal("-79.8")
NEW_YORK_STATE_MAXIMUM_LONGITUDE = Decimal("-71.8")
NEW_YORK_STATE_MINIMUM_LATITUDE = Decimal("40.4")
NEW_YORK_STATE_MAXIMUM_LATITUDE = Decimal("45.1")


@dataclasses.dataclass(frozen=True, slots=True)
class Coordinates:
    longitude: Decimal
    latitude: Decimal


def is_inside_new_york_state(coordinates: Coordinates) -> bool:
    """Return whether the point lies in the state's bounding box, edges included."""
    return (
        NEW_YORK_STATE_MINIMUM_LONGITUDE
        <= coordinates.longitude
        <= NEW_YORK_STATE_MAXIMUM_LONGITUDE
        and NEW_YORK_STATE_MINIMUM_LATITUDE
        <= coordinates.latitude
        <= NEW_YORK_STATE_MAXIMUM_LATITUDE
    )
