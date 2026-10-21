"""Turn report and teaser content into HTML and the HTML into a PDF, loading nothing
from anywhere.
"""

import contextlib
from collections.abc import Iterator

from django.template.loader import render_to_string
from weasyprint import HTML
from weasyprint.urls import FatalURLFetchingError, URLFetcher

from axisence.reports.content import ReportContent
from axisence.reports.errors import ExternalResourceRefusedError, NotOneLetterPageError
from axisence.reports.teaser_content import ContactDetails, TeaserContent

# US Letter portrait, 8.5 by 11 inches, in CSS pixels.
LETTER_PORTRAIT_PAGE_SIZE = (816, 1056)


def render_report_html(content: ReportContent, *, number: int) -> str:
    """Return the report's HTML, its stylesheet and map inline."""
    return render_to_string(
        "reports/report.html", {"report": content, "number": number}
    )


def render_teaser_html(
    content: TeaserContent, *, number: int, contact: ContactDetails
) -> str:
    """Return the teaser's HTML, its stylesheet, map, and lock symbol inline."""
    return render_to_string(
        "reports/teaser.html",
        {"teaser": content, "number": number, "contact": contact},
    )


def render_pdf(html: str) -> bytes:
    """Return the PDF of the HTML, refusing every resource outside it."""
    with _refusing_outside_resources():
        pdf: bytes = HTML(string=html, url_fetcher=_refusing_url_fetcher()).write_pdf()
    return pdf


def render_one_letter_page_pdf(html: str) -> bytes:
    """Return the PDF of the HTML when it lays out as exactly one US Letter portrait
    page, refusing every resource outside it.

    The pages are checked on the laid-out document, before any PDF is written; any other
    layout raises NotOneLetterPageError.
    """
    with _refusing_outside_resources():
        document = HTML(string=html, url_fetcher=_refusing_url_fetcher()).render()
        page_sizes = [(page.width, page.height) for page in document.pages]
        if page_sizes != [LETTER_PORTRAIT_PAGE_SIZE]:
            raise NotOneLetterPageError(
                "The document is not exactly one US Letter portrait page."
            )
        pdf: bytes = document.write_pdf()
    return pdf


def _refusing_url_fetcher() -> URLFetcher:
    # Refuses every URL of every scheme, file: and data: included, and stops rendering
    # instead of leaving a resource out.
    return URLFetcher(allowed_protocols=frozenset(), fail_on_errors=True)


@contextlib.contextmanager
def _refusing_outside_resources() -> Iterator[None]:
    # FatalURLFetchingError derives from BaseException, so it is converted here for the
    # generation's except Exception. Images and stylesheets are fetched while laying
    # out, attachments while writing, so both run inside.
    try:
        yield
    except FatalURLFetchingError:
        raise ExternalResourceRefusedError(
            "Rendering loads no resource from outside the report."
        ) from None
