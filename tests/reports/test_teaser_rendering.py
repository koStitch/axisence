"""Tests that a teaser keeps personal and research data out, links its contact details,
fits one page, and loads nothing from outside.
"""

import logging

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from weasyprint import HTML
from weasyprint.urls import URLFetcher

from axisence.providers.models import (
    Competitor,
    Provider,
    ProviderKind,
    ProviderSource,
    ReviewFlag,
    ReviewFlagReason,
)
from axisence.reports import generation
from axisence.reports.errors import ExternalResourceRefusedError, NotOneLetterPageError
from axisence.reports.rendering import render_one_letter_page_pdf, render_teaser_html
from axisence.reports.teaser_content import build_teaser, load_contact_details
from axisence.research.models import (
    ObservationKind,
    OperatingStatus,
    PersonRole,
    PricePeriod,
)
from tests.providers.factories import make_researcher
from tests.reports.factories import (
    SYNTHETIC_CONTACT_EMAIL,
    SYNTHETIC_CONTACT_PHONE,
    SYNTHETIC_WEBSITE_URL,
    add_candidate,
    add_definitions,
    add_price,
    make_area,
    make_prospect_job,
    make_teaser_ready_job,
)
from tests.research.factories import BOROUGH_HALL, make_contact, make_observation

pytestmark = pytest.mark.django_db

ADDRESS_MARKER = "Synthetic Address Marker 4417"
REQUEST_ADDRESS_MARKER = "Synthetic Request Marker 4418"
MATCHED_ADDRESS_MARKER = "Synthetic Matched Marker 4419"
NOTES_MARKER = "synthetic notes marker 4420"
GENERATING_USER = "synthetic-generating-user-4421"
ASSIGNED_RESEARCHER = "synthetic-assigned-researcher-4422"
CONFIRMING_USER = "synthetic-confirming-user-4423"
PHONE_MARKER = "(718)555-4425"
LICENSEE_MARKER = "Synthetic Licensee Marker 4426"
CLOSED_MARKER = "Synthetic Permanently Closed Marker 4427"
OWN_PROGRAM_MARKER = "Synthetic Own Program Marker 4428"
ORGANIZATION_MARKER = "Synthetic Prospect Marker 4430"

MARKERS = (
    ADDRESS_MARKER,
    REQUEST_ADDRESS_MARKER,
    MATCHED_ADDRESS_MARKER,
    NOTES_MARKER,
    GENERATING_USER,
    ASSIGNED_RESEARCHER,
    CONFIRMING_USER,
    PHONE_MARKER,
    LICENSEE_MARKER,
    CLOSED_MARKER,
    OWN_PROGRAM_MARKER,
    "7,777",
    "7777",
    "Pending review",
    "Front desk",
    "Pending Revocation",
    "Reported temporarily closed",
    "No longer listed",
)
HEADINGS = [
    "Childcare market preview",
    "Nearest competitors",
    "What the full report verifies",
    "Local demographics",
    "Get the full report",
]
LETTER_PAGE_STYLE = "<style>@page { size: letter portrait; }</style>"


def _refusing_fetcher():
    return URLFetcher(allowed_protocols=frozenset(), fail_on_errors=True)


def _teaser_html(job):
    content = build_teaser(job=job, cut_off=timezone.now()).content
    return render_teaser_html(content, number=41, contact=load_contact_details())


def test_personal_and_internal_data_never_reach_the_teaser(caplog, contact_settings):
    confirming_user = make_researcher(CONFIRMING_USER)
    generating_user = make_researcher(GENERATING_USER)
    assigned_researcher = make_researcher(ASSIGNED_RESEARCHER)
    own = add_candidate(0.1, OWN_PROGRAM_MARKER)
    job = make_prospect_job(
        researcher=confirming_user,
        organization_name=ORGANIZATION_MARKER,
        target_address=ADDRESS_MARKER,
        geocoding_request_address=REQUEST_ADDRESS_MARKER,
        geocoding_matched_address=MATCHED_ADDRESS_MARKER,
        assigned_researcher=assigned_researcher,
        own_competitor=own,
    )
    school = add_candidate(
        0.2,
        "Synthetic School Candidate",
        kind=ProviderKind.SCHOOL_BASED_CHILD_CARE,
    )
    Competitor.objects.filter(pk=school.pk).update(is_school_based=True)
    provider = Provider.objects.get(competitor=school)
    Provider.objects.filter(pk=provider.pk).update(
        source=ProviderSource.NY_STATE,
        ny_state_facility_status="Pending Revocation",
        phone_number=PHONE_MARKER,
        ny_state_licensee_name=LICENSEE_MARKER,
    )
    ReviewFlag.objects.create(
        reason=ReviewFlagReason.CHANGED_KEYS,
        provider=provider,
        flagged_competitor=school,
    )
    contact = make_contact(
        competitor=school,
        researcher=generating_user,
        job=job,
        notes=NOTES_MARKER,
        person_role=PersonRole.FRONT_DESK,
    )
    (group,), (schedule,) = add_definitions(
        contact, age_groups=[("Toddlers", 12, 36)], schedules=[("Full time", 5, None)]
    )
    add_price(contact, group, schedule, "7777.77", PricePeriod.MONTHLY)
    make_observation(
        contact=contact,
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.TEMPORARILY_CLOSED,
    )
    closed = add_candidate(0.3, CLOSED_MARKER)
    make_observation(
        contact=make_contact(competitor=closed, researcher=generating_user),
        kind=ObservationKind.OPERATING_STATUS,
        operating_status=OperatingStatus.PERMANENTLY_CLOSED,
    )

    with caplog.at_level(logging.DEBUG):
        teaser = generation.generate_teaser(job=job, generated_by=generating_user)
        html = _teaser_html(job)

    for marker in MARKERS:
        assert marker not in html
    assert ORGANIZATION_MARKER in html
    # The kind label "School-based child care" also holds the words.
    assert html.count('<div class="competitor-label">School-based</div>') == 1
    for header in (
        '<th class="number-column">No.</th>',
        "<th>Competitor</th>",
        '<th class="kind-column">Kind</th>',
        '<th class="distance-column">Distance (straight line)</th>',
        '<th class="capacity-column">Licensed capacity</th>',
    ):
        assert header in html
    assert f"{BOROUGH_HALL.x:.4f}" not in html
    assert f"{BOROUGH_HALL.y:.4f}" not in html
    document = HTML(string=html, url_fetcher=_refusing_fetcher()).render()
    metadata = document.metadata
    assert metadata.title == "Axisence market preview 41"
    assert metadata.lang == "en-US"
    assert metadata.authors == []
    assert metadata.description is None
    assert metadata.keywords == []
    assert metadata.generator is None
    assert metadata.created is None
    assert metadata.modified is None
    assert metadata.custom == {}
    assert [
        label for page in document.pages for _, label, _, _ in page.bookmarks
    ] == HEADINGS
    assert teaser.file_name == f"market-preview-{teaser.pk}.pdf"
    for record in caplog.records:
        message = record.getMessage()
        assert not any(marker in message for marker in (*MARKERS, ORGANIZATION_MARKER))


def test_the_call_to_action_links_and_phone(contact_settings):
    html = _teaser_html(make_teaser_ready_job(researcher=make_researcher()))

    document = HTML(string=html, url_fetcher=_refusing_fetcher()).render()

    links = {(link_type, target) for link_type, target, _, _ in document.pages[0].links}
    assert ("external", SYNTHETIC_WEBSITE_URL) in links
    assert ("external", f"mailto:{SYNTHETIC_CONTACT_EMAIL}") in links
    assert f'<span class="contact-phone">{SYNTHETIC_CONTACT_PHONE}</span>' in html
    assert "tel:" not in html


@pytest.mark.parametrize(
    ("body", "fits"),
    [
        (f"{LETTER_PAGE_STYLE}<p>1</p>", True),
        (f'{LETTER_PAGE_STYLE}<p style="break-after: page">1</p><p>2</p>', False),
        ("<style>@page { size: A4; }</style><p>1</p>", False),
    ],
    ids=["one letter page", "two pages", "A4"],
)
def test_the_one_page_check_accepts_one_letter_page_and_refuses_others(body, fits):
    html = f"<!doctype html><html><head></head><body>{body}</body></html>"

    if fits:
        assert render_one_letter_page_pdf(html).startswith(b"%PDF-")
    else:
        with pytest.raises(NotOneLetterPageError):
            render_one_letter_page_pdf(html)


def test_the_one_page_rendering_refuses_outside_resources():
    html = (
        f"<!doctype html><html><head>{LETTER_PAGE_STYLE}</head><body>"
        '<img src="https://example.com/a.png"><p>x</p></body></html>'
    )

    with pytest.raises(ExternalResourceRefusedError):
        render_one_letter_page_pdf(html)


def test_moved_numbers_are_drawn_with_a_line_to_their_position(contact_settings):
    job = make_prospect_job(researcher=make_researcher())
    add_candidate(0.02, "Synthetic First")
    add_candidate(0.02, "Synthetic Second")
    add_candidate(0.6, "Synthetic Far")

    content = build_teaser(job=job, cut_off=timezone.now()).content
    html = render_teaser_html(content, number=41, contact=load_contact_details())

    # The two at the same spot beside the target move; the far one keeps its spot.
    assert [marker.is_moved for marker in content.market_map.markers] == [
        True,
        True,
        False,
    ]
    assert html.count('<line class="leader"') == 2
    assert html.count('<circle class="anchor"') == 2
    assert html.count('<circle class="marker"') == 3
    # The template draws the marks at the sizes the map kept them apart for.
    assert "font-size: 33.0px;" in html
    assert html.count('<circle class="marker" cx="0" cy="0" r="27.0"/>') == 3
    assert html.count('<text class="marker-number" x="0" y="12.0">') == 3
    assert html.count('r="7.0"/>') == 2
    assert '<rect class="target" x="-20.0" y="-20.0" width="40.0" height="40.0"' in html
    assert '<text class="scale-label" x="0" y="-16.0">' in html
    # Lines and their position dots go over the target, numbers over the lines.
    assert (
        html.index('<rect class="target"')
        < html.index('<line class="leader"')
        < html.index('<circle class="anchor"')
        < html.index('<circle class="marker"')
    )


def test_an_area_code_that_does_not_fit_is_left_off_the_page(contact_settings):
    job = make_prospect_job(researcher=make_researcher())
    add_candidate(0.3)
    make_area("11201", BOROUGH_HALL)
    # A sliver at the map's west edge, about 1.37 miles west of the target, where its
    # code would be cut off.
    make_area(
        "10004",
        Point(BOROUGH_HALL.x - 0.0262, BOROUGH_HALL.y, srid=4326),
        half_size_degrees=0.002,
    )

    content = build_teaser(job=job, cut_off=timezone.now()).content
    html = render_teaser_html(content, number=41, contact=load_contact_details())

    assert {area.code for area in content.market_map.areas} == {"10004", "11201"}
    assert ">11201</text>" in html
    assert ">10004<" not in html
