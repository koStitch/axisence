"""The report download inside the admin, behind its login."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpRequest, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from axisence.reports import storage
from axisence.reports.models import Report


def download_report(request: HttpRequest, report_id: int) -> HttpResponseBase:
    """Serve a stored report's or teaser's PDF as an attachment, under its stored file
    name.
    """
    if not request.user.has_perm("reports.view_report"):
        raise PermissionDenied
    report = get_object_or_404(Report, pk=report_id)
    path = storage.report_file_path(report.file_name)
    if not path.is_file():
        messages.error(request, f"The file of report #{report.pk} is no longer stored.")
        return HttpResponseRedirect(_return_url(request))
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=report.file_name,
        content_type="application/pdf",
    )


def _return_url(request: HttpRequest) -> str:
    referer = request.headers.get("referer", "")
    if url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return reverse("admin:reports_report_changelist")
