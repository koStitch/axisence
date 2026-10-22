"""Write GTFS zips from rows, so each test shows the feed rows it relies on."""

import csv
import io
import zipfile

from tests.fixture_files import FIXTURES_DIRECTORY

MTA_GTFS_FIXTURES = FIXTURES_DIRECTORY / "mta-gtfs"

WEEKDAYS = ["1", "1", "1", "1", "1"]
FAR_END_DATE = "20991231"

BUS_HEADERS = {
    "stops.txt": ["stop_id", "stop_name", "stop_lat", "stop_lon"],
    "routes.txt": ["route_id", "route_short_name", "route_type"],
    "trips.txt": ["route_id", "service_id", "trip_id"],
    "calendar.txt": [
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "end_date",
    ],
    # pickup_type and drop_off_type are optional; a shorter row leaves them blank.
    "stop_times.txt": [
        "trip_id",
        "stop_id",
        "arrival_time",
        "departure_time",
        "pickup_type",
        "drop_off_type",
    ],
}
SUBWAY_HEADERS = {
    **BUS_HEADERS,
    "stops.txt": [*BUS_HEADERS["stops.txt"], "location_type", "parent_station"],
    "routes.txt": [*BUS_HEADERS["routes.txt"], "route_sort_order"],
    "transfers.txt": ["from_stop_id", "to_stop_id"],
}


def write_feed(directory, file_name, tables):
    """Write a zip holding one CSV member per table, and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name
    with zipfile.ZipFile(path, "w") as archive:
        for member, rows in tables.items():
            text = io.StringIO(newline="")
            csv.writer(text).writerows(rows)
            archive.writestr(member, text.getvalue())
    return path


def fixture_tables(file_name):
    """Read a trimmed real feed into tables of rows, header first, to change and write
    again.
    """
    with zipfile.ZipFile(MTA_GTFS_FIXTURES / file_name) as archive:
        return {
            member: list(csv.reader(io.StringIO(_text(archive, member), newline="")))
            for member in archive.namelist()
        }


def minimal_bus_tables(
    *,
    stops=(("900001", "SYNTHETIC ST/EXAMPLE AV", "40.6926", "-73.9907"),),
    routes=(("B900", "B900", "3"),),
    trips=(("B900", "WEEKDAY", "TRIP-1"),),
    calendar=(("WEEKDAY", *WEEKDAYS, FAR_END_DATE),),
    stop_times=(("TRIP-1", "900001", "12:00:00", "12:00:00"),),
):
    """Return a bus feed of one stop and one weekday daytime trip, with the required
    headers; each argument replaces one table's rows.
    """
    return _tables(
        BUS_HEADERS,
        {
            "stops.txt": stops,
            "routes.txt": routes,
            "trips.txt": trips,
            "calendar.txt": calendar,
            "stop_times.txt": stop_times,
        },
    )


def minimal_subway_tables(
    *,
    stops=(
        ("901", "Synthetic Station", "40.6926", "-73.9907", "1", ""),
        ("901N", "Synthetic Station", "40.6926", "-73.9907", "", "901"),
    ),
    routes=(("A", "A", "1", "1"),),
    trips=(("A", "WEEKDAY", "TRIP-1"),),
    calendar=(("WEEKDAY", *WEEKDAYS, FAR_END_DATE),),
    stop_times=(("TRIP-1", "901N", "12:00:00", "12:00:00"),),
    transfers=(),
):
    """Return a subway feed of one station with a platform and one weekday daytime
    trip, with the required headers; each argument replaces one table's rows.
    """
    return _tables(
        SUBWAY_HEADERS,
        {
            "stops.txt": stops,
            "routes.txt": routes,
            "trips.txt": trips,
            "calendar.txt": calendar,
            "stop_times.txt": stop_times,
            "transfers.txt": transfers,
        },
    )


def _text(archive, member):
    return archive.read(member).decode("utf-8")


def _tables(headers, rows_by_member):
    return {
        member: [list(headers[member]), *(list(row) for row in rows)]
        for member, rows in rows_by_member.items()
    }
