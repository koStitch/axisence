"""Turn report content into HTML and the HTML into a PDF, loading nothing from
anywhere.
"""

from django.template.loader import render_to_string
from weasyprint import HTML
from weasyprint.urls import FatalURLFetchingError, URLFetcher

from axisence.reports.content import ReportContent
from axisence.reports.errors import ExternalResourceRefusedError


def render_report_html(content: ReportContent, *, number: int) -> str:
    """Return the report's HTML, its stylesheet and map inline."""
    return render_to_string(
        "reports/report.html", {"report": content, "number": number}
    )


def render_pdf(html: str) -> bytes:
    """Return the PDF of the HTML, refusing every resource outside it."""
    # Refuses every URL of every scheme, file: and data: included, and stops
    # rendering instead of leaving a resource out.
    url_fetcher = URLFetcher(allowed_protocols=frozenset(), fail_on_errors=True)
    try:
        pdf: bytes = HTML(string=html, url_fetcher=url_fetcher).write_pdf()
    # FatalURLFetchingError derives from BaseException, so it is converted here for
    # the generation's except Exception.
    except FatalURLFetchingError:
        raise ExternalResourceRefusedError(
            "Rendering loads no resource from outside the report."
        ) from None
    return pdf
