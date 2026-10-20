"""Tests for the research admin pages, their buttons, and their refusals."""

import re
from datetime import date

import pytest
from django.contrib import admin
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.urls import reverse
from django.utils import timezone

from axisence.demographics.models import TabulationArea
from axisence.providers.models import Competitor
from axisence.research import contacts
from axisence.research.geocoding import GEOCODER_ONE_LINE_ADDRESS_URL
from axisence.research.models import (
    Contact,
    JobCompetitor,
    JobStatus,
    Observation,
    ObservationKind,
    ResearchJob,
    ResearchStatus,
    TargetLookupOutcome,
    TargetPointSource,
)
from tests.admin_forms import change_form_data
from tests.fixture_files import fixture_bytes
from tests.providers.factories import make_competitor
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_job,
    make_located_competitor,
    make_observation,
    point_north_of,
)
from tests.research.geocoder_fakes import RECORDED_RESPONSE

NOTE_MARKER = "synthetic note marker 7f3a"


@pytest.fixture
def seeded(db, admin_user):
    competitor = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.2), name="Seeded Day Care"
    )
    job = make_job(researcher=admin_user, point=BOROUGH_HALL, confirmed=True)
    job_competitor = JobCompetitor.objects.create(
        job=job, competitor=competitor, distance_meters=321.0
    )
    contact = make_contact(
        competitor=competitor, researcher=admin_user, job=None, notes=NOTE_MARKER
    )
    observation = make_observation(
        contact=contact, kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50
    )
    return {
        ResearchJob: job,
        JobCompetitor: job_competitor,
        Contact: contact,
        Observation: observation,
        Competitor: competitor,
    }


def _url(model, view, *args):
    return reverse(
        f"admin:{model._meta.app_label}_{model._meta.model_name}_{view}", args=args
    )


# Each row: the model, a search term, and its first list filter set to the seeded
# value.
CHANGELISTS = (
    (ResearchJob, "Synthetic Organization", {"status__exact": "draft"}),
    (JobCompetitor, "Seeded", {"research_status__exact": "to_do"}),
    (Contact, "Seeded", {"method__exact": "call"}),
    (Observation, "Seeded", {"kind__exact": "weeks_open"}),
)


@pytest.mark.parametrize(
    ("model", "search_term", "filters"),
    CHANGELISTS,
    ids=[model.__name__ for model, *_ in CHANGELISTS],
)
def test_research_admin_pages_render(admin_client, seeded, model, search_term, filters):
    record = seeded[model]

    changelist = admin_client.get(
        _url(model, "changelist"), {"q": search_term, **filters}
    )
    change_page = admin_client.get(_url(model, "change", record.pk))

    assert changelist.status_code == 200
    assert str(record) in changelist.text
    assert changelist.context["cl"].result_count == 1
    assert change_page.status_code == 200


def test_notes_never_appear_in_list_pages_or_search(admin_client, seeded):
    contact_list = admin_client.get(_url(Contact, "changelist"))
    observation_list = admin_client.get(_url(Observation, "changelist"))
    note_search = admin_client.get(_url(Contact, "changelist"), {"q": "7f3a"})

    assert NOTE_MARKER not in contact_list.text
    assert NOTE_MARKER not in observation_list.text
    assert note_search.context["cl"].result_count == 0
    assert (
        NOTE_MARKER
        in admin_client.get(_url(Contact, "change", seeded[Contact].pk)).text
    )


def test_contacts_and_observations_cannot_be_added_changed_or_deleted_in_the_admin(
    rf, admin_user, admin_client, seeded
):
    request = rf.get("/admin/")
    request.user = admin_user
    for model in (Contact, Observation):
        record = seeded[model]
        model_admin = admin.site.get_model_admin(model)
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request, record) is False
        assert model_admin.has_delete_permission(request, record) is False
        assert (
            admin_client.post(_url(model, "change", record.pk), {}).status_code == 403
        )
        assert admin_client.get(_url(model, "delete", record.pk)).status_code == 403
    job = seeded[ResearchJob]
    assert admin_client.get(_url(ResearchJob, "delete", job.pk)).status_code == 403


@pytest.mark.parametrize("target", ["contact", "observation"])
def test_retraction_from_the_contact_page(admin_client, admin_user, seeded, target):
    contact = seeded[Contact]
    observation = seeded[Observation]
    retract_url = reverse("admin:research_contact_retract", args=(contact.pk,))
    contact_page = admin_client.get(_url(Contact, "change", contact.pk))
    if target == "observation":
        retract_url += f"?observation={observation.pk}"

    response = admin_client.post(retract_url, {"reason": "Synthetic mistake"})

    assert "Retract this contact" in contact_page.text
    assert f"?observation={observation.pk}" in contact_page.text
    assert response.status_code == 302
    assert response["Location"] == _url(Contact, "change", contact.pk)
    record = Contact.objects.get() if target == "contact" else Observation.objects.get()
    assert record.retracted_by == admin_user
    assert record.retraction_reason == "Synthetic mistake"


def test_the_job_pages_render_the_map_buttons_and_tabulation_area(
    admin_client, seeded, fake_transport
):
    TabulationArea.objects.create(
        code="11201",
        boundary=MultiPolygon(
            Polygon.from_bbox(
                (
                    BOROUGH_HALL.x - 0.01,
                    BOROUGH_HALL.y - 0.01,
                    BOROUGH_HALL.x + 0.01,
                    BOROUGH_HALL.y + 0.01,
                )
            ),
            srid=4326,
        ),
    )
    fake_transport.add(
        "GET", GEOCODER_ONE_LINE_ADDRESS_URL, fixture_bytes(RECORDED_RESPONSE)
    )

    add_page = admin_client.get(_url(ResearchJob, "add"))
    created = admin_client.post(
        _url(ResearchJob, "add"),
        {
            "purpose": "prospect",
            "organization_name": "Synthetic Prospect",
            "target_address": "100 Example Street, Brooklyn, NY 11217",
            "preselected_competitor_count": "15",
            "search_radius_miles": "1.00",
            "provider_kinds": ["day_care_center", "small_day_care_center"],
        },
        follow=True,
    )
    change_page = admin_client.get(_url(ResearchJob, "change", seeded[ResearchJob].pk))

    assert add_page.status_code == 200
    assert add_page.context["adminform"].form["provider_kinds"].value() == [
        "day_care_center",
        "small_day_care_center",
        "school_based_child_care",
    ]
    assert 'name="target_address"' in add_page.text
    assert 'name="target_point"' not in add_page.text
    new_job = ResearchJob.objects.get(organization_name="Synthetic Prospect")
    assert created.redirect_chain == [(_url(ResearchJob, "change", new_job.pk), 302)]
    assert (
        f"Research job #{new_job.pk} created. Check the pin on the map." in created.text
    )
    assert new_job.target_point is not None
    assert change_page.status_code == 200
    assert 'name="target_point"' in change_page.text
    assert "Look up the address again" in change_page.text
    assert "Start research" in change_page.text
    assert "Search competitors" in change_page.text
    assert "11201: a ZIP Code Tabulation Area" in change_page.text


def test_status_buttons_change_the_status(admin_client, seeded):
    job = seeded[ResearchJob]
    url = _url(ResearchJob, "change", job.pk)

    response = admin_client.post(
        url, change_form_data(admin_client, url, _transition="researching")
    )

    job.refresh_from_db()
    assert response.status_code == 302
    assert job.status == JobStatus.RESEARCHING
    assert "Day care center, Small day care center, School-based child care" in (
        admin_client.get(url).text
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (JobStatus.RESEARCHING, ResearchStatus.DONE),
        (JobStatus.DRAFT, ResearchStatus.TO_DO),
    ],
    ids=["researching", "draft"],
)
def test_research_statuses_change_only_while_researching(
    admin_client, seeded, status, expected
):
    job = seeded[ResearchJob]
    ResearchJob.objects.filter(pk=job.pk).update(status=status)
    url = _url(ResearchJob, "change", job.pk)

    response = admin_client.post(
        url,
        change_form_data(
            admin_client, url, **{"job_competitors-0-research_status": "done"}
        ),
    )

    assert response.status_code == 302
    assert JobCompetitor.objects.get().research_status == expected


def test_the_competitor_page_links_to_the_call_sheet(admin_client, seeded):
    competitor = seeded[Competitor]

    response = admin_client.get(_url(Competitor, "change", competitor.pk))

    assert reverse("admin:research_contact_call_sheet", args=(competitor.pk,)) in (
        response.text
    )


def test_custom_pages_render(admin_client, seeded):
    job = seeded[ResearchJob]
    competitor = seeded[Competitor]
    contact = seeded[Contact]

    for url in (
        reverse("admin:research_researchjob_search", args=(job.pk,)),
        reverse("admin:research_contact_call_sheet", args=(competitor.pk,)),
        reverse("admin:research_contact_retract", args=(contact.pk,)),
    ):
        assert admin_client.get(url).status_code == 200


def test_the_own_competitor_autocomplete_never_offers_retired_competitors(
    admin_client,
):
    current = make_competitor(display_name="Synthetic Target Day Care")
    retired = make_competitor(display_name="Synthetic Target Old Day Care")
    Competitor.objects.filter(pk=retired.pk).update(
        retired_at=timezone.now(), merged_into=current, is_active=False
    )

    response = admin_client.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "research",
            "model_name": "researchjob",
            "field_name": "own_competitor",
            "term": "Synthetic Target",
        },
    )

    assert [result["id"] for result in response.json()["results"]] == [str(current.pk)]


def _first_submit_control(html, form_marker):
    form = html[html.index(form_marker) :]
    first_submit = re.search(r'<(?:input|button)[^>]*type="submit"[^>]*>', form)
    assert first_submit is not None
    return first_submit.group(0)


def _job_with_matches(researcher):
    return make_job(
        researcher=researcher,
        geocoding_outcome=TargetLookupOutcome.SEVERAL_MATCHES,
        geocoding_candidates=[
            {
                "matched_address": "100 EXAMPLE ST, BROOKLYN, NY, 11217",
                "longitude": "-73.9754",
                "latitude": "40.6826",
            },
            {
                "matched_address": "100 EXAMPLE AVE, BROOKLYN, NY, 11217",
                "longitude": "-73.9791",
                "latitude": "40.6812",
            },
        ],
    )


def test_enter_on_the_job_page_saves_instead_of_using_a_match(admin_client, admin_user):
    job = _job_with_matches(admin_user)

    html = admin_client.get(_url(ResearchJob, "change", job.pk)).text

    assert 'name="_use_geocoding_candidate"' in html
    assert 'name="_save"' in _first_submit_control(html, 'id="researchjob_form"')


def test_confirming_the_pin_on_the_job_page_records_the_researcher(
    admin_client, admin_user
):
    job = make_job(researcher=admin_user, point=BOROUGH_HALL)
    url = _url(ResearchJob, "change", job.pk)

    response = admin_client.post(
        url, change_form_data(admin_client, url, confirm_target_point="on")
    )

    job.refresh_from_db()
    assert response.status_code == 302
    assert job.target_point_confirmed_by == admin_user


@pytest.mark.parametrize(
    ("button", "expected_longitude"),
    [
        ({"_look_up_target_address": "Look up the address again"}, BOROUGH_HALL.x),
        ({"_use_geocoding_candidate": "1"}, -73.9791),
    ],
    ids=["look up again", "use a match"],
)
def test_the_lookup_and_match_buttons_propose_their_point(
    admin_client, admin_user, fake_transport, button, expected_longitude
):
    fake_transport.add(
        "GET", GEOCODER_ONE_LINE_ADDRESS_URL, fixture_bytes(RECORDED_RESPONSE)
    )
    job = _job_with_matches(admin_user)
    url = _url(ResearchJob, "change", job.pk)

    response = admin_client.post(url, change_form_data(admin_client, url, **button))

    job.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == url
    assert job.target_point.x == pytest.approx(expected_longitude)
    assert job.target_point_source == TargetPointSource.GEOCODER
    assert not job.is_target_point_confirmed


@pytest.mark.parametrize("index", ["5", "x", "²"])
def test_a_match_that_is_not_listed_is_refused_with_a_message(
    admin_client, admin_user, index
):
    job = _job_with_matches(admin_user)
    url = _url(ResearchJob, "change", job.pk)

    response = admin_client.post(
        url,
        change_form_data(admin_client, url, _use_geocoding_candidate=index),
        follow=True,
    )

    job.refresh_from_db()
    assert response.status_code == 200
    assert "That match is no longer listed." in response.text
    assert job.target_point is None


@pytest.mark.parametrize(
    "case",
    ["outside the state", "retired own competitor", "confirmed without a pin"],
)
def test_the_job_form_refuses_invalid_targets(admin_client, admin_user, case):
    receiving = make_competitor(display_name="Synthetic Receiving Day Care")
    retired = make_competitor(display_name="Synthetic Merged Day Care")
    Competitor.objects.filter(pk=retired.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )
    job = make_job(
        researcher=admin_user,
        point=None if case == "confirmed without a pin" else BOROUGH_HALL,
    )
    url = _url(ResearchJob, "change", job.pk)
    changes, message = {
        "outside the state": (
            {"target_point": "SRID=4326;POINT (-118.2428 34.0537)"},
            "The target must lie in New York State.",
        ),
        "retired own competitor": (
            {"own_competitor": retired.pk},
            "Choose the current competitor; this one was merged into another.",
        ),
        "confirmed without a pin": (
            {"confirm_target_point": "on"},
            "Place the pin before confirming it.",
        ),
    }[case]

    response = admin_client.post(url, change_form_data(admin_client, url, **changes))

    stored = ResearchJob.objects.get(pk=job.pk)
    assert response.status_code == 200
    assert message in response.text
    assert stored.own_competitor is None
    assert not stored.is_target_point_confirmed


def test_the_full_history_lists_observations_of_merged_competitors(
    admin_client, admin_user
):
    merged = make_competitor(display_name="Synthetic Merged Day Care")
    receiving = make_located_competitor(BOROUGH_HALL, name="Synthetic Receiving")
    merged_observation = make_observation(
        contact=make_contact(
            competitor=merged, researcher=admin_user, contact_date=date(2026, 9, 1)
        ),
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=50,
    )
    receiving_observation = make_observation(
        contact=make_contact(
            competitor=receiving, researcher=admin_user, contact_date=date(2026, 9, 2)
        ),
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=48,
    )
    make_observation(
        contact=make_contact(
            competitor=make_competitor(display_name="Synthetic Unrelated Day Care"),
            researcher=admin_user,
        ),
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=40,
    )
    Competitor.objects.filter(pk=merged.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )

    call_sheet = admin_client.get(
        reverse("admin:research_contact_call_sheet", args=(receiving.pk,))
    )
    history = admin_client.get(call_sheet.context["history_url"])
    unfiltered = admin_client.get(_url(Observation, "changelist"), {"competitor": "x"})

    assert list(history.context["cl"].result_list) == [
        receiving_observation,
        merged_observation,
    ]
    assert unfiltered.context["cl"].result_count == 3


def test_the_retraction_filters_split_current_and_retracted_records(
    admin_client, admin_user
):
    competitor = make_competitor(display_name="Synthetic Day Care")
    current_contact = make_contact(competitor=competitor, researcher=admin_user)
    current_observation = make_observation(
        contact=current_contact,
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=50,
    )
    retracted_observation = make_observation(
        contact=current_contact, kind=ObservationKind.STATED_PLACES, stated_places=40
    )
    retracted_contact = make_contact(competitor=competitor, researcher=admin_user)
    observation_of_retracted_contact = make_observation(
        contact=retracted_contact,
        kind=ObservationKind.WEEKS_OPEN,
        weeks_open_per_year=48,
    )
    contacts.retract_observation(
        observation=retracted_observation,
        researcher=admin_user,
        reason="Synthetic mistake",
    )
    contacts.retract_contact(
        contact=retracted_contact, researcher=admin_user, reason="Synthetic mistake"
    )

    def listed(model, retraction):
        response = admin_client.get(
            _url(model, "changelist"), {"retraction": retraction}
        )
        return set(response.context["cl"].result_list)

    assert listed(Observation, "current") == {current_observation}
    assert listed(Observation, "retracted") == {
        retracted_observation,
        observation_of_retracted_contact,
    }
    assert listed(Contact, "current") == {current_contact}
    assert listed(Contact, "retracted") == {retracted_contact}


def test_malformed_numbers_in_parameters_never_break_a_page(admin_client, seeded):
    competitor = seeded[Competitor]
    contact = seeded[Contact]

    call_sheet = admin_client.get(
        reverse("admin:research_contact_call_sheet", args=(competitor.pk,)),
        {"job": "²"},
    )
    retraction = admin_client.get(
        reverse("admin:research_contact_retract", args=(contact.pk,)),
        {"observation": "²"},
    )
    history = admin_client.get(_url(Observation, "changelist"), {"competitor": "²"})

    assert call_sheet.status_code == 200
    assert "This contact will not be linked to job" in call_sheet.text
    assert retraction.status_code == 404
    assert history.status_code == 200
    assert history.context["cl"].result_count == 1
