"""Tests for the call sheet: what it shows, and what each post records."""

from datetime import date

import pytest
from django.urls import reverse
from django.utils import timezone

from axisence.providers.models import Competitor, ProviderKind, ProviderSource
from axisence.research.models import (
    Contact,
    JobCompetitor,
    JobStatus,
    Observation,
    ObservationKind,
    OperatingStatus,
)
from tests.providers.factories import make_provider
from tests.research.call_sheet_posts import call_sheet_data
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_job,
    make_located_competitor,
    make_observation,
)

pytestmark = pytest.mark.django_db


def _call_sheet_url(competitor, job=None):
    url = reverse("admin:research_contact_call_sheet", args=(competitor.pk,))
    return url if job is None else f"{url}?job={job.pk}"


@pytest.fixture
def competitor():
    return make_located_competitor(BOROUGH_HALL, total_capacity=40)


@pytest.fixture
def earlier_contact(competitor, admin_user):
    contact = make_contact(
        competitor=competitor, researcher=admin_user, contact_date=date(2026, 9, 1)
    )
    make_observation(
        contact=contact, kind=ObservationKind.WEEKS_OPEN, weeks_open_per_year=50
    )
    make_observation(
        contact=contact,
        kind=ObservationKind.AGE_GROUP,
        name="Toddlers",
        youngest_age_months=18,
        oldest_age_months=36,
    )
    make_observation(
        contact=contact,
        kind=ObservationKind.SCHEDULE,
        name="Part time",
        days_per_week=3,
    )
    make_observation(
        contact=contact, kind=ObservationKind.THREE_K_FOR_ALL, yes_no_answer=True
    )
    make_observation(
        contact=contact,
        kind=ObservationKind.LAST_FEE_RISE,
        last_fee_rise_on=date(2025, 3, 1),
    )
    return contact


def _definition(kind):
    return Observation.objects.get(kind=kind)


def _new_contact(earlier_contact):
    return Contact.objects.exclude(pk=earlier_contact.pk).get()


def test_the_call_sheet_shows_published_data_labels_and_current_values(
    admin_client, competitor, earlier_contact
):
    published = competitor.providers.get()
    published.phone_number = "(718) 555-0100"
    published.save(update_fields=("phone_number",))
    make_provider(
        competitor=competitor,
        source=ProviderSource.NY_STATE,
        kind=ProviderKind.SCHOOL_AGE_CHILD_CARE,
        ny_state_is_phone_omitted=True,
    )

    response = admin_client.get(_call_sheet_url(competitor))

    assert response.status_code == 200
    assert "100 Example Street" in response.text
    assert "(718) 555-0100" in response.text
    assert "Withheld by the regulator" in response.text
    assert "Licensed capacity: 40 (incomplete)" in response.text
    assert "50 weeks per year" in response.text
    assert "2026-09-01" in response.text


def test_prefilled_slots_start_not_covered_and_record_nothing(
    admin_client, competitor, earlier_contact
):
    url = _call_sheet_url(competitor)
    data = call_sheet_data(admin_client, url)

    # The untouched yes-or-no and month-and-year slots must not count as changed.
    response = admin_client.post(url, data)

    assert data["weeks_open-coverage"] == "not_covered"
    assert response.status_code == 302
    assert not _new_contact(earlier_contact).observations.exists()


def test_confirmed_slots_record_the_current_value_again(
    admin_client, competitor, earlier_contact
):
    url = _call_sheet_url(competitor)

    response = admin_client.post(
        url, call_sheet_data(admin_client, url, **{"weeks_open-coverage": "confirmed"})
    )

    assert response.status_code == 302
    [observation] = _new_contact(earlier_contact).observations.all()
    assert observation.kind == ObservationKind.WEEKS_OPEN
    assert observation.weeks_open_per_year == 50


def test_new_values_and_new_rows_are_recorded_in_one_contact(
    admin_client, competitor, earlier_contact
):
    url = _call_sheet_url(competitor)
    toddlers = _definition(ObservationKind.AGE_GROUP)
    part_time = _definition(ObservationKind.SCHEDULE)
    data = call_sheet_data(
        admin_client,
        url,
        **{
            "contact-notes": "synthetic note marker 7f3a",
            "retirement-retired_age_groups": str(toddlers.pk),
            "retirement-retired_schedules": str(part_time.pk),
            "new-age-group-A-name": "Infants",
            "new-age-group-A-youngest_age_months": "6",
            "new-age-group-A-oldest_age_months": "18",
            "new-schedule-A-name": "Full time",
            "new-schedule-A-days_per_week": "5",
            "new-price-1-age_group": "new:A",
            "new-price-1-schedule": "new:A",
            "new-price-1-amount": "2400",
            "new-price-1-price_period": "monthly",
            "operating_status-coverage": "new_value",
            "operating_status-operating_status": OperatingStatus.OPERATING,
            "last_fee_rise-coverage": "new_value",
            "last_fee_rise-month": "4",
            "last_fee_rise-year": "2026",
        },
    )

    response = admin_client.post(url, data)

    assert response.status_code == 302
    contact = _new_contact(earlier_contact)
    assert contact.notes == "synthetic note marker 7f3a"
    assert sorted(contact.observations.values_list("kind", flat=True)) == sorted(
        [
            ObservationKind.AGE_GROUP,
            ObservationKind.SCHEDULE,
            ObservationKind.AGE_GROUP_RETIREMENT,
            ObservationKind.SCHEDULE_RETIREMENT,
            ObservationKind.PRICE,
            ObservationKind.OPERATING_STATUS,
            ObservationKind.LAST_FEE_RISE,
        ]
    )
    observations = contact.observations
    price = observations.get(kind=ObservationKind.PRICE)
    assert price.value_summary() == "$2,400.00 monthly for Infants on Full time"
    assert (
        observations.get(kind=ObservationKind.AGE_GROUP_RETIREMENT).age_group
        == toddlers
    )
    assert (
        observations.get(kind=ObservationKind.SCHEDULE_RETIREMENT).schedule == part_time
    )
    assert observations.get(
        kind=ObservationKind.LAST_FEE_RISE
    ).last_fee_rise_on == date(2026, 4, 1)


def _cross_row_changes(case, toddlers, part_time):
    return {
        "blank new definition referenced": {
            "new-price-1-age_group": "new:B",
            "new-price-1-schedule": f"existing:{part_time.pk}",
            "new-price-1-amount": "2400",
            "new-price-1-price_period": "monthly",
        },
        "retired definition used": {
            "retirement-retired_age_groups": str(toddlers.pk),
            "new-availability-1-age_group": f"existing:{toddlers.pk}",
            "new-availability-1-availability_status": "openings",
        },
        "offered and not offered": {
            "discount-sibling-coverage": "new_value",
            "discount-sibling-is_none_offered": "on",
            "new-discount-1-discount_kind": "sibling",
            "new-discount-1-percent": "10",
        },
        "month without a year": {
            "last_fee_rise-coverage": "new_value",
            "last_fee_rise-month": "4",
            "last_fee_rise-year": None,
        },
        "slot no longer on the sheet": {
            f"price-{toddlers.pk + 1000}-{part_time.pk}-coverage": "new_value",
        },
        "retirement no longer offered": {
            "retirement-retired_age_groups": str(toddlers.pk + 1000),
        },
    }[case]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            "blank new definition referenced",
            "New age group B is chosen in a row but left blank.",
        ),
        (
            "retired definition used",
            "A retired age group or schedule cannot get new facts in the same contact.",
        ),
        (
            "offered and not offered",
            "A kind cannot be both offered and not offered in one contact.",
        ),
        ("month without a year", "Give both the month and the year."),
        (
            "slot no longer on the sheet",
            "The age groups or schedules changed since this call sheet was opened.",
        ),
        ("retirement no longer offered", "is not one of the available choices."),
    ],
)
def test_entries_that_break_the_rules_across_rows_are_refused(
    admin_client, competitor, earlier_contact, case, message
):
    url = _call_sheet_url(competitor)
    changes = _cross_row_changes(
        case,
        _definition(ObservationKind.AGE_GROUP),
        _definition(ObservationKind.SCHEDULE),
    )

    response = admin_client.post(url, call_sheet_data(admin_client, url, **changes))

    assert response.status_code == 200
    assert "Please correct the errors below." in response.text
    assert message in response.text
    assert Contact.objects.get() == earlier_contact


@pytest.mark.parametrize("coverage", ["not_covered", "confirmed"])
def test_a_changed_prefilled_value_needs_new_value(
    admin_client, competitor, earlier_contact, coverage
):
    url = _call_sheet_url(competitor)

    response = admin_client.post(
        url,
        call_sheet_data(
            admin_client,
            url,
            **{
                "weeks_open-coverage": coverage,
                "weeks_open-weeks_open_per_year": "48",
            },
        ),
    )

    assert response.status_code == 200
    assert "Choose New value to record a changed value." in response.text
    assert Contact.objects.get() == earlier_contact


def test_a_retired_competitors_call_sheet_leads_to_the_receiving_competitor(
    admin_client, admin_user, competitor
):
    retired = make_located_competitor(BOROUGH_HALL)
    make_observation(
        contact=make_contact(competitor=retired, researcher=admin_user),
        kind=ObservationKind.AGE_GROUP,
        name="Infants",
        youngest_age_months=6,
    )
    Competitor.objects.filter(pk=retired.pk).update(
        retired_at=timezone.now(), merged_into=competitor, is_active=False
    )

    response = admin_client.get(f"{_call_sheet_url(retired)}?job=7")
    receiving_sheet = admin_client.get(_call_sheet_url(competitor))

    assert response.status_code == 302
    assert response["Location"] == f"{_call_sheet_url(competitor)}?job=7"
    assert f"(from merged competitor #{retired.pk})" in receiving_sheet.text


@pytest.mark.parametrize(
    ("status", "linked"),
    [(JobStatus.RESEARCHING, True), (JobStatus.DRAFT, False)],
    ids=["researching", "draft"],
)
def test_the_job_link_holds_only_while_the_job_is_researching(
    admin_client, admin_user, competitor, status, linked
):
    job = make_job(
        researcher=admin_user, point=BOROUGH_HALL, confirmed=True, status=status
    )
    JobCompetitor.objects.create(job=job, competitor=competitor)
    url = _call_sheet_url(competitor, job)

    page = admin_client.get(url)
    response = admin_client.post(url, call_sheet_data(admin_client, url))

    assert response.status_code == 302
    contact = Contact.objects.get()
    if linked:
        assert contact.job == job
        assert "This contact will be linked to" in page.text
    else:
        assert contact.job is None
        assert (
            f"This contact will not be linked to job #{job.pk}: it is Draft, not "
            "Researching." in page.text
        )
