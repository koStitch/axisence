"""Researcher decisions on competitors: merges, separations, and provider moves.

Every public function runs in one transaction and logs exactly one CompetitorDecision.
Nothing else changes a placed provider's competitor.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from axisence.accounts.models import User
from axisence.providers.grouping import (
    refresh_competitor_summaries,
    summarize_competitor,
)
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


class ReviewError(Exception):
    """A researcher action that cannot be applied; its message is shown in the admin."""


@transaction.atomic
def merge_competitors(
    *,
    absorbed: Competitor,
    receiving: Competitor,
    researcher: User,
    review_flag: ReviewFlag | None = None,
) -> None:
    """Move every provider of the absorbed competitor to the receiving one.

    The absorbed competitor is retired with the receiving one as its receiver. With a
    review flag, the flag is resolved as same operator.
    """
    now = timezone.now()
    if absorbed.pk == receiving.pk:
        raise ReviewError("A competitor cannot be merged into itself.")
    if absorbed.retired_at is not None or receiving.retired_at is not None:
        raise ReviewError("A retired competitor cannot be merged.")
    if review_flag is not None:
        _require_open(review_flag)
        if review_flag.reason == ReviewFlagReason.CHANGED_KEYS:
            raise ReviewError("A changed-keys flag proposes no competitor to merge.")
    absorbed.providers.update(competitor=receiving)
    _retire(absorbed, receiving, now)
    if review_flag is not None:
        _resolve(review_flag, ReviewDecision.SAME_OPERATOR, researcher, now)
    _repoint_open_flags(absorbed, receiving, researcher, now)
    CompetitorDecision.objects.create(
        action=CompetitorDecisionAction.SAME_OPERATOR
        if review_flag is not None
        else CompetitorDecisionAction.COMPETITORS_MERGED,
        review_flag=review_flag,
        provider=review_flag.provider if review_flag is not None else None,
        from_competitor=absorbed,
        to_competitor=receiving,
        researcher=researcher,
        decided_at=now,
    )
    refresh_competitor_summaries({receiving.pk})


@transaction.atomic
def declare_different_operators(*, review_flag: ReviewFlag, researcher: User) -> None:
    """Resolve a proposal as two operators; grouping never proposes the pair again."""
    now = timezone.now()
    _require_open(review_flag)
    if review_flag.reason == ReviewFlagReason.CHANGED_KEYS:
        raise ReviewError("A changed-keys flag proposes no competitor to separate.")
    _resolve(review_flag, ReviewDecision.DIFFERENT_OPERATORS, researcher, now)
    CompetitorDecision.objects.create(
        action=CompetitorDecisionAction.DIFFERENT_OPERATORS,
        review_flag=review_flag,
        provider=review_flag.provider,
        from_competitor=review_flag.flagged_competitor,
        to_competitor=review_flag.proposed_competitor,
        researcher=researcher,
        decided_at=now,
    )


@transaction.atomic
def keep_provider(*, review_flag: ReviewFlag, researcher: User) -> None:
    """Resolve a changed-keys flag by keeping the provider in its competitor."""
    now = timezone.now()
    _require_open(review_flag)
    if review_flag.reason != ReviewFlagReason.CHANGED_KEYS:
        raise ReviewError("Only a changed-keys flag can keep its provider in place.")
    _resolve(review_flag, ReviewDecision.PROVIDER_STAYS, researcher, now)
    CompetitorDecision.objects.create(
        action=CompetitorDecisionAction.PROVIDER_STAYS,
        review_flag=review_flag,
        provider=review_flag.provider,
        from_competitor=review_flag.provider.competitor,
        to_competitor=None,
        researcher=researcher,
        decided_at=now,
    )


@transaction.atomic
def move_provider(
    *, provider: Provider, target: Competitor | None, researcher: User
) -> Competitor:
    """Move a provider to another competitor, or to a new one of its own when None.

    An old competitor left without providers is retired with the target as its
    receiver. Returns the competitor the provider now belongs to.
    """
    now = timezone.now()
    old = provider.competitor
    if target is not None and target.retired_at is not None:
        raise ReviewError("A retired competitor cannot receive providers.")
    if target is not None and old is not None and target.pk == old.pk:
        raise ReviewError("The provider already belongs to this competitor.")
    if (
        target is None
        and old is not None
        and not old.providers.exclude(pk=provider.pk).exists()
    ):
        raise ReviewError("The provider is already alone in its competitor.")
    if target is None:
        target = Competitor.objects.create(
            **summarize_competitor([provider]).as_fields()
        )
    provider.competitor = target
    provider.save(update_fields=("competitor",))
    changed_keys_flag = ReviewFlag.objects.filter(
        provider=provider,
        state=ReviewFlagState.OPEN,
        reason=ReviewFlagReason.CHANGED_KEYS,
    ).first()
    if changed_keys_flag is not None:
        _resolve(changed_keys_flag, ReviewDecision.PROVIDER_MOVED, researcher, now)
    if old is not None and not old.providers.exists():
        _retire(old, target, now)
        _repoint_open_flags(old, target, researcher, now)
    CompetitorDecision.objects.create(
        action=CompetitorDecisionAction.PROVIDER_MOVED,
        review_flag=changed_keys_flag,
        provider=provider,
        from_competitor=old,
        to_competitor=target,
        researcher=researcher,
        decided_at=now,
    )
    refreshed = {target.pk} if old is None else {target.pk, old.pk}
    refresh_competitor_summaries(refreshed)
    return target


def _require_open(review_flag: ReviewFlag) -> None:
    if review_flag.state != ReviewFlagState.OPEN:
        raise ReviewError("The review flag is already resolved.")


def _retire(retired: Competitor, receiving: Competitor, now: datetime) -> None:
    retired.retired_at = now
    retired.merged_into = receiving
    retired.is_active = False
    retired.save(update_fields=("retired_at", "merged_into", "is_active"))


def _resolve(
    review_flag: ReviewFlag,
    decision: ReviewDecision,
    researcher: User,
    now: datetime,
) -> None:
    review_flag.state = ReviewFlagState.RESOLVED
    review_flag.decision = decision
    review_flag.resolved_by = researcher
    review_flag.resolved_at = now
    review_flag.save(update_fields=("state", "decision", "resolved_by", "resolved_at"))


def _repoint_open_flags(
    retired: Competitor, receiving: Competitor, researcher: User, now: datetime
) -> None:
    """Point the retired competitor's open flags at the receiving competitor.

    A proposal that would become a self-pair or duplicate another open proposal is
    resolved as superseded instead, keeping its competitors.
    """
    flags = list(
        ReviewFlag.objects.filter(
            Q(flagged_competitor=retired) | Q(proposed_competitor=retired),
            state=ReviewFlagState.OPEN,
        ).order_by("pk")
    )
    open_pairs: set[frozenset[int | None]] = {
        frozenset(pair)
        for pair in ReviewFlag.objects.filter(
            state=ReviewFlagState.OPEN, proposed_competitor__isnull=False
        )
        .exclude(pk__in=[flag.pk for flag in flags])
        .values_list("flagged_competitor_id", "proposed_competitor_id")
    }
    superseded: list[ReviewFlag] = []
    repointed: list[ReviewFlag] = []
    for flag in flags:
        flagged_id = (
            receiving.pk
            if flag.flagged_competitor_id == retired.pk
            else flag.flagged_competitor_id
        )
        if flag.reason == ReviewFlagReason.CHANGED_KEYS:
            flag.flagged_competitor_id = flagged_id
            repointed.append(flag)
            continue
        proposed_id = (
            receiving.pk
            if flag.proposed_competitor_id == retired.pk
            else flag.proposed_competitor_id
        )
        pair = frozenset((flagged_id, proposed_id))
        if flagged_id == proposed_id or pair in open_pairs:
            superseded.append(flag)
            continue
        flag.flagged_competitor_id = flagged_id
        flag.proposed_competitor_id = proposed_id
        open_pairs.add(pair)
        repointed.append(flag)
    # Superseded flags close first, so the open-pair constraint never sees a duplicate.
    for flag in superseded:
        _resolve(flag, ReviewDecision.SUPERSEDED, researcher, now)
    for flag in repointed:
        flag.save(update_fields=("flagged_competitor", "proposed_competitor"))
