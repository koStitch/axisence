"""Tests for grouping providers into competitors and summarizing competitors."""

from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from axisence.providers import review
from axisence.providers.grouping import (
    assign_competitors,
    refresh_competitor_summaries,
    summarize_competitor,
)
from axisence.providers.models import (
    Competitor,
    Provider,
    ProviderKind,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
)
from tests.providers.factories import (
    make_competitor,
    make_provider,
    make_researcher,
    make_run,
)

SIMILAR_NAME = "Tender Year Childcare"
OTHER_SIMILAR_NAME = "Tender Years Childcare"


def _assign():
    return assign_competitors(make_run())


def _competitor_of(provider):
    provider.refresh_from_db()
    return provider.competitor


@pytest.mark.django_db
def test_equal_keys_join_one_competitor_across_both_sources():
    providers = [
        make_provider(source=ProviderSource.NY_STATE, original_name="Kids' Place"),
        make_provider(original_name="KIDS PLACE"),
        make_provider(original_name="Kids Place, Inc."),
    ]

    counts = _assign()

    assert counts.competitors_created == 1
    assert counts.review_flags_raised == 0
    assert {_competitor_of(provider) for provider in providers} == {
        Competitor.objects.get()
    }


@pytest.mark.django_db
def test_unequal_name_at_one_address_gets_its_own_competitor():
    first = make_provider(original_name="Sunshine Kids")
    second = make_provider(original_name="Little Scholars Academy")

    counts = _assign()

    assert counts.competitors_created == 2
    assert _competitor_of(first) != _competitor_of(second)
    assert not ReviewFlag.objects.exists()


@pytest.mark.django_db
def test_omitted_address_always_gets_a_competitor_of_its_own():
    first = make_provider(source=ProviderSource.NY_STATE, street_address="")
    second = make_provider(source=ProviderSource.NY_STATE, street_address="")

    counts = _assign()

    assert first.address_key == ""
    assert counts.competitors_created == 2
    assert _competitor_of(first) != _competitor_of(second)


@pytest.mark.django_db
def test_placed_provider_keeps_its_competitor_on_later_runs():
    first_competitor = make_competitor()
    second_competitor = make_competitor()
    # Equal keys in two competitors, as after a researcher moved one of them.
    first = make_provider(competitor=first_competitor)
    second = make_provider(competitor=second_competitor)

    counts = _assign()

    assert counts.competitors_created == 0
    assert _competitor_of(first) == first_competitor
    assert _competitor_of(second) == second_competitor


@pytest.mark.django_db
def test_similar_name_at_one_address_raises_one_flag_per_competitor_pair():
    existing = make_competitor()
    make_provider(original_name=SIMILAR_NAME, competitor=existing)
    preschool = make_provider(
        source_identifier="DC9100001", original_name=OTHER_SIMILAR_NAME
    )
    infant_toddler = make_provider(
        source_identifier="DC9100002", original_name=OTHER_SIMILAR_NAME
    )

    counts = _assign()

    flag = ReviewFlag.objects.get()
    assert counts.review_flags_raised == 1
    assert _competitor_of(preschool) == _competitor_of(infant_toddler)
    assert flag.reason == ReviewFlagReason.SIMILAR_NAME
    assert flag.provider == preschool
    assert flag.flagged_competitor == _competitor_of(preschool)
    assert flag.proposed_competitor == existing


@pytest.mark.django_db
def test_equal_keys_in_several_competitors_give_an_own_competitor_and_one_flag_each():
    first = make_provider(source_identifier="DC9100001")
    second = make_provider(source_identifier="DC9100002")
    _assign()
    original = _competitor_of(first)
    moved_to = review.move_provider(
        provider=Provider.objects.get(pk=second.pk),
        target=None,
        researcher=make_researcher(),
    )
    newcomer = make_provider()

    counts = _assign()

    own = _competitor_of(newcomer)
    flags = ReviewFlag.objects.order_by("pk")
    assert own not in (original, moved_to)
    assert counts.competitors_created == 1
    assert [
        (flag.reason, flag.flagged_competitor, flag.proposed_competitor)
        for flag in flags
    ] == [
        (ReviewFlagReason.EQUAL_KEYS_IN_SEVERAL_COMPETITORS, own, original),
        (ReviewFlagReason.EQUAL_KEYS_IN_SEVERAL_COMPETITORS, own, moved_to),
    ]


@pytest.mark.django_db
def test_no_flag_for_a_pair_declared_different_operators():
    first = make_competitor()
    second = make_competitor()
    provider = make_provider(original_name=SIMILAR_NAME, competitor=first)
    make_provider(original_name=OTHER_SIMILAR_NAME, competitor=second)
    flag = ReviewFlag.objects.create(
        reason=ReviewFlagReason.SIMILAR_NAME,
        provider=provider,
        flagged_competitor=first,
        proposed_competitor=second,
    )
    review.declare_different_operators(review_flag=flag, researcher=make_researcher())
    newcomer = make_provider(original_name=OTHER_SIMILAR_NAME)

    counts = _assign()

    assert _competitor_of(newcomer) == second
    assert counts.review_flags_raised == 0
    assert ReviewFlag.objects.count() == 1


@pytest.mark.django_db
def test_no_second_open_flag_for_the_same_pair():
    first = make_competitor()
    second = make_competitor()
    provider = make_provider(original_name=SIMILAR_NAME, competitor=first)
    make_provider(original_name=OTHER_SIMILAR_NAME, competitor=second)
    ReviewFlag.objects.create(
        reason=ReviewFlagReason.SIMILAR_NAME,
        provider=provider,
        flagged_competitor=first,
        proposed_competitor=second,
    )
    make_provider(original_name=OTHER_SIMILAR_NAME)

    counts = _assign()

    assert counts.review_flags_raised == 0
    assert ReviewFlag.objects.count() == 1


@pytest.mark.django_db
def test_automation_never_merges_existing_competitors():
    first_competitor = make_competitor()
    second_competitor = make_competitor()
    make_provider(competitor=first_competitor)
    make_provider(competitor=second_competitor)
    make_provider()

    _assign()

    assert Competitor.objects.filter(retired_at__isnull=True).count() == 3
    assert first_competitor.providers.count() == 1
    assert second_competitor.providers.count() == 1


@pytest.mark.django_db
def test_competitor_summary_prefers_an_active_member_with_address_and_location():
    location = Point(-73.9754, 40.6826, srid=4326)
    members = [
        make_provider(
            original_name="Missing Member",
            location=location,
            missing_since=timezone.now() - timedelta(days=1),
        ),
        make_provider(original_name="Member Without Address", street_address=""),
        make_provider(original_name="Member Without Location"),
        make_provider(
            source=ProviderSource.NY_STATE,
            original_name="NY State Member",
            location=location,
        ),
        make_provider(original_name="Representative Member", location=location),
    ]

    summary = summarize_competitor(members)

    assert summary.display_name == "Representative Member"
    assert summary.street_address == "100 Example Street"
    assert summary.location == location
    assert summary.is_active


@pytest.mark.django_db
def test_competitor_is_inactive_when_every_provider_is_missing_and_active_again_on_reactivation():  # noqa: E501
    competitor = make_competitor()
    first = make_provider(competitor=competitor)
    make_provider(competitor=competitor)

    Provider.objects.update(missing_since=timezone.now())
    refresh_competitor_summaries()
    competitor.refresh_from_db()
    inactive = competitor.is_active
    Provider.objects.filter(pk=first.pk).update(missing_since=None)
    refresh_competitor_summaries()
    competitor.refresh_from_db()

    assert inactive is False
    assert competitor.is_active is True


@pytest.mark.django_db
def test_competitor_is_school_based_when_any_provider_is():
    school_based = make_provider(kind=ProviderKind.SCHOOL_BASED_CHILD_CARE)
    make_provider()

    _assign()

    assert _competitor_of(school_based).is_school_based
