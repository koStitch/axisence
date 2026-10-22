"""Admin pages for reports and teasers, which are listed and downloaded but never added,
changed, or deleted, the research job page's buttons and list of them, and a repeat
job's first selection.
"""

from typing import TYPE_CHECKING, Any, Protocol

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from axisence.accounts.models import User
from axisence.reports import generation, listed_competitors, views
from axisence.reports.errors import ReportError
from axisence.reports.models import Report
from axisence.research.admin import ResearchJobAdmin
from axisence.research.merges import MergeChains
from axisence.research.models import ResearchJob
from axisence.research.views import researcher_from_request

# django-stubs declares ModelAdmin generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    ReportAdminBase = admin.ModelAdmin[Report]
else:
    ReportAdminBase = admin.ModelAdmin

_REPORT_FIELD_NAMES = (
    "job",
    "kind",
    "compared_report",
    "generated_at",
    "generated_by",
    "census_vintage",
    "tabulation_area_code",
    "ny_state_provider_run",
    "nyc_health_provider_run",
    "transit_run",
    "file_name",
    "size_bytes",
    "sha256",
    "download_link",
)


class _DocumentGenerator(Protocol):
    def __call__(self, *, job: ResearchJob, generated_by: User | None) -> Report: ...


@admin.register(Report)
class ReportAdmin(ReportAdminBase):
    list_display = (
        "__str__",
        "job",
        "kind",
        "generated_at",
        "compared_with",
        "census_vintage",
        "download_link",
    )
    list_filter = ("kind", ("job", admin.RelatedOnlyFieldListFilter))
    search_fields = ("=job__id",)
    list_select_related = ("job",)
    fields = _REPORT_FIELD_NAMES
    readonly_fields = _REPORT_FIELD_NAMES

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Report | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Report | None = None
    ) -> bool:
        return False

    @admin.display(description="Compared with")
    def compared_with(self, report: Report) -> str:
        if report.compared_report_id is None:
            return ""
        return f"Report #{report.compared_report_id}"

    @admin.display(description="File")
    def download_link(self, report: Report) -> SafeString:
        return format_html(
            '<a href="{}">Download</a>',
            reverse("admin:reports_report_download", args=(report.pk,)),
        )

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "<int:report_id>/download/",
                self.admin_site.admin_view(self.download_view),
                name="reports_report_download",
            ),
            *super().get_urls(),
        ]

    def download_view(self, request: HttpRequest, report_id: int) -> HttpResponseBase:
        return views.download_report(request, report_id)


admin.site.unregister(ResearchJob)


@admin.register(ResearchJob)
class ResearchJobWithReportsAdmin(ResearchJobAdmin):
    """The research job page with the report and teaser buttons and the job's reports
    and teasers.

    Registered in place of the research app's admin, so the research app never
    imports the reports app.
    """

    change_form_template = "admin/reports/research_job_change_form.html"

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: ResearchJob | None = None,
    ) -> HttpResponse:
        if obj is not None and obj.pk is not None:
            context.update(
                can_generate_report=generation.is_eligible(obj)
                and request.user.has_perm("reports.generate_report")
                and self.has_view_permission(request, obj),
                can_generate_teaser=generation.is_teaser_eligible(obj)
                and request.user.has_perm("reports.generate_teaser")
                and self.has_view_permission(request, obj),
                job_reports=list(
                    Report.objects.filter(job=obj)
                    .order_by("-pk")
                    .only("pk", "kind", "generated_at", "compared_report")
                ),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def carried_over_competitor_ids(
        self, job: ResearchJob, chains: MergeChains
    ) -> frozenset[int]:
        """Return the competitors a repeat job carries over from its predecessor's
        latest final report.
        """
        return listed_competitors.carried_over_competitor_ids(job, chains)

    def response_change(self, request: HttpRequest, obj: ResearchJob) -> HttpResponse:
        # Runs after the page's own changes are saved, so the document sees them.
        if "_generate_report" in request.POST:
            return self._generate_document(
                request, obj, "reports.generate_report", generation.generate_report
            )
        if "_generate_teaser" in request.POST:
            return self._generate_document(
                request, obj, "reports.generate_teaser", generation.generate_teaser
            )
        return super().response_change(request, obj)

    def _generate_document(
        self,
        request: HttpRequest,
        job: ResearchJob,
        permission: str,
        generate: _DocumentGenerator,
    ) -> HttpResponse:
        if not (
            request.user.has_perm(permission) and self.has_view_permission(request, job)
        ):
            raise PermissionDenied
        try:
            report = generate(job=job, generated_by=researcher_from_request(request))
        except ReportError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(
                request, generation.generated_message(report), messages.SUCCESS
            )
        return HttpResponseRedirect(
            reverse("admin:research_researchjob_change", args=(job.pk,))
        )
