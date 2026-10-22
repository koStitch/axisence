"""Tests for the refusal of new age groups and schedules identical to current ones or
to another new one of the same contact.
"""

import logging
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from axisence.providers.models import Competitor
from axisence.research import contacts
from axisence.research.contacts import ContactEntry, NewDefinition, RecordingError
from axisence.research.models import Contact, ContactMethod, ObservationKind
from tests.providers.factories import make_competitor, make_researcher
from tests.reports.factories import add_definitions
from tests.research.call_sheet_posts import call_sheet_data
from tests.research.factories import make_contact

pytestmark = pytest.mark.django_db


@pytest.fixture
def researcher():
    return make_researcher()


@pytest.fixture
def competitor():
    return make_competitor(display_name="Synthetic Day Care")


def _age_group(key, name, youngest, oldest=None):
    values = {"name": name, "youngest_age_months": youngest}
    if oldest is not None:
        values["oldest_age_months"] = oldest
    return NewDefinition(key=key, kind=ObservationKind.AGE_GROUP, values=values)


def _schedule(key, name, days, hours=None):
    values = {"name": name, "days_per_week": days}
    if hours is not None:
        values["hours_per_day"] = Decimal(hours)
    return NewDefinition(key=key, kind=ObservationKind.SCHEDULE, values=values)


def _record(competitor, researcher, *new_definitions, retired_definition_ids=()):
    return contacts.record_contact(
        entry=ContactEntry(
            competitor=competitor,
            job=None,
            contact_date=timezone.localdate(),
            method=ContactMethod.CALL,
            person_role="",
            notes="",
            new_definitions=new_definitions,
            retired_definition_ids=frozenset(retired_definition_ids),
            observations=(),
        ),
        researcher=researcher,
    )


def _current_definitions(competitor, researcher, *, age_groups=(), schedules=()):
    return add_definitions(
        make_contact(competitor=competitor, researcher=researcher),
        age_groups=age_groups,
        schedules=schedules,
    )


def _refusal(competitor, researcher, *new_definitions, retired_definition_ids=()):
    with pytest.raises(RecordingError) as raised:
        _record(
            competitor,
            researcher,
            *new_definitions,
            retired_definition_ids=retired_definition_ids,
        )
    return str(raised.value)


def test_a_new_age_group_matching_a_current_one_is_refused(competitor, researcher):
    _current_definitions(competitor, researcher, age_groups=[("Infants", 6, 18)])
    contact_count = Contact.objects.count()

    message = _refusal(competitor, researcher, _age_group("A", "  infants ", 6, 18))

    assert message == (
        'New age group A matches the current age group "Infants". Choose that age '
        "group instead."
    )
    assert Contact.objects.count() == contact_count


def test_blank_oldest_ages_and_blank_hours_match_only_blank(competitor, researcher):
    _current_definitions(
        competitor,
        researcher,
        age_groups=[("Preschool", 36, None)],
        schedules=[("Full time", 5, None)],
    )

    _record(
        competitor,
        researcher,
        _age_group("A", "Preschool", 36, 60),
        _schedule("A", "Full time", 5, "10"),
    )

    assert "Preschool" in _refusal(
        competitor, researcher, _age_group("A", "Preschool", 36)
    )
    assert "Full time" in _refusal(
        competitor, researcher, _schedule("A", "Full time", 5)
    )
    assert "Full time" in _refusal(
        competitor, researcher, _schedule("A", "Full time", 5, "10.0")
    )


def test_a_new_schedule_matching_a_current_one_is_refused(competitor, researcher):
    _current_definitions(competitor, researcher, schedules=[("Full time", 5, "10")])

    message = _refusal(
        competitor, researcher, _schedule("B", "  FULL    time ", 5, "10")
    )

    assert message == (
        'New schedule B matches the current schedule "Full time". Choose that '
        "schedule instead."
    )


def test_definitions_of_merged_competitors_count(competitor, researcher):
    merged = make_competitor(display_name="Synthetic Merged Day Care")
    _current_definitions(merged, researcher, age_groups=[("Toddlers", 12, 36)])
    Competitor.objects.filter(pk=merged.pk).update(
        retired_at=timezone.now(), merged_into=competitor, is_active=False
    )

    message = _refusal(competitor, researcher, _age_group("A", "Toddlers", 12, 36))

    assert message.startswith("New age group A matches the current age group")


def test_a_definition_retired_in_the_same_contact_still_counts(competitor, researcher):
    (infants,), _ = _current_definitions(
        competitor, researcher, age_groups=[("Infants", 6, 18)]
    )

    message = _refusal(
        competitor,
        researcher,
        _age_group("A", "Infants", 6, 18),
        retired_definition_ids=[infants.pk],
    )

    assert message.startswith('New age group A matches the current age group "Infants"')


def test_two_new_definitions_of_one_contact_that_match_are_refused(
    competitor, researcher
):
    age_groups = _refusal(
        competitor,
        researcher,
        _age_group("A", "Infants", 6, 18),
        _age_group("B", "INFANTS", 6, 18),
    )
    schedules = _refusal(
        competitor,
        researcher,
        _schedule("A", "Part time", 3),
        _schedule("B", "part  time", 3),
    )

    assert age_groups == "New age group B matches new age group A."
    assert schedules == "New schedule B matches new schedule A."


def test_distinct_definitions_are_recorded(competitor, researcher):
    _current_definitions(
        competitor,
        researcher,
        age_groups=[("Infants", 6, 18)],
        schedules=[("Full time", 5, None)],
    )

    contact = _record(
        competitor,
        researcher,
        _age_group("A", "Infants", 6, 24),
        _age_group("B", "Young infants", 6, 18),
        _age_group("C", "Infants", 3, 18),
        _schedule("A", "Full time", 4),
        _schedule("B", "Full days", 5),
    )

    assert contact.observations.count() == 5


def test_the_call_sheet_shows_the_refusal_and_keeps_the_entered_values(
    admin_client, competitor, researcher
):
    _current_definitions(competitor, researcher, age_groups=[("Infants", 6, 18)])
    contact_count = Contact.objects.count()
    url = reverse("admin:research_contact_call_sheet", args=(competitor.pk,))

    response = admin_client.post(
        url,
        call_sheet_data(
            admin_client,
            url,
            **{
                "new-age-group-A-name": "infants",
                "new-age-group-A-youngest_age_months": "6",
                "new-age-group-A-oldest_age_months": "18",
            },
        ),
    )

    assert response.status_code == 200
    assert (
        escape(
            'New age group A matches the current age group "Infants". Choose that '
            "age group instead."
        )
        in response.text
    )
    assert 'value="infants"' in response.text
    assert Contact.objects.count() == contact_count


def test_refusals_are_not_logged(competitor, researcher, caplog):
    _current_definitions(competitor, researcher, age_groups=[("Infants", 6, 18)])

    with caplog.at_level(logging.DEBUG):
        _refusal(competitor, researcher, _age_group("A", "Infants", 6, 18))
        _refusal(
            competitor,
            researcher,
            _schedule("A", "Full time", 5),
            _schedule("B", "Full time", 5),
        )

    assert not any("matches" in record.getMessage() for record in caplog.records)
