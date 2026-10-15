"""End-to-end test of GeoDjango against the Compose PostGIS database.

A throwaway model in an isolated app registry stores a public Brooklyn landmark. No
production model, migration, app, or setting exists to serve it.
"""

import pytest
from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db import connection
from django.test.utils import isolate_apps

BARCLAYS_CENTER_LONGITUDE = -73.9754
BARCLAYS_CENTER_LATITUDE = 40.6826
BROOKLYN_BOROUGH_HALL_LONGITUDE = -73.9903
BROOKLYN_BOROUGH_HALL_LATITUDE = 40.6929

# WGS 84 geodesic distance between the two landmarks, computed independently with
# Vincenty's inverse formula. PostGIS measures geography on the same ellipsoid.
DISTANCE_BETWEEN_LANDMARKS_IN_METERS = 1701.362
DISTANCE_TOLERANCE_IN_METERS = 1.0
RADIUS_MARGIN_IN_METERS = 100


@pytest.fixture
def landmark_model():
    with isolate_apps("tests"):

        class Landmark(models.Model):
            name = models.CharField(max_length=100)
            location = models.PointField(srid=4326, geography=True)

            class Meta:
                app_label = "tests"

            def __str__(self):
                return self.name

    # The table is created inside the test's transaction and disappears with its
    # rollback.
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(Landmark)
    return Landmark


def _create_barclays_center(landmark_model):
    return landmark_model.objects.create(
        name="Barclays Center",
        location=Point(BARCLAYS_CENTER_LONGITUDE, BARCLAYS_CENTER_LATITUDE, srid=4326),
    )


def _brooklyn_borough_hall():
    return Point(
        BROOKLYN_BOROUGH_HALL_LONGITUDE, BROOKLYN_BOROUGH_HALL_LATITUDE, srid=4326
    )


@pytest.mark.django_db
def test_point_stores_longitude_as_x_and_latitude_as_y(landmark_model):
    _create_barclays_center(landmark_model)

    stored = landmark_model.objects.get()

    assert stored.location.x == pytest.approx(BARCLAYS_CENTER_LONGITUDE)
    assert stored.location.y == pytest.approx(BARCLAYS_CENTER_LATITUDE)


@pytest.mark.django_db
def test_dwithin_includes_the_point_only_when_the_radius_reaches_it(landmark_model):
    _create_barclays_center(landmark_model)
    larger_radius = D(m=DISTANCE_BETWEEN_LANDMARKS_IN_METERS + RADIUS_MARGIN_IN_METERS)
    smaller_radius = D(m=DISTANCE_BETWEEN_LANDMARKS_IN_METERS - RADIUS_MARGIN_IN_METERS)

    within_larger = landmark_model.objects.filter(
        location__dwithin=(_brooklyn_borough_hall(), larger_radius)
    )
    within_smaller = landmark_model.objects.filter(
        location__dwithin=(_brooklyn_borough_hall(), smaller_radius)
    )

    assert within_larger.exists()
    assert not within_smaller.exists()


@pytest.mark.django_db
def test_distance_annotation_matches_the_independent_geodesic_distance(landmark_model):
    _create_barclays_center(landmark_model)

    stored = landmark_model.objects.annotate(
        distance=Distance("location", _brooklyn_borough_hall())
    ).get()

    assert stored.distance.m == pytest.approx(
        DISTANCE_BETWEEN_LANDMARKS_IN_METERS, abs=DISTANCE_TOLERANCE_IN_METERS
    )
