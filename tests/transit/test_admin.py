"""Tests for the view-only transit stop list and transit runs in the ingestion pages."""

import pytest
from django.contrib import admin
from django.contrib.gis.geos import Point
from django.urls import reverse

from axisence.transit.models import TransitMode, TransitStop
from tests.research.factories import make_researcher_in_group
from tests.transit.factories import (
    ensure_transit_run,
    make_transit_line,
    make_transit_stop,
)

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:transit_transitstop_changelist")


@pytest.fixture
def station():
    lines = [
        make_transit_line(TransitMode.SUBWAY, name, position)
        for name, position in (("C", 2), ("A", 1))
    ]
    return make_transit_stop(
        mode=TransitMode.SUBWAY,
        published_stop_id="A41",
        name="Jay St-MetroTech",
        point=Point(-73.987342, 40.692338, srid=4326),
        lines=lines,
        station_group_key="A41+R29",
    )


@pytest.fixture
def bus_stop():
    return make_transit_stop(
        mode=TransitMode.BUS,
        published_stop_id="308791",
        name="CADMAN PLAZA WEST/REMSEN ST",
        point=Point(-73.990599, 40.693227, srid=4326),
        lines=[make_transit_line(TransitMode.BUS, "B25", 1)],
    )


@pytest.mark.parametrize(
    ("query", "shown", "hidden"),
    [
        (
            {"q": "Jay", "mode__exact": "subway"},
            ("Jay St-MetroTech", "A, C", "A41+R29"),
            ("CADMAN PLAZA WEST",),
        ),
        (
            {"q": "308791", "is_active__exact": "1"},
            ("CADMAN PLAZA WEST/REMSEN ST", "B25"),
            ("Jay St-MetroTech",),
        ),
        ({"q": "3087"}, ("0 results",), ("CADMAN PLAZA WEST",)),
    ],
    ids=["name and mode", "stop ID and activity", "partial stop ID"],
)
def test_the_stop_list_searches_and_filters(
    admin_client, station, bus_stop, query, shown, hidden
):
    response = admin_client.get(CHANGELIST_URL, query)

    assert response.status_code == 200
    for text in shown:
        assert text in response.text
    for text in hidden:
        assert text not in response.text


def test_nobody_adds_changes_or_deletes_a_stop(rf, admin_user, station):
    request = rf.get("/admin/")
    request.user = admin_user
    model_admin = admin.site.get_model_admin(TransitStop)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, station) is False
    assert model_admin.has_delete_permission(request, station) is False


def test_a_researcher_views_stops_but_cannot_change_them(client, station):
    client.force_login(make_researcher_in_group())
    change_url = reverse("admin:transit_transitstop_change", args=(station.pk,))

    listed = client.get(CHANGELIST_URL)
    viewed = client.get(change_url)
    posted = client.post(change_url, {"name": "Synthetic Change"})

    assert listed.status_code == 200
    assert viewed.status_code == 200
    assert "40.692338, -73.987342" in viewed.text
    assert posted.status_code == 403
    station.refresh_from_db()
    assert station.name == "Jay St-MetroTech"


def test_transit_runs_appear_in_the_ingestion_run_list(admin_client):
    run = ensure_transit_run()

    response = admin_client.get(
        reverse("admin:ingestion_ingestionrun_changelist"),
        {"source__exact": "mta_transit"},
    )

    assert response.status_code == 200
    assert f"MTA transit run {run.pk}" in response.text
