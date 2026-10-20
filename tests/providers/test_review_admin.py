"""Tests for the researcher actions offered in the admin."""

import pytest
from django.urls import reverse

from axisence.providers.models import (
    Competitor,
    CompetitorDecision,
    CompetitorDecisionAction,
    Provider,
    ReviewDecision,
    ReviewFlag,
    ReviewFlagReason,
)
from tests.providers.factories import make_competitor, make_provider


def _competitor_with_provider(name):
    competitor = make_competitor(display_name=name)
    provider = make_provider(original_name=name, competitor=competitor)
    return competitor, provider


def _change_form_data(admin_client, url, **changes):
    # The posted form must carry the inline formsets the change page renders.
    response = admin_client.get(url)
    data = {}
    for inline_admin_formset in response.context["inline_admin_formsets"]:
        formset = inline_admin_formset.formset
        management_form = formset.management_form
        for name in management_form.fields:
            data[management_form.add_prefix(name)] = management_form[name].value()
        for form in formset.forms:
            for name in form.fields:
                value = form[name].value()
                if value is not None:
                    data[form.add_prefix(name)] = value
    data.update(changes)
    return data


@pytest.mark.django_db
def test_same_operator_admin_action_merges_and_resolves(admin_client, admin_user):
    flagged, provider = _competitor_with_provider("Tender Year Childcare")
    proposed, _ = _competitor_with_provider("Tender Years Childcare")
    flag = ReviewFlag.objects.create(
        reason=ReviewFlagReason.SIMILAR_NAME,
        provider=provider,
        flagged_competitor=flagged,
        proposed_competitor=proposed,
    )

    response = admin_client.post(
        reverse("admin:providers_reviewflag_changelist"),
        {"action": "resolve_as_same_operator", "_selected_action": [flag.pk]},
    )

    flag.refresh_from_db()
    provider.refresh_from_db()
    flagged.refresh_from_db()
    assert response.status_code == 302
    assert flag.decision == ReviewDecision.SAME_OPERATOR
    assert flag.resolved_by == admin_user
    assert provider.competitor == proposed
    assert flagged.merged_into == proposed


@pytest.mark.django_db
def test_editing_a_providers_competitor_moves_it(admin_client):
    old, provider = _competitor_with_provider("Synthetic Day Care")
    target, _ = _competitor_with_provider("Other Day Care")
    url = reverse("admin:providers_provider_change", args=(provider.pk,))

    response = admin_client.post(
        url, _change_form_data(admin_client, url, competitor=target.pk)
    )

    provider.refresh_from_db()
    old.refresh_from_db()
    assert response.status_code == 302
    assert provider.competitor == target
    assert old.merged_into == target
    assert (
        CompetitorDecision.objects.get().action
        == CompetitorDecisionAction.PROVIDER_MOVED
    )


@pytest.mark.django_db
def test_move_to_new_competitor_admin_action(admin_client):
    old, provider = _competitor_with_provider("Synthetic Day Care")
    make_provider(original_name="Synthetic Day Care", competitor=old)

    response = admin_client.post(
        reverse("admin:providers_provider_changelist"),
        {"action": "move_to_new_competitor", "_selected_action": [provider.pk]},
    )

    provider.refresh_from_db()
    assert response.status_code == 302
    assert provider.competitor not in (old, None)
    assert list(provider.competitor.providers.all()) == [provider]


@pytest.mark.django_db
def test_editing_merged_into_merges_competitors(admin_client):
    absorbed, provider = _competitor_with_provider("Synthetic Day Care")
    receiving, _ = _competitor_with_provider("Other Day Care")
    url = reverse("admin:providers_competitor_change", args=(absorbed.pk,))

    response = admin_client.post(
        url, _change_form_data(admin_client, url, merged_into=receiving.pk)
    )

    absorbed.refresh_from_db()
    provider.refresh_from_db()
    assert response.status_code == 302
    assert absorbed.retired_at is not None
    assert absorbed.merged_into == receiving
    assert provider.competitor == receiving
    assert (
        CompetitorDecision.objects.get().action
        == CompetitorDecisionAction.COMPETITORS_MERGED
    )


@pytest.mark.django_db
def test_retired_competitor_is_rejected_as_a_target(admin_client):
    current, provider = _competitor_with_provider("Synthetic Day Care")
    retired = make_competitor(display_name="Retired Day Care")
    Competitor.objects.filter(pk=retired.pk).update(
        retired_at=current.created_at, merged_into=current
    )
    url = reverse("admin:providers_provider_change", args=(provider.pk,))

    response = admin_client.post(
        url, _change_form_data(admin_client, url, competitor=retired.pk)
    )
    autocomplete = admin_client.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "providers",
            "model_name": "provider",
            "field_name": "competitor",
            "term": "Day Care",
        },
    )

    assert response.status_code == 200
    assert "A retired competitor cannot receive providers." in response.text
    assert Provider.objects.get(pk=provider.pk).competitor == current
    assert [result["id"] for result in autocomplete.json()["results"]] == [
        str(current.pk)
    ]
