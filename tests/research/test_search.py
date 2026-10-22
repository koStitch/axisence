"""Tests for the competitor search, the selection, the labels, and query counts."""

import re
from datetime import date

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from axisence.providers.models import (
    Borough,
    Competitor,
    ProviderKind,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
)
from axisence.research import search
from axisence.research.job_competitors import load_displayed_job_competitors
from axisence.research.jobs import ResearchJobError
from axisence.research.labels import load_competitor_labels
from axisence.research.merges import MergeChains
from axisence.research.models import (
    JobCompetitor,
    JobStatus,
    ObservationKind,
    OperatingStatus,
)
from tests.providers.factories import make_competitor, make_provider, make_researcher
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_job,
    make_located_competitor,
    make_observation,
    point_north_of,
)

pytestmark = pytest.mark.django_db


def _job(**fields):
    return make_job(
        researcher=make_researcher(), point=BOROUGH_HALL, confirmed=True, **fields
    )


def _retire(competitor, receiving):
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )


def _report_closure(competitor, status, contact_date):
    contact = make_contact(
        competitor=competitor,
        researcher=make_researcher(f"caller-{competitor.pk}"),
        contact_date=contact_date,
    )
    make_observation(
        contact=contact, kind=ObservationKind.OPERATING_STATUS, operating_status=status
    )


def _found_ids(job):
    return [
        competitor.pk
        for competitor, _ in search.find_competitors(job, MergeChains.load())
    ]


def _page(job):
    return search.build_search_page(job, MergeChains.load())


def test_results_are_active_located_current_competitors_within_the_radius():
    job = _job()
    included = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    inactive = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    Competitor.objects.filter(pk=inactive.pk).update(is_active=False)
    retired = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    _retire(retired, included)
    unlocated = make_competitor(display_name="Synthetic Unlocated Day Care")
    make_provider(competitor=unlocated)
    make_located_competitor(point_north_of(BOROUGH_HALL, 1.2))

    assert _found_ids(job) == [included.pk]


def test_kinds_match_through_any_active_provider():
    job = _job()
    point = point_north_of(BOROUGH_HALL, 0.3)
    only_missing_match = make_located_competitor(
        point, kind=ProviderKind.DAY_CARE_CENTER, missing_since=timezone.now()
    )
    make_provider(
        competitor=only_missing_match, kind=ProviderKind.FAMILY_DAY_CARE, location=point
    )
    one_of_several = make_located_competitor(point, kind=ProviderKind.FAMILY_DAY_CARE)
    make_provider(
        competitor=one_of_several,
        kind=ProviderKind.SCHOOL_BASED_CHILD_CARE,
        location=point,
    )
    make_located_competitor(point, kind=ProviderKind.FAMILY_DAY_CARE)

    assert _found_ids(job) == [one_of_several.pk]


def test_the_own_competitor_is_never_a_result_even_after_a_merge():
    own = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    receiving = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    other = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    job = _job(own_competitor=own)
    assert _found_ids(job) == [receiving.pk, other.pk]

    _retire(own, receiving)

    assert _found_ids(job) == [other.pk]


def test_results_cross_borough_lines():
    job = _job()
    manhattan = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.6), borough=Borough.MANHATTAN
    )

    assert _found_ids(job) == [manhattan.pk]


def test_results_are_ordered_by_distance_then_competitor_number():
    job = _job()
    far = make_located_competitor(point_north_of(BOROUGH_HALL, 0.6))
    first_twin = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    second_twin = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    near = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))

    assert _found_ids(job) == [near.pk, first_twin.pk, second_twin.pk, far.pk]


def test_preselection_takes_the_nearest_n_skipping_reported_closures():
    job = _job(preselected_competitor_count=2)
    nearest = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    closed = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    _report_closure(closed, OperatingStatus.TEMPORARILY_CLOSED, date(2026, 9, 1))
    third = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    fourth = make_located_competitor(point_north_of(BOROUGH_HALL, 0.5))

    page = _page(job)

    assert {row.competitor.pk: row.is_selected for row in page.results} == {
        nearest.pk: True,
        closed.pk: False,
        third.pk: True,
        fourth.pk: False,
    }


def test_a_new_search_keeps_the_selection_and_leaves_new_results_unticked():
    job = _job(search_radius_miles="0.50")
    selected = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    outside = make_located_competitor(point_north_of(BOROUGH_HALL, 0.8))
    for competitor in (selected, outside):
        JobCompetitor.objects.create(job=job, competitor=competitor)
    new = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))

    page = _page(job)

    assert [(row.competitor.pk, row.is_selected) for row in page.results] == [
        (selected.pk, True),
        (new.pk, False),
    ]
    assert [(row.competitor.pk, row.is_selected) for row in page.kept_selections] == [
        (outside.pk, True)
    ]


def test_saving_the_selection_stores_distances_and_removes_deselected_rows():
    job = _job()
    added = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    removed = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    JobCompetitor.objects.create(job=job, competitor=removed)

    changes = search.save_selection(
        job=job,
        displayed_competitor_ids={added.pk, removed.pk},
        selected_competitor_ids={added.pk},
        added_competitor=None,
        chains=MergeChains.load(),
    )

    assert changes == search.SelectionChanges(added=1, removed=1)
    row = JobCompetitor.objects.get(job=job)
    assert row.competitor == added
    assert row.distance_meters == pytest.approx(0.2 * METERS_PER_MILE, rel=0.01)


def test_setting_the_own_competitor_hides_its_row_instead_of_deleting_it():
    job = _job()
    target = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    row = JobCompetitor.objects.create(job=job, competitor=target)
    job.own_competitor = target
    job.save(update_fields=("own_competitor",))

    changes = search.save_selection(
        job=job,
        displayed_competitor_ids={target.pk},
        selected_competitor_ids=set(),
        added_competitor=None,
        chains=MergeChains.load(),
    )

    assert changes == search.SelectionChanges(added=0, removed=0)
    assert JobCompetitor.objects.filter(pk=row.pk).exists()
    assert load_displayed_job_competitors(job, MergeChains.load()) == []


def test_add_by_name_offers_only_active_competitors_other_than_the_own_one(
    admin_client,
):
    addable = make_competitor(display_name="Synthetic Addable Day Care")
    inactive = make_competitor(display_name="Synthetic Addable Closed", is_active=False)
    retired = make_competitor(display_name="Synthetic Addable Merged")
    _retire(retired, addable)
    own = make_competitor(display_name="Synthetic Addable Own")
    job = _job(own_competitor=own)

    response = admin_client.get(
        reverse("admin:research_researchjob_competitor_autocomplete", args=(job.pk,)),
        {
            "app_label": "research",
            "model_name": "jobcompetitor",
            "field_name": "competitor",
            "term": "Addable",
        },
    )

    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == [str(addable.pk)]

    def save_with(added_competitor):
        return search.save_selection(
            job=job,
            displayed_competitor_ids=set(),
            selected_competitor_ids=set(),
            added_competitor=added_competitor,
            chains=MergeChains.load(),
        )

    save_with(addable)
    assert (
        JobCompetitor.objects.get(job=job, competitor=addable).distance_meters is None
    )
    with pytest.raises(ResearchJobError, match="already selected"):
        save_with(addable)
    with pytest.raises(ResearchJobError, match="own competitor"):
        save_with(own)
    with pytest.raises(ResearchJobError, match="Only an active competitor"):
        save_with(inactive)


def _labels_case(case):
    """Return a competitor built for the case and the label text it must show."""
    point = point_north_of(BOROUGH_HALL, 0.2)
    if case == "school-based":
        competitor = make_located_competitor(
            point, kind=ProviderKind.SCHOOL_BASED_CHILD_CARE
        )
        Competitor.objects.filter(pk=competitor.pk).update(is_school_based=True)
        return Competitor.objects.get(pk=competitor.pk), "School-based"
    if case == "capacity complete":
        competitor = make_located_competitor(point, total_capacity=30)
        make_provider(competitor=competitor, total_capacity=20)
        return competitor, "Licensed capacity: 50"
    if case == "capacity incomplete":
        competitor = make_located_competitor(point, total_capacity=30)
        make_provider(competitor=competitor, total_capacity=None)
        return competitor, "Licensed capacity: 30 (incomplete)"
    if case == "capacity unpublished":
        return make_located_competitor(point), "Licensed capacity: Unpublished"
    if case == "ages from NYC Health":
        competitor = make_located_competitor(
            point,
            nyc_health_program_type="INFANT TODDLER",
            nyc_health_child_age_range="0 YEARS - 2 YEARS",
        )
        return competitor, "Ages served: INFANT TODDLER, 0 YEARS - 2 YEARS"
    if case == "ages from NY State":
        competitor = make_located_competitor(
            point,
            source=ProviderSource.NY_STATE,
            kind=ProviderKind.GROUP_FAMILY_DAY_CARE,
            ny_state_infant_capacity=4,
            ny_state_toddler_capacity=0,
            ny_state_preschool_capacity=6,
            ny_state_facility_status="License",
        )
        return competitor, "Ages served: Infant; Preschool"
    if case == "pending review":
        competitor = make_located_competitor(point)
        other = make_located_competitor(point)
        flag = ReviewFlag.objects.create(
            reason=ReviewFlagReason.SIMILAR_NAME,
            provider=other.providers.get(),
            flagged_competitor=other,
            proposed_competitor=competitor,
        )
        return competitor, reverse("admin:providers_reviewflag_change", args=(flag.pk,))
    if case == "unusual facility status":
        competitor = make_located_competitor(
            point, source=ProviderSource.NY_STATE, ny_state_facility_status="Suspended"
        )
        return competitor, "<li>Suspended</li>"
    if case == "blank facility status":
        competitor = make_located_competitor(
            point, source=ProviderSource.NY_STATE, ny_state_facility_status=""
        )
        return competitor, "Status not published"
    if case == "temporary closure":
        competitor = make_located_competitor(point)
        _report_closure(
            competitor, OperatingStatus.TEMPORARILY_CLOSED, date(2026, 9, 1)
        )
        return competitor, "Reported temporarily closed on 2026-09-01"
    if case == "permanent closure":
        competitor = make_located_competitor(point)
        _report_closure(
            competitor, OperatingStatus.PERMANENTLY_CLOSED, date(2026, 9, 2)
        )
        return competitor, "Reported permanently closed on 2026-09-02"
    competitor = make_located_competitor(point, missing_since=timezone.now())
    Competitor.objects.filter(pk=competitor.pk).update(is_active=False)
    return Competitor.objects.get(pk=competitor.pk), "No longer listed by its regulator"


@pytest.mark.parametrize(
    "case",
    [
        "school-based",
        "capacity complete",
        "capacity incomplete",
        "capacity unpublished",
        "ages from NYC Health",
        "ages from NY State",
        "pending review",
        "unusual facility status",
        "blank facility status",
        "temporary closure",
        "permanent closure",
        "no longer listed",
    ],
)
def test_labels(case):
    competitor, expected_text = _labels_case(case)

    labels = load_competitor_labels([competitor], MergeChains.load())[competitor.pk]
    rendered = render_to_string(
        "admin/research/includes/competitor_labels.html", {"labels": labels}
    )

    assert expected_text in rendered


def test_merged_job_competitors_show_once_as_the_receiving_competitor_with_a_live_distance():  # noqa: E501
    job = _job()
    merged = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    receiving = make_located_competitor(point_north_of(BOROUGH_HALL, 0.6))
    JobCompetitor.objects.create(
        job=job, competitor=merged, distance_meters=0.2 * METERS_PER_MILE
    )
    _retire(merged, receiving)

    [displayed] = load_displayed_job_competitors(job, MergeChains.load())
    assert displayed.competitor == receiving
    assert displayed.distance_meters == pytest.approx(0.6 * METERS_PER_MILE, rel=0.01)

    own_row = JobCompetitor.objects.create(
        job=job, competitor=receiving, distance_meters=1000.0
    )

    [displayed] = load_displayed_job_competitors(job, MergeChains.load())
    assert displayed.row == own_row
    assert displayed.distance_meters == 1000.0


def _add_listed_competitors(job, count, *, select):
    for number in range(count):
        competitor = make_located_competitor(
            point_north_of(BOROUGH_HALL, 0.1 + 0.05 * number),
            name=f"Synthetic Day Care {number}",
            total_capacity=20,
        )
        make_provider(competitor=competitor, kind=ProviderKind.FAMILY_DAY_CARE)
        other = make_located_competitor(point_north_of(BOROUGH_HALL, 0.95))
        ReviewFlag.objects.create(
            reason=ReviewFlagReason.SIMILAR_NAME,
            provider=other.providers.get(),
            flagged_competitor=other,
            proposed_competitor=competitor,
        )
        _report_closure(competitor, OperatingStatus.OPERATING, date(2026, 9, 1))
        if select:
            JobCompetitor.objects.create(job=job, competitor=competitor)


def test_the_search_page_runs_a_fixed_number_of_queries(
    admin_client, django_assert_num_queries
):
    job = _job()
    url = reverse("admin:research_researchjob_search", args=(job.pk,))
    _add_listed_competitors(job, 2, select=False)
    # A first request fills the caches every later request shares.
    admin_client.get(url)
    with CaptureQueriesContext(connection) as small_page:
        assert admin_client.get(url).status_code == 200

    _add_listed_competitors(job, 10, select=False)

    with django_assert_num_queries(len(small_page.captured_queries)):
        assert admin_client.get(url).status_code == 200


def test_the_job_page_runs_a_fixed_number_of_queries(
    admin_client, django_assert_num_queries
):
    job = _job(status=JobStatus.RESEARCHING)
    url = reverse("admin:research_researchjob_change", args=(job.pk,))
    _add_listed_competitors(job, 2, select=True)
    admin_client.get(url)
    with CaptureQueriesContext(connection) as small_page:
        assert admin_client.get(url).status_code == 200

    _add_listed_competitors(job, 10, select=True)

    with django_assert_num_queries(len(small_page.captured_queries)):
        assert admin_client.get(url).status_code == 200


def _search_url(job):
    return reverse("admin:research_researchjob_search", args=(job.pk,))


def test_carried_over_competitors_are_ticked_with_the_nearest_ones():
    job = _job(preselected_competitor_count=1)
    nearest = make_located_competitor(point_north_of(BOROUGH_HALL, 0.1))
    beyond_the_count = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    unticked = make_located_competitor(point_north_of(BOROUGH_HALL, 0.5))
    outside_the_radius = make_located_competitor(point_north_of(BOROUGH_HALL, 1.5))

    page = search.build_search_page(
        job,
        MergeChains.load(),
        carried_over_ids=lambda: {beyond_the_count.pk, outside_the_radius.pk},
    )

    assert [(row.competitor.pk, row.is_selected) for row in page.results] == [
        (nearest.pk, True),
        (beyond_the_count.pk, True),
        (unticked.pk, False),
    ]
    (kept,) = page.kept_selections
    assert kept.competitor == outside_the_radius
    assert kept.is_selected
    assert kept.distance_meters == pytest.approx(1.5 * METERS_PER_MILE, rel=0.01)


def test_carried_over_competitors_are_not_read_once_a_selection_exists():
    job = _job()
    selected = make_located_competitor(point_north_of(BOROUGH_HALL, 0.1))
    JobCompetitor.objects.create(job=job, competitor=selected)
    calls = []

    def carried_over_ids():
        calls.append(job.pk)
        return {selected.pk}

    page = search.build_search_page(
        job, MergeChains.load(), carried_over_ids=carried_over_ids
    )

    assert calls == []
    assert [row.competitor for row in page.results if row.is_selected] == [selected]


def test_the_search_page_saves_the_posted_selection_and_the_added_competitor(
    admin_client,
):
    job = _job()
    kept = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    dropped = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    ticked = make_located_competitor(point_north_of(BOROUGH_HALL, 0.4))
    for competitor in (kept, dropped):
        JobCompetitor.objects.create(job=job, competitor=competitor)
    added = make_competitor(display_name="Synthetic Unlocated Day Care")
    make_provider(competitor=added)

    response = admin_client.post(
        _search_url(job),
        {"selected": [kept.pk, ticked.pk], "added_competitor": added.pk},
        follow=True,
    )

    distances = dict(
        JobCompetitor.objects.filter(job=job).values_list(
            "competitor_id", "distance_meters"
        )
    )
    assert response.redirect_chain == [(_search_url(job), 302)]
    assert "Selection saved: 2 added, 1 removed." in response.text
    assert set(distances) == {kept.pk, ticked.pk, added.pk}
    assert distances[added.pk] is None


def test_the_target_button_sets_the_own_competitor_and_hides_its_row(admin_client):
    job = _job()
    target = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    other = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    JobCompetitor.objects.create(job=job, competitor=target)

    response = admin_client.post(
        _search_url(job),
        {"selected": [target.pk, other.pk], "own_competitor_from_results": target.pk},
    )

    job.refresh_from_db()
    assert response.status_code == 302
    assert job.own_competitor == target
    assert JobCompetitor.objects.filter(job=job, competitor=target).exists()
    assert [
        item.competitor
        for item in load_displayed_job_competitors(job, MergeChains.load())
    ] == [other]


def test_a_refused_save_changes_nothing_and_keeps_the_posted_ticks(admin_client):
    job = _job()
    selected = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    newly_ticked = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
    JobCompetitor.objects.create(job=job, competitor=selected)

    response = admin_client.post(
        _search_url(job),
        {
            "selected": [selected.pk, newly_ticked.pk],
            "own_competitor_from_results": newly_ticked.pk,
            "added_competitor": selected.pk,
        },
    )

    job.refresh_from_db()
    assert response.status_code == 200
    assert "The competitor is already selected." in response.text
    assert job.own_competitor is None
    assert list(
        JobCompetitor.objects.filter(job=job).values_list("competitor_id", flat=True)
    ) == [selected.pk]
    assert {
        row.competitor.pk: row.is_selected for row in response.context["results"]
    } == {selected.pk: True, newly_ticked.pk: True}


@pytest.mark.parametrize(
    ("status", "confirmed", "message"),
    [
        (
            JobStatus.CANCELLED,
            True,
            "The search is available while the job is Draft or Researching.",
        ),
        (JobStatus.DRAFT, False, "Confirm the target point first."),
    ],
    ids=["cancelled", "unconfirmed"],
)
def test_the_search_page_needs_an_open_job_with_a_confirmed_point(
    admin_client, status, confirmed, message
):
    job = make_job(
        researcher=make_researcher(),
        point=BOROUGH_HALL,
        confirmed=confirmed,
        status=status,
    )

    response = admin_client.get(_search_url(job), follow=True)

    assert response.redirect_chain == [
        (reverse("admin:research_researchjob_change", args=(job.pk,)), 302)
    ]
    assert message in response.text


def test_enter_on_the_search_page_saves_instead_of_setting_the_target(admin_client):
    job = _job()
    make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))

    html = admin_client.get(_search_url(job)).text
    form = html[html.index('<form method="post" action="" novalidate>') :]
    first_submit = re.search(r'<(?:input|button)[^>]*type="submit"[^>]*>', form)

    assert 'name="own_competitor_from_results"' in form
    assert first_submit is not None
    assert "name=" not in first_submit.group(0)
    assert 'value="Save selection"' in first_submit.group(0)
