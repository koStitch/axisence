"""Tests that every new admin page renders, searches, filters, and stays read-only."""

from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.urls import reverse
from django.utils import timezone

from axisence.demographics.models import CensusFigure, CensusVariable, TabulationArea
from axisence.ingestion.models import (
    IngestionRun,
    IngestionSnapshot,
    IngestionSource,
    SnapshotKind,
)
from axisence.providers.models import (
    Competitor,
    CompetitorDecision,
    CompetitorDecisionAction,
    Provider,
    ReviewFlag,
    ReviewFlagReason,
)
from tests.providers.factories import make_competitor, make_provider, make_run

SNAPSHOT_PATH = (
    "ny-ocfs/20260102T030405Z-run-1-child-care-regulated-programs-export.csv"
)


@pytest.fixture(autouse=True)
def seeded(db, admin_user):
    run = make_run(IngestionSource.NY_STATE_PROVIDERS)
    IngestionSnapshot.objects.create(
        run=run,
        kind=SnapshotKind.PROVIDER_EXPORT,
        path=SNAPSHOT_PATH,
        size_bytes=1024,
        sha256="0" * 64,
        fetched_at=timezone.now(),
    )
    competitor = make_competitor(
        display_name="Seeded Day Care", location=Point(-73.9754, 40.6826, srid=4326)
    )
    other = make_competitor(display_name="Seeded Other Day Care")
    provider = make_provider(original_name="Seeded Day Care", competitor=competitor)
    make_provider(original_name="Seeded Day Care", competitor=competitor)
    make_provider(original_name="Seeded Other Day Care", competitor=other)
    flag = ReviewFlag.objects.create(
        reason=ReviewFlagReason.SIMILAR_NAME,
        provider=provider,
        flagged_competitor=competitor,
        proposed_competitor=other,
        raised_by_run=run,
    )
    decision = CompetitorDecision.objects.create(
        action=CompetitorDecisionAction.DIFFERENT_OPERATORS,
        provider=provider,
        from_competitor=competitor,
        to_competitor=other,
        researcher=admin_user,
        decided_at=timezone.now(),
    )
    area = TabulationArea.objects.create(
        code="11217",
        boundary=MultiPolygon(
            Polygon.from_bbox((-73.9794, 40.6796, -73.9714, 40.6856)), srid=4326
        ),
    )
    variable = CensusVariable.objects.create(
        vintage=2024,
        table="DP05",
        code="DP05_0005",
        estimate_label="Estimate!!SEX AND AGE!!Total population!!Under 5 years",
        percent_label="Percent!!SEX AND AGE!!Total population!!Under 5 years",
    )
    CensusFigure.objects.create(
        tabulation_area=area,
        variable=variable,
        estimate=Decimal(3036),
        estimate_margin_of_error=Decimal(526),
        percent=Decimal("7.0"),
        percent_margin_of_error=Decimal("1.1"),
    )
    return {"run": run, "flag": flag, "decision": decision}


# Each row: the model, a search term, the first list filter set to the seeded value,
# and the seeded row's first list_display value, or None when it depends on a key.
CHANGELISTS = (
    (IngestionRun, "ny-ocfs", {"source__exact": "ny_state_providers"}, None),
    (
        IngestionSnapshot,
        "child-care",
        {"kind__exact": "provider_export"},
        SNAPSHOT_PATH,
    ),
    (Provider, "Seeded", {"source__exact": "nyc_health"}, "Seeded Day Care"),
    (Competitor, "Seeded", {"is_active__exact": "1"}, "Seeded Day Care"),
    (ReviewFlag, "Seeded", {"reason__exact": "similar_name"}, None),
    (CompetitorDecision, "Seeded", {"action__exact": "different_operators"}, None),
    (TabulationArea, "11217", {"boundary": "with"}, "11217"),
    (CensusVariable, "DP05_0005", {"vintage": "2024"}, "DP05_0005"),
    (CensusFigure, "11217", {"vintage": "2024"}, "11217"),
)


def _changelist_url(model):
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")


@pytest.mark.parametrize(
    ("model", "search_term", "filters", "expected_text"),
    CHANGELISTS,
    ids=[model.__name__ for model, *_ in CHANGELISTS],
)
def test_changelist_renders_with_search_and_filter(
    admin_client, seeded, model, search_term, filters, expected_text
):
    expected = (
        expected_text
        or {
            IngestionRun: str(seeded["run"]),
            ReviewFlag: str(seeded["flag"]),
            CompetitorDecision: str(seeded["decision"]),
        }[model]
    )

    response = admin_client.get(_changelist_url(model), {"q": search_term, **filters})

    assert response.status_code == 200
    assert expected in response.text
    assert "0 results" not in response.text


def test_competitor_provider_count_is_not_multiplied_by_a_provider_search(
    admin_client,
):
    # The search matches provider names too, which joins the providers again.
    response = admin_client.get(_changelist_url(Competitor), {"q": "Seeded Day Care"})

    assert {
        competitor.display_name: competitor.provider_count
        for competitor in response.context["cl"].result_list
    } == {"Seeded Day Care": 2, "Seeded Other Day Care": 1}


@pytest.mark.parametrize(
    "model",
    [model for model, *_ in CHANGELISTS],
    ids=[model.__name__ for model, *_ in CHANGELISTS],
)
def test_new_models_cannot_be_added_or_deleted_in_the_admin(rf, admin_user, model):
    request = rf.get("/admin/")
    request.user = admin_user
    model_admin = admin.site.get_model_admin(model)

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
