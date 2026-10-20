"""End-to-end tests of the boundaries command, with a synthetic shapefile."""

import zipfile
from io import StringIO

import pytest
from django.contrib.gis.geos import MultiPolygon, Point
from django.core.management import call_command
from django.core.management.base import CommandError

from axisence.demographics.boundaries import BOUNDARY_FILE_URL
from axisence.demographics.census_api import TABULATION_AREA_LIST_VINTAGE, profile_url
from axisence.demographics.models import TabulationArea
from axisence.ingestion.models import IngestionOutcome, IngestionRun
from tests.demographics.shapefiles import square, write_polygon_shapefile_zip
from tests.fixture_files import fixture_bytes
from tests.test_postgis import (
    BARCLAYS_CENTER_LATITUDE,
    BARCLAYS_CENTER_LONGITUDE,
    BROOKLYN_BOROUGH_HALL_LATITUDE,
    BROOKLYN_BOROUGH_HALL_LONGITUDE,
)

SYNTHETIC_CENSUS_VALUE = "synthetic-census-value"
STEM = "cb_2020_us_zcta520_500k"
FEATURES = (
    (
        "11217",
        [square(BARCLAYS_CENTER_LONGITUDE, BARCLAYS_CENTER_LATITUDE, 0.004, 0.003)],
    ),
    (
        "11201",
        [
            square(
                BROOKLYN_BOROUGH_HALL_LONGITUDE,
                BROOKLYN_BOROUGH_HALL_LATITUDE,
                0.003,
                0.003,
            ),
            square(-74.0150, 40.6900, 0.002, 0.002),
        ],
    ),
    ("06390", [square(-71.99, 41.26, 0.01, 0.01)]),
    # Not a NY State tabulation area, so the list request does not name it.
    ("07030", [square(-74.03, 40.745, 0.005, 0.005)]),
)


@pytest.fixture
def census_key(settings):
    settings.CENSUS_API_KEY = SYNTHETIC_CENSUS_VALUE


def _zip_bytes(tmp_path, features):
    path = tmp_path / "boundaries.zip"
    write_polygon_shapefile_zip(path, STEM, features)
    return path.read_bytes()


def _serve(fake_transport, boundary_file):
    fake_transport.routes.clear()
    fake_transport.add(
        "GET",
        f"{profile_url(TABULATION_AREA_LIST_VINTAGE)}?",
        fixture_bytes("census/acs5-profile-2024-new-york-tabulation-areas.json"),
        query_contains="get=NAME",
        headers={"Content-Type": "application/json;charset=utf-8"},
    )
    fake_transport.add(
        "GET",
        BOUNDARY_FILE_URL,
        boundary_file,
        headers={"Content-Type": "application/zip"},
    )


def _run():
    stdout = StringIO()
    call_command("ingest_census_boundaries", stdout=stdout, stderr=StringIO())
    return stdout.getvalue()


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_boundaries_command_keeps_only_listed_areas_as_multipolygons_in_4326(
    fake_transport, tmp_path
):
    _serve(fake_transport, _zip_bytes(tmp_path, FEATURES))

    output = _run()

    run = IngestionRun.objects.get()
    polygon_counts = {}
    for area in TabulationArea.objects.all():
        assert isinstance(area.boundary, MultiPolygon)
        assert area.boundary.srid == 4326
        polygon_counts[area.code] = len(area.boundary)
    assert run.outcome == IngestionOutcome.SUCCEEDED
    # 07030 is absent, and 06390 keeps its leading zero.
    assert polygon_counts == {"06390": 1, "11201": 2, "11217": 1}
    assert (run.rows_read_count, run.created_count, run.field_warning_count) == (
        4,
        3,
        0,
    )
    assert output.startswith(f"Census boundaries run {run.pk} succeeded.\n")


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_point_in_polygon_finds_the_tabulation_area(fake_transport, tmp_path):
    _serve(fake_transport, _zip_bytes(tmp_path, FEATURES))
    _run()

    barclays_center = Point(
        BARCLAYS_CENTER_LONGITUDE, BARCLAYS_CENTER_LATITUDE, srid=4326
    )
    borough_hall = Point(
        BROOKLYN_BOROUGH_HALL_LONGITUDE, BROOKLYN_BOROUGH_HALL_LATITUDE, srid=4326
    )

    assert TabulationArea.objects.get(boundary__contains=barclays_center).code == (
        "11217"
    )
    assert TabulationArea.objects.get(boundary__contains=borough_hall).code == "11201"


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_rerunning_updates_boundaries_in_place(fake_transport, tmp_path):
    _serve(fake_transport, _zip_bytes(tmp_path, FEATURES))
    _run()
    _run()
    unchanged_run = IngestionRun.objects.latest("started_at")
    moved = (
        (
            "11217",
            [square(BARCLAYS_CENTER_LONGITUDE, BARCLAYS_CENTER_LATITUDE, 0.005, 0.003)],
        ),
        *FEATURES[1:],
    )
    _serve(fake_transport, _zip_bytes(tmp_path, moved))

    _run()

    changed_run = IngestionRun.objects.latest("started_at")
    assert (
        unchanged_run.created_count,
        unchanged_run.updated_count,
        unchanged_run.unchanged_count,
    ) == (0, 0, 3)
    assert (
        changed_run.created_count,
        changed_run.updated_count,
        changed_run.unchanged_count,
    ) == (0, 1, 2)
    assert TabulationArea.objects.count() == 3


@pytest.mark.django_db
def test_missing_census_key_stops_before_any_request(fake_transport, tmp_path):
    _serve(fake_transport, _zip_bytes(tmp_path, FEATURES))

    with pytest.raises(CommandError, match="CENSUS_API_KEY is not set"):
        _run()

    assert fake_transport.requests == []
    assert IngestionRun.objects.get().outcome == IngestionOutcome.FAILED


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
@pytest.mark.parametrize(
    ("is_zip", "message"),
    [(True, "exactly one shapefile"), (False, "not a zip archive")],
    ids=["zip without shapefile", "not a zip"],
)
def test_zip_without_a_shapefile_fails_the_run(
    fake_transport, tmp_path, is_zip, message
):
    path = tmp_path / "no-shapefile.zip"
    if is_zip:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "No shapefile here.")
    else:
        path.write_bytes(b"<html>Not a zip archive.</html>")
    _serve(fake_transport, path.read_bytes())

    with pytest.raises(CommandError, match=message):
        _run()

    assert IngestionRun.objects.get().outcome == IngestionOutcome.FAILED
    assert not TabulationArea.objects.exists()
