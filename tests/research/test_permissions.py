"""Tests for the Researchers group and what its members can and cannot do."""

import pytest
from django.contrib.auth.models import Group
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.urls import reverse

from axisence.demographics.models import TabulationArea
from axisence.research.models import RESEARCHERS_GROUP_NAME, Contact, ResearchJob
from tests.research.call_sheet_posts import call_sheet_data
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_job,
    make_located_competitor,
    make_researcher_in_group,
    point_north_of,
)

pytestmark = pytest.mark.django_db

RESEARCHERS_PERMISSIONS = {
    ("providers", "view_provider"),
    ("providers", "change_provider"),
    ("providers", "view_competitor"),
    ("providers", "change_competitor"),
    ("providers", "view_reviewflag"),
    ("providers", "change_reviewflag"),
    ("providers", "view_competitordecision"),
    ("demographics", "view_tabulationarea"),
    ("demographics", "view_censusvariable"),
    ("demographics", "view_censusfigure"),
    ("research", "add_researchjob"),
    ("research", "view_researchjob"),
    ("research", "change_researchjob"),
    ("research", "add_jobcompetitor"),
    ("research", "view_jobcompetitor"),
    ("research", "change_jobcompetitor"),
    ("research", "add_contact"),
    ("research", "view_contact"),
    ("research", "retract_contact"),
    ("research", "add_observation"),
    ("research", "view_observation"),
    # Granted by reports.0002_grant_report_permissions_to_researchers.
    ("reports", "view_report"),
    ("reports", "generate_report"),
}


def test_the_researchers_group_has_exactly_the_specified_permissions():
    group = Group.objects.get(name=RESEARCHERS_GROUP_NAME)

    assert (
        set(group.permissions.values_list("content_type__app_label", "codename"))
        == RESEARCHERS_PERMISSIONS
    )


def test_a_researcher_runs_a_job_records_and_retracts(client):
    researcher = make_researcher_in_group()
    client.force_login(researcher)
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    job = make_job(researcher=researcher, point=BOROUGH_HALL, confirmed=True)

    add_page = client.get(reverse("admin:research_researchjob_add"))
    created = client.post(
        reverse("admin:research_researchjob_add"),
        {
            "purpose": "client_report",
            "organization_name": "Synthetic Client",
            "target_address": "100 Example Street, Brooklyn, NY 11217",
            "preselected_competitor_count": "15",
            "search_radius_miles": "1.00",
            "provider_kinds": ["day_care_center"],
        },
    )
    search_page = client.get(
        reverse("admin:research_researchjob_search", args=(job.pk,))
    )
    call_sheet_url = reverse("admin:research_contact_call_sheet", args=(competitor.pk,))
    recorded = client.post(call_sheet_url, call_sheet_data(client, call_sheet_url))
    contact = Contact.objects.get()
    retracted = client.post(
        reverse("admin:research_contact_retract", args=(contact.pk,)),
        {"reason": "Synthetic mistake"},
    )

    assert add_page.status_code == 200
    assert created.status_code == 302
    assert ResearchJob.objects.get(organization_name="Synthetic Client").created_by == (
        researcher
    )
    assert search_page.status_code == 200
    assert recorded.status_code == 302
    assert contact.researcher == researcher
    assert retracted.status_code == 302
    contact.refresh_from_db()
    assert contact.retracted_by == researcher


def test_a_researcher_cannot_manage_accounts_change_records_or_edit_ingested_data(
    client,
):
    researcher = make_researcher_in_group()
    client.force_login(researcher)
    competitor = make_located_competitor(BOROUGH_HALL)
    contact = make_contact(competitor=competitor, researcher=researcher)
    area = TabulationArea.objects.create(
        code="11201",
        boundary=MultiPolygon(Polygon.from_bbox((-74.0, 40.6, -73.9, 40.7)), srid=4326),
    )

    users = client.get(reverse("admin:accounts_user_changelist"))
    contact_change = client.post(
        reverse("admin:research_contact_change", args=(contact.pk,)), {}
    )
    area_change = client.post(
        reverse("admin:demographics_tabulationarea_change", args=(area.pk,)), {}
    )

    assert users.status_code == 403
    assert contact_change.status_code == 403
    assert area_change.status_code == 403
