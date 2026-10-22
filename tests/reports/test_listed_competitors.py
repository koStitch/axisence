"""Tests for the competitors each draft and final report records, and what repeat jobs
read from them.
"""

import pytest
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone

from axisence.providers.models import Competitor
from axisence.reports import generation, listed_competitors
from axisence.reports.errors import ReportGenerationError, ReportRefusedError
from axisence.reports.models import (
    ListedCompetitor,
    Report,
    ReportKind,
    ReportListing,
)
from axisence.research.merges import MergeChains
from axisence.research.models import (
    JobCompetitor,
    JobStatus,
    ObservationKind,
    OperatingStatus,
    PermanentRecordError,
)
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    add_job_competitor,
    make_listed_report,
    make_ready_job,
    make_report_job,
    make_teaser_ready_job,
    set_contact_settings,
)
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_contact,
    make_located_competitor,
    make_observation,
    point_north_of,
)
from tests.subscriptions.factories import make_repeat_job

pytestmark = pytest.mark.django_db


@pytest.fixture
def researcher():
    return make_researcher()


def _listed(job, miles, name):
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, miles), name=name)
    add_job_competitor(job, competitor, distance_meters=miles * METERS_PER_MILE)
    return competitor


def _retire(competitor, receiver):
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiver, is_active=False
    )


def _report_permanent_closure(competitor, researcher):
    make_observation(
        contact=make_contact(competitor=competitor, researcher=researcher),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.PERMANENTLY_CLOSED,
    )


def _records(report):
    return [
        (row.competitor_id, row.listing, row.number, row.distance_meters)
        for row in ListedCompetitor.objects.filter(report=report)
    ]


def _stored_files(report_directory):
    if not report_directory.exists():
        return []
    return sorted(path.name for path in report_directory.iterdir())


def test_draft_and_final_reports_record_every_listed_competitor(researcher):
    job = make_report_job(researcher=researcher)
    nearest = _listed(job, 0.2, "Synthetic Nearest")
    second = _listed(job, 0.4, "Synthetic Second")
    closed = _listed(job, 0.5, "Synthetic Closed")
    _report_permanent_closure(closed, researcher)
    merged = _listed(job, 0.6, "Synthetic Merged")
    receiving = make_located_competitor(
        point_north_of(BOROUGH_HALL, 0.61), name="Synthetic Receiving"
    )
    _retire(merged, receiving)

    draft = generation.generate_report(job=job, generated_by=researcher)
    job.status = JobStatus.COMPLETE
    job.save()
    final = generation.generate_report(job=job, generated_by=researcher)

    expected = [
        (nearest.pk, ReportListing.MAIN, 1),
        (second.pk, ReportListing.MAIN, 2),
        (receiving.pk, ReportListing.MAIN, 3),
        (closed.pk, ReportListing.REPORTED_CLOSED, None),
    ]
    # The receiving competitor is shown at its own distance, as the report prints it.
    expected_miles = [0.2, 0.4, 0.61, 0.5]
    assert draft.kind == ReportKind.DRAFT
    assert final.kind == ReportKind.FINAL
    for report in (draft, final):
        records = _records(report)
        assert [record[:3] for record in records] == expected
        assert [record[3] / METERS_PER_MILE for record in records] == pytest.approx(
            expected_miles, rel=0.01
        )


def test_teasers_record_no_listed_competitors(researcher, settings):
    set_contact_settings(settings)

    teaser = generation.generate_teaser(
        job=make_teaser_ready_job(researcher=researcher), generated_by=researcher
    )

    assert teaser.compared_report is None
    assert not ListedCompetitor.objects.exists()


def test_a_refused_or_failed_generation_records_none(
    researcher, report_directory, monkeypatch
):
    refused_job = make_report_job(researcher=researcher)
    failing_job = make_ready_job(researcher=researcher)
    original = listed_competitors.store_listed_competitors

    def failing(report, records):
        original(report, records)
        raise RuntimeError("synthetic failure after storing")

    monkeypatch.setattr(listed_competitors, "store_listed_competitors", failing)

    with pytest.raises(ReportRefusedError):
        generation.generate_report(job=refused_job, generated_by=researcher)
    with pytest.raises(ReportGenerationError) as raised:
        generation.generate_report(job=failing_job, generated_by=researcher)

    assert str(raised.value) == "The report could not be generated."
    assert not Report.objects.exists()
    assert not ListedCompetitor.objects.exists()
    assert _stored_files(report_directory) == []


def test_listed_competitors_are_never_changed_or_deleted(researcher):
    job = make_ready_job(researcher=researcher)
    competitor = JobCompetitor.objects.get(job=job).competitor
    report = make_listed_report(job, main=[competitor])
    listed = ListedCompetitor.objects.get()
    listed.number = 2

    with pytest.raises(PermanentRecordError):
        listed.save()
    with pytest.raises(PermanentRecordError):
        listed.delete()
    with pytest.raises(PermanentRecordError):
        ListedCompetitor.objects.update(number=2)
    with pytest.raises(PermanentRecordError):
        ListedCompetitor.objects.all().delete()
    assert ListedCompetitor.objects.get(report=report).number == 1
    assert not admin.site.is_registered(ListedCompetitor)
    # Last, because bulk_update's own transaction marks the test's for rollback.
    with pytest.raises(PermanentRecordError):
        ListedCompetitor.objects.bulk_update([listed], ["number"])


def test_carried_over_ids_follow_the_predecessors_latest_final_report(
    researcher, django_assert_num_queries
):
    predecessor = make_report_job(researcher=researcher, status=JobStatus.COMPLETE)

    def competitor(name, miles=0.3):
        return make_located_competitor(point_north_of(BOROUGH_HALL, miles), name=name)

    older_only = competitor("Synthetic Older Report Only")
    kept = competitor("Synthetic Kept")
    merged = competitor("Synthetic Merged")
    receiving = competitor("Synthetic Receiving")
    _retire(merged, receiving)
    inactive = competitor("Synthetic Inactive")
    Competitor.objects.filter(pk=inactive.pk).update(is_active=False)
    own = competitor("Synthetic Own Program")
    closed = competitor("Synthetic Closed Since")
    _report_permanent_closure(closed, researcher)
    reported_closed_row = competitor("Synthetic Reported Closed Row")
    make_listed_report(predecessor, main=[older_only])
    make_listed_report(
        predecessor,
        main=[kept, merged, inactive, own, closed],
        closed=[reported_closed_row],
    )
    # A final report without listed competitors is never compared with.
    make_listed_report(predecessor)
    repeat_job = make_repeat_job(predecessor, researcher=researcher, own_competitor=own)
    other_job = make_report_job(researcher=researcher)
    chains = MergeChains.load()

    with django_assert_num_queries(0):
        no_predecessor = listed_competitors.carried_over_competitor_ids(
            other_job, chains
        )

    assert listed_competitors.carried_over_competitor_ids(repeat_job, chains) == {
        kept.pk,
        receiving.pk,
    }
    assert no_predecessor == frozenset()


def test_a_repeat_jobs_first_search_ticks_and_keeps_the_earlier_competitors(
    admin_client, researcher
):
    predecessor = make_report_job(researcher=researcher, status=JobStatus.COMPLETE)
    far = make_located_competitor(
        point_north_of(BOROUGH_HALL, 2.0), name="Synthetic Far Competitor"
    )
    make_listed_report(predecessor, main=[far])
    repeat_job = make_repeat_job(
        predecessor, researcher=researcher, status=JobStatus.DRAFT
    )
    url = reverse("admin:research_researchjob_search", args=(repeat_job.pk,))

    first_page = admin_client.get(url)
    saved = admin_client.post(url, {"selected": [far.pk]}, follow=True)
    next_page = admin_client.get(url)

    kept_section = first_page.text[
        first_page.text.index("Selected competitors outside these results") :
    ]
    assert f'value="{far.pk}" aria-label="Select {far}" checked' in kept_section
    assert "Selection saved: 1 added, 0 removed." in saved.text
    assert list(
        JobCompetitor.objects.filter(job=repeat_job).values_list(
            "competitor_id", flat=True
        )
    ) == [far.pk]
    assert [row.competitor for row in next_page.context["kept_selections"]] == [far]
