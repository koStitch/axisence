"""Subway stations and bus stops of the MTA feeds, and the lines that stop at them."""

from django.contrib.gis.db import models

PUBLISHED_STOP_ID_MAXIMUM_LENGTH = 50
STOP_NAME_MAXIMUM_LENGTH = 255
LINE_NAME_MAXIMUM_LENGTH = 50
STATION_GROUP_KEY_MAXIMUM_LENGTH = 255


class TransitMode(models.TextChoices):
    SUBWAY = "subway", "Subway"
    BUS = "bus", "Bus"


class TransitLine(models.Model):
    """A line as riders know it, by its rider-facing name, stored once a run finds it
    scheduled to stop on weekday daytime service.
    """

    mode = models.CharField(max_length=20, choices=TransitMode.choices)
    name = models.CharField(max_length=LINE_NAME_MAXIMUM_LENGTH)
    # Subway: the route_sort_order of the line's route; bus: the rank of the name in
    # natural order.
    sort_position = models.PositiveIntegerField()

    class Meta:
        ordering = ("mode", "sort_position", "name")
        constraints = (
            models.UniqueConstraint(
                fields=("mode", "name"), name="unique_transit_line_per_mode"
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_mode_display()} {self.name}"


class TransitStop(models.Model):
    """A subway station or bus stop of the MTA feeds, kept under its published ID and
    marked inactive, never deleted, when the feeds no longer list it.
    """

    mode = models.CharField(max_length=20, choices=TransitMode.choices)
    published_stop_id = models.CharField(max_length=PUBLISHED_STOP_ID_MAXIMUM_LENGTH)
    name = models.CharField(max_length=STOP_NAME_MAXIMUM_LENGTH, blank=True)
    location = models.PointField(srid=4326, geography=True)
    lines = models.ManyToManyField(TransitLine, related_name="stops", blank=True)
    # Subway only: the sorted published IDs of the station's group joined by "+";
    # blank for bus stops.
    station_group_key = models.CharField(
        max_length=STATION_GROUP_KEY_MAXIMUM_LENGTH, blank=True
    )
    is_active = models.BooleanField(default=True)
    first_seen_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        related_name="transit_stops_first_seen",
    )
    last_seen_run = models.ForeignKey(
        "ingestion.IngestionRun",
        on_delete=models.PROTECT,
        related_name="transit_stops_last_seen",
    )

    class Meta:
        ordering = ("mode", "name", "published_stop_id")
        constraints = (
            models.UniqueConstraint(
                fields=("mode", "published_stop_id"),
                name="unique_transit_stop_per_mode",
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_mode_display()} {self.published_stop_id} {self.name}"
