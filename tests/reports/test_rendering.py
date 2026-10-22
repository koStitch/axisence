"""Tests that rendering keeps personal data out and loads nothing from outside."""

import logging
from decimal import Decimal

import pytest
from django.utils import timezone
from weasyprint import HTML
from weasyprint.urls import URLFetcher

from axisence.providers.models import Provider, ReviewFlag, ReviewFlagReason
from axisence.reports import generation
from axisence.reports.content import build_report
from axisence.reports.errors import ExternalResourceRefusedError
from axisence.reports.models import ReportKind
from axisence.reports.rendering import render_pdf, render_report_html
from axisence.research import contacts
from axisence.research.models import (
    JobCompetitor,
    ObservationKind,
    PersonRole,
    PricePeriod,
)
from axisence.transit.models import TransitMode
from tests.providers.factories import make_researcher
from tests.reports.factories import add_definitions, add_price, make_ready_job
from tests.research.factories import (
    BOROUGH_HALL,
    make_contact,
    make_observation,
    point_north_of,
)
from tests.transit.factories import make_transit_line, make_transit_stop

pytestmark = pytest.mark.django_db

ADDRESS_MARKER = "Synthetic Address Marker 4417"
REQUEST_ADDRESS_MARKER = "Synthetic Request Marker 4418"
MATCHED_ADDRESS_MARKER = "Synthetic Matched Marker 4419"
NOTES_MARKER = "synthetic notes marker 4420"
GENERATING_USER = "synthetic-generating-user-4421"
ASSIGNED_RESEARCHER = "synthetic-assigned-researcher-4422"
CONFIRMING_USER = "synthetic-confirming-user-4423"
RETRACTION_MARKER = "synthetic retraction marker 4424"
PHONE_MARKER = "(718)555-4425"
LICENSEE_MARKER = "Synthetic Licensee Marker 4426"
RETRACTED_AMOUNT = Decimal("7777.77")

MARKERS = (
    ADDRESS_MARKER,
    REQUEST_ADDRESS_MARKER,
    MATCHED_ADDRESS_MARKER,
    NOTES_MARKER,
    GENERATING_USER,
    ASSIGNED_RESEARCHER,
    CONFIRMING_USER,
    RETRACTION_MARKER,
    PHONE_MARKER,
    LICENSEE_MARKER,
    "7,777",
    "7777",
)


def _refusing_fetcher():
    return URLFetcher(allowed_protocols=frozenset(), fail_on_errors=True)


def _report_html(job, kind=ReportKind.DRAFT):
    built = build_report(job=job, kind=kind, cut_off=timezone.now())
    return render_report_html(built.content, number=41)


def test_personal_and_internal_data_never_reach_the_report(caplog):
    confirming_user = make_researcher(CONFIRMING_USER)
    generating_user = make_researcher(GENERATING_USER)
    assigned_researcher = make_researcher(ASSIGNED_RESEARCHER)
    job = make_ready_job(
        researcher=confirming_user,
        target_address=ADDRESS_MARKER,
        geocoding_request_address=REQUEST_ADDRESS_MARKER,
        geocoding_matched_address=MATCHED_ADDRESS_MARKER,
        assigned_researcher=assigned_researcher,
    )
    competitor = JobCompetitor.objects.get(job=job).competitor
    provider = Provider.objects.get(competitor=competitor)
    Provider.objects.filter(pk=provider.pk).update(
        phone_number=PHONE_MARKER, ny_state_licensee_name=LICENSEE_MARKER
    )
    ReviewFlag.objects.create(
        reason=ReviewFlagReason.CHANGED_KEYS,
        provider=provider,
        flagged_competitor=competitor,
    )
    contact = make_contact(
        competitor=competitor,
        researcher=generating_user,
        job=job,
        notes=NOTES_MARKER,
        person_role=PersonRole.FRONT_DESK,
    )
    (group,), (schedule,) = add_definitions(
        contact, age_groups=[("Toddlers", 12, 36)], schedules=[("Full time", 5, None)]
    )
    add_price(contact, group, schedule, "2100", PricePeriod.MONTHLY)
    retracted = make_contact(competitor=competitor, researcher=generating_user, job=job)
    retracted_price = add_price(
        retracted, group, schedule, RETRACTED_AMOUNT, PricePeriod.MONTHLY
    )
    contacts.retract_observation(
        observation=retracted_price,
        researcher=generating_user,
        reason=RETRACTION_MARKER,
    )
    for mode, stop_id, name, line_name in (
        (TransitMode.SUBWAY, "901", "Synthetic Rendering Station", "A"),
        (TransitMode.BUS, "900001", "Synthetic Rendering Bus Stop", "B900"),
    ):
        make_transit_stop(
            mode=mode,
            published_stop_id=stop_id,
            point=point_north_of(BOROUGH_HALL, 0.05),
            name=name,
            lines=[make_transit_line(mode, line_name, 1)],
        )

    with caplog.at_level(logging.DEBUG):
        report = generation.generate_report(job=job, generated_by=generating_user)
        html = _report_html(job)

    for marker in (*MARKERS, "Pending review", "Front desk"):
        assert marker not in html
    assert f"{BOROUGH_HALL.x:.4f}" not in html
    assert f"{BOROUGH_HALL.y:.4f}" not in html
    assert "$2,100 per month" in html
    # The target is named only by its label; its transit lists by name and distance.
    assert "<td>Target location</td>" in html
    assert "Synthetic Rendering Station: A (0.05 mi)" in html
    assert '<span class="bus-entry">B900 (0.05 mi)</span>' in html
    metadata = HTML(string=html, url_fetcher=_refusing_fetcher()).render().metadata
    assert metadata.title == "Axisence competitor analysis, report 41"
    assert not metadata.authors
    assert not metadata.description
    assert not metadata.keywords
    assert metadata.lang == "en-US"
    assert report.file_name == f"report-{report.pk}.pdf"
    for record in caplog.records:
        message = record.getMessage()
        assert not any(marker in message for marker in MARKERS)


@pytest.mark.parametrize(
    "resource",
    [
        '<img src="https://example.com/a.png">',
        '<img src="http://127.0.0.1/a.png">',
        '<img src="file:///etc/hostname">',
        '<img src="data:image/png;base64,iVBORw0KGgo=">',
        '<link rel="stylesheet" href="https://example.com/a.css">',
    ],
    ids=["https", "local http", "file", "data", "stylesheet"],
)
def test_rendering_refuses_every_outside_address(resource):
    html = f"<!doctype html><html><head></head><body>{resource}<p>x</p></body></html>"

    with pytest.raises(ExternalResourceRefusedError):
        render_pdf(html)


def test_rendering_produces_a_letter_pdf():
    researcher = make_researcher()
    html = _report_html(make_ready_job(researcher=researcher))

    pdf = render_pdf(html)
    first_page = HTML(string=html, url_fetcher=_refusing_fetcher()).render().pages[0]

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert (first_page.width, first_page.height) == (816, 1056)


def test_draft_reports_are_marked_on_every_page():
    researcher = make_researcher()
    job = make_ready_job(researcher=researcher)
    cover_mark = '<p class="draft-mark">Draft</p>'

    draft = _report_html(job, ReportKind.DRAFT)
    final = _report_html(job, ReportKind.FINAL)

    assert "@top-right" in draft
    assert 'content: "Draft"' in draft
    assert cover_mark in draft
    assert "@top-right" not in final
    assert cover_mark not in final


def test_free_text_appears_as_recorded_and_escaped():
    researcher = make_researcher()
    job = make_ready_job(researcher=researcher)
    competitor = JobCompetitor.objects.get(job=job).competitor
    contact = make_contact(competitor=competitor, researcher=researcher)
    make_observation(
        contact=contact,
        kind=ObservationKind.DESCRIPTION,
        description="Garden & <b>play</b>",
    )

    html = _report_html(job)

    assert "Garden &amp; &lt;b&gt;play&lt;/b&gt;" in html
    assert "<b>play</b>" not in html


def test_pdf_library_records_never_reach_the_log(caplog):
    html = (
        "<!doctype html><html><head><style>p { synthetic-unknown-property: 1; }"
        "</style></head><body><p>x</p></body></html>"
    )

    with caplog.at_level(logging.DEBUG):
        render_pdf(html)

    assert not [
        record
        for record in caplog.records
        if record.name.startswith(("weasyprint", "fontTools"))
    ]
