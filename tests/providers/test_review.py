"""Tests for the researcher decisions in review.py."""

import pytest

from axisence.providers import review
from axisence.providers.grouping import assign_competitors, different_operator_pairs
from axisence.providers.models import (
    Competitor,
    CompetitorDecision,
    CompetitorDecisionAction,
    Provider,
    ReviewDecision,
    ReviewFlag,
    ReviewFlagReason,
    ReviewFlagState,
)
from tests.providers.factories import (
    make_competitor,
    make_provider,
    make_researcher,
    make_run,
)


@pytest.fixture
def researcher(db):
    return make_researcher()


def _competitor_with_provider(name):
    competitor = make_competitor(display_name=name)
    provider = make_provider(original_name=name, competitor=competitor)
    return competitor, provider


def _similar_name_flag(provider, flagged, proposed):
    return ReviewFlag.objects.create(
        reason=ReviewFlagReason.SIMILAR_NAME,
        provider=provider,
        flagged_competitor=flagged,
        proposed_competitor=proposed,
    )


def _changed_keys_flag(provider):
    return ReviewFlag.objects.create(
        reason=ReviewFlagReason.CHANGED_KEYS,
        provider=provider,
        flagged_competitor=provider.competitor,
    )


def _reload(instance):
    instance.refresh_from_db()
    return instance


@pytest.mark.django_db
def test_same_operator_moves_providers_retires_the_flagged_competitor_and_logs_the_decision(  # noqa: E501
    researcher,
):
    flagged, provider = _competitor_with_provider("Tender Year Childcare")
    proposed, _ = _competitor_with_provider("Tender Years Childcare")
    flag = _similar_name_flag(provider, flagged, proposed)

    review.merge_competitors(
        absorbed=flagged, receiving=proposed, researcher=researcher, review_flag=flag
    )

    flagged = _reload(flagged)
    flag = _reload(flag)
    decision = CompetitorDecision.objects.get()
    assert _reload(provider).competitor == proposed
    assert flagged.retired_at is not None
    assert flagged.merged_into == proposed
    assert flagged.is_active is False
    assert flag.state == ReviewFlagState.RESOLVED
    assert flag.decision == ReviewDecision.SAME_OPERATOR
    assert flag.resolved_by == researcher
    assert decision.action == CompetitorDecisionAction.SAME_OPERATOR
    assert decision.review_flag == flag
    assert decision.provider == provider
    assert (decision.from_competitor, decision.to_competitor) == (flagged, proposed)
    assert decision.researcher == researcher


@pytest.mark.django_db
def test_different_operators_resolves_the_flag_and_separates_the_pair(researcher):
    flagged, provider = _competitor_with_provider("Tender Year Childcare")
    proposed, _ = _competitor_with_provider("Tender Years Childcare")
    flag = _similar_name_flag(provider, flagged, proposed)

    review.declare_different_operators(review_flag=flag, researcher=researcher)

    flag = _reload(flag)
    decision = CompetitorDecision.objects.get()
    assert flag.decision == ReviewDecision.DIFFERENT_OPERATORS
    assert decision.action == CompetitorDecisionAction.DIFFERENT_OPERATORS
    assert (decision.from_competitor, decision.to_competitor) == (flagged, proposed)
    assert different_operator_pairs() == {frozenset((flagged.pk, proposed.pk))}
    assert _reload(provider).competitor == flagged


@pytest.mark.django_db
def test_provider_stays_resolves_a_changed_keys_flag(researcher):
    competitor, provider = _competitor_with_provider("Synthetic Day Care")
    flag = _changed_keys_flag(provider)

    review.keep_provider(review_flag=flag, researcher=researcher)

    decision = CompetitorDecision.objects.get()
    assert _reload(flag).decision == ReviewDecision.PROVIDER_STAYS
    assert decision.action == CompetitorDecisionAction.PROVIDER_STAYS
    assert (decision.from_competitor, decision.to_competitor) == (competitor, None)
    assert decision.provider == provider


@pytest.mark.django_db
def test_moving_a_provider_to_an_existing_competitor_resolves_its_changed_keys_flag(
    researcher,
):
    old, provider = _competitor_with_provider("Synthetic Day Care")
    make_provider(competitor=old)
    target, _ = _competitor_with_provider("Other Day Care")
    flag = _changed_keys_flag(provider)

    review.move_provider(provider=provider, target=target, researcher=researcher)

    decision = CompetitorDecision.objects.get()
    assert _reload(provider).competitor == target
    assert _reload(flag).decision == ReviewDecision.PROVIDER_MOVED
    assert decision.action == CompetitorDecisionAction.PROVIDER_MOVED
    assert decision.review_flag == flag
    assert (decision.from_competitor, decision.to_competitor) == (old, target)
    assert _reload(old).retired_at is None


@pytest.mark.django_db
def test_moving_the_last_provider_retires_the_old_competitor_with_its_receiver(
    researcher,
):
    old, provider = _competitor_with_provider("Synthetic Day Care")
    target, _ = _competitor_with_provider("Other Day Care")

    review.move_provider(provider=provider, target=target, researcher=researcher)

    old = _reload(old)
    assert old.retired_at is not None
    assert old.merged_into == target
    assert old.is_active is False
    assert CompetitorDecision.objects.get().review_flag is None


@pytest.mark.django_db
def test_moving_a_provider_to_a_new_competitor_of_its_own(researcher):
    old, provider = _competitor_with_provider("Synthetic Day Care")
    make_provider(original_name="Synthetic Day Care", competitor=old)

    new = review.move_provider(provider=provider, target=None, researcher=researcher)

    assert new not in (old, None)
    assert list(new.providers.all()) == [provider]
    assert new.display_name == "Synthetic Day Care"
    assert _reload(old).retired_at is None
    assert CompetitorDecision.objects.get().to_competitor == new


@pytest.mark.django_db
def test_moved_provider_stays_put_when_grouping_runs_again(researcher):
    first = make_provider(source_identifier="DC9100001")
    second = make_provider(source_identifier="DC9100002")
    assign_competitors(make_run())
    second = Provider.objects.get(pk=second.pk)
    own = review.move_provider(provider=second, target=None, researcher=researcher)

    assign_competitors(make_run())

    assert _reload(second).competitor == own
    assert _reload(first).competitor != own


@pytest.mark.django_db
def test_merging_two_competitors_without_a_flag(researcher):
    absorbed, provider = _competitor_with_provider("Synthetic Day Care")
    receiving, _ = _competitor_with_provider("Other Day Care")

    review.merge_competitors(
        absorbed=absorbed, receiving=receiving, researcher=researcher
    )

    decision = CompetitorDecision.objects.get()
    assert _reload(provider).competitor == receiving
    assert decision.action == CompetitorDecisionAction.COMPETITORS_MERGED
    assert decision.review_flag is None
    assert decision.provider is None
    assert (decision.from_competitor, decision.to_competitor) == (absorbed, receiving)


@pytest.mark.django_db
def test_open_flags_of_a_retired_competitor_are_repointed_or_superseded(researcher):
    retired, retired_provider = _competitor_with_provider("Retired Day Care")
    receiving, _ = _competitor_with_provider("Receiving Day Care")
    third, _ = _competitor_with_provider("Third Day Care")
    fourth, fourth_provider = _competitor_with_provider("Fourth Day Care")
    becomes_self_pair = _similar_name_flag(retired_provider, retired, receiving)
    repointed = _similar_name_flag(retired_provider, retired, third)
    becomes_duplicate = _similar_name_flag(fourth_provider, fourth, retired)
    already_open = _similar_name_flag(fourth_provider, fourth, receiving)
    changed_keys = _changed_keys_flag(retired_provider)

    review.merge_competitors(
        absorbed=retired, receiving=receiving, researcher=researcher
    )

    for flag in (becomes_self_pair, becomes_duplicate):
        flag = _reload(flag)
        assert flag.decision == ReviewDecision.SUPERSEDED
        assert retired in (flag.flagged_competitor, flag.proposed_competitor)
    repointed = _reload(repointed)
    assert repointed.state == ReviewFlagState.OPEN
    assert (repointed.flagged_competitor, repointed.proposed_competitor) == (
        receiving,
        third,
    )
    assert _reload(already_open).state == ReviewFlagState.OPEN
    changed_keys = _reload(changed_keys)
    assert changed_keys.state == ReviewFlagState.OPEN
    assert changed_keys.flagged_competitor == receiving


@pytest.mark.django_db
def test_actions_on_a_retired_competitor_are_refused(researcher):
    retired, _ = _competitor_with_provider("Retired Day Care")
    receiving, provider = _competitor_with_provider("Receiving Day Care")
    other, _ = _competitor_with_provider("Other Day Care")
    review.merge_competitors(
        absorbed=retired, receiving=receiving, researcher=researcher
    )
    retired = Competitor.objects.get(pk=retired.pk)

    with pytest.raises(review.ReviewError):
        review.merge_competitors(
            absorbed=retired, receiving=other, researcher=researcher
        )
    with pytest.raises(review.ReviewError):
        review.merge_competitors(
            absorbed=other, receiving=retired, researcher=researcher
        )
    with pytest.raises(review.ReviewError):
        review.move_provider(provider=provider, target=retired, researcher=researcher)
    assert CompetitorDecision.objects.count() == 1
