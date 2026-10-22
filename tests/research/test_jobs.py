"""Tests for the research job lifecycle: lookup, confirmation, settings, and status."""

import itertools
import logging

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from axisence.demographics.models import TabulationArea
from axisence.providers.models import Competitor
from axisence.research import jobs
from axisence.research.geocoding import GEOCODER_ONE_LINE_ADDRESS_URL
from axisence.research.merges import MergeChains
from axisence.research.models import (
    JobCompetitor,
    JobPurpose,
    JobStatus,
    PermanentRecordError,
    ResearchJob,
    ResearchStatus,
    TargetLookupOutcome,
    TargetPointSource,
)
from tests.fixture_files import fixture_bytes
from tests.providers.factories import make_competitor, make_researcher
from tests.research.factories import (
    BOROUGH_HALL,
    METERS_PER_MILE,
    make_job,
    make_located_competitor,
    point_north_of,
)
from tests.research.geocoder_fakes import RECORDED_RESPONSE, one_line_address_body

FIRST_ADDRESS = "100 Example Street, Brooklyn, NY 11217"
SECOND_ADDRESS = "200 Example Street, Brooklyn, NY 11217"
FIRST_MATCH = ("100 EXAMPLE ST, BROOKLYN, NY, 11217", -73.9754, 40.6826)
SECOND_MATCH = ("100 EXAMPLE AVE, BROOKLYN, NY, 11217", -73.9791, 40.6812)

# The transitions requirement R8 allows, written out independently of the code.
REQUIRED_TRANSITIONS = {
    (JobStatus.DRAFT, JobStatus.RESEARCHING),
    (JobStatus.DRAFT, JobStatus.CANCELLED),
    (JobStatus.RESEARCHING, JobStatus.COMPLETE),
    (JobStatus.RESEARCHING, JobStatus.CANCELLED),
    (JobStatus.COMPLETE, JobStatus.RESEARCHING),
    (JobStatus.CANCELLED, JobStatus.RESEARCHING),
}

pytestmark = pytest.mark.django_db


def _answer(fake_transport, address, body=None, *, status=200):
    # Routes match on the address as the query string encodes it.
    fake_transport.add(
        "GET",
        GEOCODER_ONE_LINE_ADDRESS_URL,
        fixture_bytes(RECORDED_RESPONSE) if body is None else body,
        query_contains=address.split(",")[0].replace(" ", "+"),
        status=status,
    )


def _new_job(address=FIRST_ADDRESS):
    return ResearchJob(
        purpose=JobPurpose.PROSPECT,
        organization_name="Synthetic Organization",
        target_address=address,
    )


def _save(job, researcher, *, changed=(), confirm=False, look_up=False, index=None):
    jobs.save_job_changes(
        job=job,
        changed_field_names=changed,
        confirm_target_point=confirm,
        look_up_requested=look_up,
        candidate_index=index,
        researcher=researcher,
    )
    job.refresh_from_db()


def _retire(competitor, receiving):
    Competitor.objects.filter(pk=competitor.pk).update(
        retired_at=timezone.now(), merged_into=receiving, is_active=False
    )


def test_creating_a_job_proposes_the_matched_point_unconfirmed(fake_transport):
    researcher = make_researcher()
    _answer(fake_transport, FIRST_ADDRESS)
    job = _new_job(address=f"  {FIRST_ADDRESS} ")

    jobs.create_job(job=job, researcher=researcher)

    job.refresh_from_db()
    assert job.target_point.x == pytest.approx(BOROUGH_HALL.x)
    assert job.target_point.y == pytest.approx(BOROUGH_HALL.y)
    assert job.target_point_source == TargetPointSource.GEOCODER
    assert job.geocoding_matched_address == "209 JORALEMON ST, BROOKLYN, NY, 11201"
    assert job.geocoding_outcome == TargetLookupOutcome.MATCHED
    assert job.geocoding_request_address == FIRST_ADDRESS
    assert job.geocoding_attempted_at is not None
    assert job.created_by == researcher
    assert job.status == JobStatus.DRAFT
    assert not job.is_target_point_confirmed


def test_several_matches_are_stored_and_using_one_proposes_it(fake_transport):
    researcher = make_researcher()
    _answer(
        fake_transport,
        FIRST_ADDRESS,
        one_line_address_body([FIRST_MATCH, SECOND_MATCH]),
    )
    job = _new_job()
    jobs.create_job(job=job, researcher=researcher)
    job.refresh_from_db()

    assert job.geocoding_outcome == TargetLookupOutcome.SEVERAL_MATCHES
    assert job.target_point is None
    assert job.geocoding_candidates == [
        {
            "matched_address": FIRST_MATCH[0],
            "longitude": "-73.9754",
            "latitude": "40.6826",
        },
        {
            "matched_address": SECOND_MATCH[0],
            "longitude": "-73.9791",
            "latitude": "40.6812",
        },
    ]

    _save(job, researcher, index=1)

    point = ResearchJob.objects.get(pk=job.pk).target_point
    assert point is not None
    assert point.x == pytest.approx(SECOND_MATCH[1])
    assert point.y == pytest.approx(SECOND_MATCH[2])
    assert job.target_point_source == TargetPointSource.GEOCODER
    assert job.geocoding_matched_address == SECOND_MATCH[0]
    assert not job.is_target_point_confirmed
    with pytest.raises(jobs.ResearchJobError, match="no longer listed"):
        _save(job, researcher, index=2)


def test_a_failed_lookup_after_an_address_change_clears_the_point(fake_transport):
    researcher = make_researcher()
    _answer(fake_transport, FIRST_ADDRESS)
    _answer(fake_transport, SECOND_ADDRESS, b"Server error", status=500)
    job = _new_job()
    jobs.create_job(job=job, researcher=researcher)
    job.refresh_from_db()
    job.target_address = SECOND_ADDRESS

    _save(job, researcher, changed=["target_address"])

    assert job.geocoding_outcome == TargetLookupOutcome.REQUEST_FAILED
    assert job.geocoding_request_address == SECOND_ADDRESS
    assert job.target_point is None
    assert job.target_point_source == ""
    assert job.geocoding_matched_address == ""


def test_confirming_records_the_researcher_and_keeps_the_geocoder_source(
    fake_transport,
):
    researcher = make_researcher()
    _answer(fake_transport, FIRST_ADDRESS)
    job = _new_job()
    jobs.create_job(job=job, researcher=researcher)
    job.refresh_from_db()

    _save(job, researcher, confirm=True)

    assert job.target_point_confirmed_by == researcher
    assert job.target_point_confirmed_at is not None
    assert job.target_point_source == TargetPointSource.GEOCODER


@pytest.mark.parametrize(
    "starting_point", [BOROUGH_HALL, None], ids=["moved", "placed"]
)
def test_moving_or_placing_the_pin_makes_the_source_researcher(starting_point):
    researcher = make_researcher()
    job = make_job(researcher=researcher, point=starting_point)
    job.target_point = point_north_of(BOROUGH_HALL, 0.2)

    _save(job, researcher, changed=["target_point"])

    assert job.target_point_source == TargetPointSource.RESEARCHER
    assert job.target_point.y == pytest.approx(point_north_of(BOROUGH_HALL, 0.2).y)


@pytest.mark.parametrize("change", ["address", "point"])
def test_changing_the_address_or_the_point_clears_the_confirmation(
    fake_transport, change
):
    researcher = make_researcher()
    _answer(fake_transport, SECOND_ADDRESS)
    job = make_job(researcher=researcher, point=BOROUGH_HALL, confirmed=True)
    if change == "address":
        job.target_address = SECOND_ADDRESS
        changed = ["target_address"]
    else:
        job.target_point = point_north_of(BOROUGH_HALL, 0.2)
        changed = ["target_point"]

    _save(job, researcher, changed=changed, confirm=change == "address")

    assert not job.is_target_point_confirmed
    assert job.target_point_confirmed_by is None


def test_point_changes_in_draft_recompute_stored_distances():
    researcher = make_researcher()
    competitor_point = point_north_of(BOROUGH_HALL, 0.5)
    competitor = make_located_competitor(competitor_point)
    job = make_job(researcher=researcher, point=BOROUGH_HALL)
    row = JobCompetitor.objects.create(
        job=job, competitor=competitor, distance_meters=0.5 * METERS_PER_MILE
    )

    job.target_point = competitor_point
    _save(job, researcher, changed=["target_point"])
    row.refresh_from_db()
    assert row.distance_meters == pytest.approx(0, abs=1)

    job.target_point = None
    _save(job, researcher, changed=["target_point"])
    row.refresh_from_db()
    assert row.distance_meters is None


def test_target_and_search_settings_change_only_in_draft():
    researcher = make_researcher()
    job = make_job(
        researcher=researcher,
        point=BOROUGH_HALL,
        confirmed=True,
        status=JobStatus.RESEARCHING,
    )

    for arguments in (
        {"changed": ["search_radius_miles"]},
        {"changed": ["target_address"]},
        {"look_up": True},
        {"index": 0},
    ):
        with pytest.raises(jobs.ResearchJobError, match="only while the job is Draft"):
            _save(job, researcher, **arguments)

    job.organization_name = "Renamed Synthetic Organization"
    job.target_point = point_north_of(BOROUGH_HALL, 0.2)
    _save(job, researcher, changed=["organization_name"], confirm=False)

    assert job.organization_name == "Renamed Synthetic Organization"
    assert job.target_point.y == pytest.approx(BOROUGH_HALL.y)
    assert job.is_target_point_confirmed


@pytest.mark.parametrize(
    ("current", "target"),
    list(itertools.product(JobStatus, JobStatus)),
    ids=lambda status: status.value,
)
def test_status_transitions_follow_the_allowed_paths(current, target):
    job = make_job(
        researcher=make_researcher(), point=BOROUGH_HALL, confirmed=True, status=current
    )

    if (current, target) in REQUIRED_TRANSITIONS:
        jobs.change_status(job=job, status=target, chains=MergeChains.load())
        job.refresh_from_db()
        assert job.status == target
    else:
        with pytest.raises(jobs.ResearchJobError, match="cannot become"):
            jobs.change_status(job=job, status=target, chains=MergeChains.load())


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.CANCELLED])
def test_research_and_reopening_need_a_confirmed_point(status):
    job = make_job(researcher=make_researcher(), status=status)

    with pytest.raises(jobs.ResearchJobError, match="Confirm the target point first"):
        jobs.change_status(
            job=job, status=JobStatus.RESEARCHING, chains=MergeChains.load()
        )


@pytest.mark.parametrize(
    ("research_status", "hidden_duplicate", "allowed"),
    [
        (ResearchStatus.TO_DO, False, False),
        (ResearchStatus.IN_PROGRESS, False, False),
        (ResearchStatus.DONE, False, True),
        (ResearchStatus.UNREACHABLE, False, True),
        (ResearchStatus.DONE, True, True),
    ],
    ids=["to do", "in progress", "done", "unreachable", "hidden duplicate"],
)
def test_completion_waits_for_every_displayed_competitor(
    research_status, hidden_duplicate, allowed
):
    job = make_job(
        researcher=make_researcher(),
        point=BOROUGH_HALL,
        confirmed=True,
        status=JobStatus.RESEARCHING,
    )
    competitor = make_located_competitor(point_north_of(BOROUGH_HALL, 0.2))
    JobCompetitor.objects.create(
        job=job, competitor=competitor, research_status=research_status
    )
    if hidden_duplicate:
        # A row of a competitor merged into the listed one is not displayed.
        merged = make_located_competitor(point_north_of(BOROUGH_HALL, 0.3))
        JobCompetitor.objects.create(
            job=job, competitor=merged, research_status=ResearchStatus.TO_DO
        )
        _retire(merged, competitor)

    if allowed:
        jobs.change_status(
            job=job, status=JobStatus.COMPLETE, chains=MergeChains.load()
        )
        job.refresh_from_db()
        assert job.status == JobStatus.COMPLETE
    else:
        with pytest.raises(jobs.ResearchJobError, match="Done or Unreachable"):
            jobs.change_status(
                job=job, status=JobStatus.COMPLETE, chains=MergeChains.load()
            )


def test_jobs_are_never_deleted():
    job = make_job(researcher=make_researcher())

    with pytest.raises(PermanentRecordError):
        job.delete()
    with pytest.raises(PermanentRecordError):
        ResearchJob.objects.filter(pk=job.pk).delete()
    assert ResearchJob.objects.filter(pk=job.pk).exists()


def _confirmed_job(researcher, status, **fields):
    return make_job(
        researcher=researcher,
        point=BOROUGH_HALL,
        confirmed=True,
        status=status,
        **fields,
    )


def _change_status(job, status):
    jobs.change_status(job=job, status=status, chains=MergeChains.load())
    job.refresh_from_db()


def test_a_job_cannot_be_cancelled_while_an_open_repeat_follows_it():
    researcher = make_researcher()
    # Reopened after its repeat was prepared, since a Complete job is never Cancelled.
    predecessor = _confirmed_job(researcher, JobStatus.RESEARCHING)
    repeat = _confirmed_job(researcher, JobStatus.DRAFT, predecessor=predecessor)

    with pytest.raises(jobs.ResearchJobError) as raised:
        _change_status(predecessor, JobStatus.CANCELLED)
    predecessor.refresh_from_db()
    _change_status(repeat, JobStatus.CANCELLED)
    _change_status(predecessor, JobStatus.CANCELLED)

    assert str(raised.value) == (
        f"Research job #{repeat.pk} repeats this job. Cancel that job first."
    )
    assert predecessor.status == JobStatus.CANCELLED


def test_a_cancelled_repeat_cannot_reopen_while_another_open_repeat_exists():
    researcher = make_researcher()
    predecessor = _confirmed_job(researcher, JobStatus.COMPLETE)
    cancelled = _confirmed_job(researcher, JobStatus.CANCELLED, predecessor=predecessor)
    open_repeat = _confirmed_job(researcher, JobStatus.DRAFT, predecessor=predecessor)

    with pytest.raises(jobs.ResearchJobError) as raised:
        _change_status(cancelled, JobStatus.RESEARCHING)
    cancelled.refresh_from_db()
    still_cancelled = cancelled.status
    _change_status(open_repeat, JobStatus.CANCELLED)
    _change_status(cancelled, JobStatus.RESEARCHING)
    predecessor.refresh_from_db()

    assert str(raised.value) == (
        f"Research job #{open_repeat.pk} already repeats research job "
        f"#{predecessor.pk}."
    )
    assert still_cancelled == JobStatus.CANCELLED
    assert cancelled.status == JobStatus.RESEARCHING
    assert predecessor.status == JobStatus.COMPLETE


def test_the_database_allows_one_open_repeat_per_job():
    researcher = make_researcher()
    predecessor = _confirmed_job(researcher, JobStatus.COMPLETE)
    _confirmed_job(researcher, JobStatus.DRAFT, predecessor=predecessor)

    with pytest.raises(IntegrityError), transaction.atomic():
        _confirmed_job(researcher, JobStatus.RESEARCHING, predecessor=predecessor)
    _confirmed_job(researcher, JobStatus.CANCELLED, predecessor=predecessor)

    assert ResearchJob.objects.filter(predecessor=predecessor).count() == 2


def test_a_cancelled_repeat_cannot_reopen_while_its_predecessor_is_cancelled():
    researcher = make_researcher()
    predecessor = _confirmed_job(researcher, JobStatus.COMPLETE)
    repeat = _confirmed_job(researcher, JobStatus.DRAFT, predecessor=predecessor)
    _change_status(repeat, JobStatus.CANCELLED)
    _change_status(predecessor, JobStatus.RESEARCHING)
    _change_status(predecessor, JobStatus.CANCELLED)

    with pytest.raises(jobs.ResearchJobError) as raised:
        _change_status(repeat, JobStatus.RESEARCHING)
    repeat.refresh_from_db()

    assert str(raised.value) == (
        f"Research job #{predecessor.pk} is Cancelled. Reopen that job first."
    )
    assert repeat.status == JobStatus.CANCELLED


@pytest.mark.parametrize("status", list(JobStatus), ids=lambda status: status.value)
def test_organization_purpose_assignee_and_own_competitor_change_in_every_status(
    status,
):
    researcher = make_researcher()
    assignee = make_researcher("assignee")
    own_competitor = make_competitor(display_name="Synthetic Own Day Care")
    job = make_job(
        researcher=researcher, point=BOROUGH_HALL, confirmed=True, status=status
    )
    job.organization_name = "Renamed Synthetic Organization"
    job.purpose = JobPurpose.PROSPECT
    job.assigned_researcher = assignee
    job.own_competitor = own_competitor

    _save(
        job,
        researcher,
        changed=[
            "organization_name",
            "purpose",
            "assigned_researcher",
            "own_competitor",
        ],
        confirm=True,
    )

    assert job.organization_name == "Renamed Synthetic Organization"
    assert job.purpose == JobPurpose.PROSPECT
    assert job.assigned_researcher == assignee
    assert job.own_competitor == own_competitor
    assert job.status == status


def test_the_tabulation_area_is_the_one_containing_the_confirmed_point():
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

    assert jobs.tabulation_area_code(BOROUGH_HALL) == "11201"
    assert jobs.tabulation_area_code(Point(-73.80, 40.75, srid=4326)) is None


def test_target_addresses_never_reach_the_logs(fake_transport, caplog):
    caplog.set_level(logging.DEBUG)
    researcher = make_researcher()
    first_marker = "100 Marker7f3a Street, Brooklyn, NY 11217"
    second_marker = "200 Marker9c1d Street, Brooklyn, NY 11217"
    _answer(fake_transport, first_marker)
    _answer(fake_transport, second_marker, b"Server error", status=500)
    job = _new_job(address=first_marker)

    # Capturing queries turns on the debug cursor, which debug mode logs SQL through.
    with CaptureQueriesContext(connection):
        jobs.create_job(job=job, researcher=researcher)
        job.refresh_from_db()
        job.target_address = second_marker
        _save(job, researcher, changed=["target_address"])

    assert job.geocoding_outcome == TargetLookupOutcome.REQUEST_FAILED
    assert "Research job" in caplog.text
    assert "Marker7f3a" not in caplog.text
    assert "Marker9c1d" not in caplog.text
