"""Admin pages where researchers run research jobs and record what contacts found.

Research jobs are never deleted. Contacts and observations are added only through
the call sheet, and never changed or deleted in the admin: a mistake is retracted from
the contact's page.
"""

from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.forms import OSMWidget
from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from axisence.providers.models import Competitor
from axisence.research import jobs, views
from axisence.research.distances import format_miles
from axisence.research.forms import ResearchJobForm
from axisence.research.job_competitors import (
    DisplayedJobCompetitor,
    load_displayed_job_competitors,
)
from axisence.research.labels import CompetitorLabels, load_competitor_labels
from axisence.research.merges import MergeChains
from axisence.research.models import (
    Contact,
    JobCompetitor,
    JobStatus,
    Observation,
    ResearchJob,
)

# django-stubs declares these classes generic, but they have no runtime
# __class_getitem__, so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    # The stubs' fieldset type, which exists only for the type checker.
    from django.contrib.admin.options import _FieldsetSpec

    ResearchJobAdminBase = GISModelAdmin[ResearchJob]
    JobCompetitorInlineBase = admin.TabularInline[JobCompetitor, ResearchJob]
    JobCompetitorAdminBase = admin.ModelAdmin[JobCompetitor]
    ContactAdminBase = admin.ModelAdmin[Contact]
    ObservationInlineBase = admin.TabularInline[Observation, Contact]
    ObservationAdminBase = admin.ModelAdmin[Observation]
else:
    ResearchJobAdminBase = GISModelAdmin
    JobCompetitorInlineBase = admin.TabularInline
    JobCompetitorAdminBase = admin.ModelAdmin
    ContactAdminBase = admin.ModelAdmin
    ObservationInlineBase = admin.TabularInline
    ObservationAdminBase = admin.ModelAdmin

# Brooklyn, as the starting view of an empty map only.
_EMPTY_MAP_LONGITUDE = -73.9442
_EMPTY_MAP_LATITUDE = 40.6782
_EMPTY_MAP_ZOOM = 12

_ALWAYS_READONLY_JOB_FIELD_NAMES = (
    "status",
    "created_by",
    "created_at",
    "target_point_source",
    "target_point_confirmation",
    "geocoding_request_address",
    "geocoding_matched_address",
    "geocoding_outcome",
    "geocoding_attempted_at",
    "geocoding_candidates_display",
    "tabulation_area",
    "current_own_competitor",
    "target_point_coordinates",
    "provider_kinds_display",
)
_DRAFT_ONLY_JOB_FIELD_NAMES = ("target_address", *jobs.SEARCH_SETTING_FIELD_NAMES)
_JOB_COMPETITOR_DISPLAY_FIELD_NAMES = (
    "competitor_name",
    "street_address",
    "borough",
    "straight_line_distance",
    "labels",
    "call_sheet",
)
_CONTACT_FIELD_NAMES = (
    "competitor",
    "job",
    "contact_date",
    "method",
    "researcher",
    "entered_at",
    "person_role",
    "notes",
    "retracted_at",
    "retracted_by",
    "retraction_reason",
)
_OBSERVATION_FIELD_NAMES = (
    "contact",
    "kind",
    "value_summary",
    "retracted_at",
    "retracted_by",
    "retraction_reason",
)


class TargetPointMapWidget(OSMWidget):
    """The map of a job's target point; an empty map starts over Brooklyn."""

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        # Passed as attributes, because the stubs type the default_* class attributes
        # as whole numbers.
        super().__init__(
            {
                "default_lon": _EMPTY_MAP_LONGITUDE,
                "default_lat": _EMPTY_MAP_LATITUDE,
                "default_zoom": _EMPTY_MAP_ZOOM,
                **(attrs or {}),
            }
        )


class ContactRetractionListFilter(admin.SimpleListFilter):
    title = "retraction"
    parameter_name = "retraction"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return [("current", "Current"), ("retracted", "Retracted")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "current":
            return queryset.filter(retracted_at__isnull=True)
        if self.value() == "retracted":
            return queryset.filter(retracted_at__isnull=False)
        return queryset


class ObservationRetractionListFilter(admin.SimpleListFilter):
    """Current means neither the observation nor its contact is retracted."""

    title = "retraction"
    parameter_name = "retraction"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return [("current", "Current"), ("retracted", "Retracted")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "current":
            return queryset.filter(
                retracted_at__isnull=True, contact__retracted_at__isnull=True
            )
        if self.value() == "retracted":
            return queryset.filter(
                Q(retracted_at__isnull=False) | Q(contact__retracted_at__isnull=False)
            )
        return queryset


class CompetitorHistoryListFilter(admin.SimpleListFilter):
    """A current competitor's observations, its merged competitors' included."""

    title = "competitor"
    parameter_name = "competitor"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        chains = MergeChains.load()
        current_ids = {
            chains.current_id(competitor_id)
            for competitor_id in Contact.objects.order_by()
            .values_list("competitor_id", flat=True)
            .distinct()
        }
        return [
            (str(competitor.pk), str(competitor))
            for competitor in Competitor.objects.filter(pk__in=current_ids).order_by(
                "display_name", "pk"
            )
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        value = self.value()
        if value is None or not value.isdecimal():
            return queryset
        return queryset.filter(
            contact__competitor_id__in=MergeChains.load().member_ids(int(value))
        )


class JobCompetitorInline(JobCompetitorInlineBase):
    """The job's competitors as current competitors, nearest first.

    The job admin supplies the rows, their order, and their labels per request.
    """

    model = JobCompetitor
    fields = (
        "competitor_name",
        "street_address",
        "borough",
        "straight_line_distance",
        "labels",
        "research_status",
        "call_sheet",
    )
    extra = 0
    can_delete = False

    def __init__(
        self, parent_model: type[ResearchJob], admin_site: admin.AdminSite
    ) -> None:
        super().__init__(parent_model, admin_site)
        self.displayed_by_row_id: dict[int, DisplayedJobCompetitor] = {}
        self.labels_by_competitor_id: dict[int, CompetitorLabels] = {}
        self.job: ResearchJob | None = None

    def has_add_permission(
        self, request: HttpRequest, obj: ResearchJob | None = None
    ) -> bool:
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: ResearchJob | None = None
    ) -> tuple[str, ...]:
        # Research statuses change only while the job is Researching.
        if obj is not None and obj.status == JobStatus.RESEARCHING:
            return _JOB_COMPETITOR_DISPLAY_FIELD_NAMES
        return (*_JOB_COMPETITOR_DISPLAY_FIELD_NAMES, "research_status")

    @admin.display(description="Competitor")
    def competitor_name(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        if displayed is None:
            return ""
        competitor = displayed.competitor
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:providers_competitor_change", args=(competitor.pk,)),
            competitor.display_name or str(competitor),
        )

    @admin.display(description="Street address")
    def street_address(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        return "" if displayed is None else displayed.competitor.street_address

    @admin.display(description="Borough")
    def borough(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        return "" if displayed is None else displayed.competitor.get_borough_display()

    @admin.display(description="Straight-line distance")
    def straight_line_distance(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        return format_miles(None if displayed is None else displayed.distance_meters)

    @admin.display(description="Labels")
    def labels(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        if displayed is None:
            return ""
        return render_to_string(
            "admin/research/includes/competitor_labels.html",
            {"labels": self.labels_by_competitor_id[displayed.competitor.pk]},
        )

    @admin.display(description="Call sheet")
    def call_sheet(self, row: JobCompetitor) -> str:
        displayed = self.displayed_by_row_id.get(row.pk)
        if displayed is None:
            return ""
        url = reverse(
            "admin:research_contact_call_sheet", args=(displayed.competitor.pk,)
        )
        if self.job is not None and self.job.status == JobStatus.RESEARCHING:
            url += f"?job={self.job.pk}"
        return format_html('<a href="{}">Call sheet</a>', url)


@admin.register(ResearchJob)
class ResearchJobAdmin(ResearchJobAdminBase):
    form = ResearchJobForm
    gis_widget = TargetPointMapWidget
    inlines = (JobCompetitorInline,)
    autocomplete_fields = ("own_competitor",)
    change_form_template = "admin/research/researchjob/change_form.html"
    list_display = (
        "__str__",
        "organization_name",
        "purpose",
        "status",
        "assigned_researcher",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "purpose", "assigned_researcher")
    search_fields = ("organization_name",)
    list_select_related = ("assigned_researcher", "created_by")

    def get_queryset(self, request: HttpRequest) -> QuerySet[ResearchJob]:
        return (
            super()
            .get_queryset(request)
            .select_related(
                "created_by",
                "assigned_researcher",
                "target_point_confirmed_by",
                "own_competitor",
            )
        )

    def get_inlines(
        self, request: HttpRequest, obj: ResearchJob | None
    ) -> tuple[type[JobCompetitorInline], ...]:
        # A new job has no competitors yet.
        return () if obj is None else self.inlines

    def get_fieldsets(
        self, request: HttpRequest, obj: ResearchJob | None = None
    ) -> _FieldsetSpec:
        if obj is None:
            return (
                (
                    "Job",
                    {
                        "fields": (
                            "purpose",
                            "organization_name",
                            "assigned_researcher",
                        )
                    },
                ),
                ("Target", {"fields": ("target_address",)}),
                ("Search settings", {"fields": jobs.SEARCH_SETTING_FIELD_NAMES}),
            )
        is_draft = obj.status == JobStatus.DRAFT
        return (
            (
                "Job",
                {
                    "fields": (
                        "purpose",
                        "organization_name",
                        "status",
                        "assigned_researcher",
                        "own_competitor",
                        "current_own_competitor",
                        "created_by",
                        "created_at",
                    )
                },
            ),
            (
                "Target",
                {
                    "fields": (
                        "target_address",
                        "geocoding_outcome",
                        "geocoding_matched_address",
                        "geocoding_candidates_display",
                        "target_point" if is_draft else "target_point_coordinates",
                        *(("confirm_target_point",) if is_draft else ()),
                        "target_point_source",
                        "target_point_confirmation",
                        "tabulation_area",
                        "geocoding_request_address",
                        "geocoding_attempted_at",
                    )
                },
            ),
            (
                "Search settings",
                {
                    "fields": jobs.SEARCH_SETTING_FIELD_NAMES
                    if is_draft
                    else (
                        "preselected_competitor_count",
                        "search_radius_miles",
                        "provider_kinds_display",
                    )
                },
            ),
        )

    def get_readonly_fields(
        self, request: HttpRequest, obj: ResearchJob | None = None
    ) -> tuple[str, ...]:
        # ModelAdmin.get_form drops declared form fields named here, so a page that
        # does not show provider_kinds does not require it, and an unshown
        # confirm_target_point never posts False.
        if obj is None:
            return (*_ALWAYS_READONLY_JOB_FIELD_NAMES, "confirm_target_point")
        if obj.status == JobStatus.DRAFT:
            return _ALWAYS_READONLY_JOB_FIELD_NAMES
        return (
            *_ALWAYS_READONLY_JOB_FIELD_NAMES,
            *_DRAFT_ONLY_JOB_FIELD_NAMES,
            "confirm_target_point",
        )

    @admin.display(description="Provider kinds")
    def provider_kinds_display(self, job: ResearchJob) -> str:
        return ", ".join(job.provider_kind_labels())

    @admin.display(description="Target point")
    def target_point_coordinates(self, job: ResearchJob) -> str:
        if job.target_point is None:
            return "No point"
        return f"Longitude {job.target_point.x:.6f}, latitude {job.target_point.y:.6f}"

    @admin.display(description="Confirmation")
    def target_point_confirmation(self, job: ResearchJob) -> str:
        confirmed_by = job.target_point_confirmed_by
        if job.target_point_confirmed_at is None or confirmed_by is None:
            return "Not confirmed"
        confirmed_at = timezone.localtime(job.target_point_confirmed_at)
        return (
            f"Confirmed by {confirmed_by.get_username()} at "
            f"{confirmed_at:%Y-%m-%d %H:%M}"
        )

    @admin.display(description="Geocoder matches")
    def geocoding_candidates_display(self, job: ResearchJob) -> SafeString | None:
        if not job.geocoding_candidates:
            return None
        is_draft = job.status == JobStatus.DRAFT
        return format_html(
            "<ul>{}</ul>",
            format_html_join(
                "",
                "<li>{} (longitude {}, latitude {}) {}</li>",
                (
                    (
                        candidate["matched_address"],
                        candidate["longitude"],
                        candidate["latitude"],
                        format_html(
                            '<button type="submit" name="_use_geocoding_candidate" '
                            'value="{}">Use this match</button>',
                            index,
                        )
                        if is_draft
                        else "",
                    )
                    for index, candidate in enumerate(job.geocoding_candidates)
                ),
            ),
        )

    @admin.display(description="ZIP Code Tabulation Area")
    def tabulation_area(self, job: ResearchJob) -> str:
        if job.target_point is None or not job.is_target_point_confirmed:
            return "Shown once the target point is confirmed"
        code = jobs.tabulation_area_code(job.target_point)
        if code is None:
            return "Outside every ZIP Code Tabulation Area"
        return (
            f"{code}: a ZIP Code Tabulation Area, which approximates but is not the "
            "zip code"
        )

    @admin.display(description="Current own competitor")
    def current_own_competitor(self, job: ResearchJob) -> str:
        if job.own_competitor_id is None:
            return "None"
        current_id = MergeChains.load().current_id(job.own_competitor_id)
        competitor = (
            job.own_competitor
            if current_id == job.own_competitor_id
            else Competitor.objects.get(pk=current_id)
        )
        if competitor is None:
            return "None"
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:providers_competitor_change", args=(competitor.pk,)),
            str(competitor),
        )

    def has_delete_permission(
        self, request: HttpRequest, obj: ResearchJob | None = None
    ) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: ResearchJob,
        form: ResearchJobForm,
        change: bool,
    ) -> None:
        # The services apply the target rules a plain save would skip.
        researcher = views.researcher_from_request(request)
        try:
            if not change:
                jobs.create_job(job=obj, researcher=researcher)
                return
            jobs.save_job_changes(
                job=obj,
                changed_field_names=form.changed_data,
                confirm_target_point=bool(
                    form.cleaned_data.get("confirm_target_point", False)
                ),
                look_up_requested="_look_up_target_address" in request.POST,
                candidate_index=_candidate_index(request),
                researcher=researcher,
            )
        except jobs.ResearchJobError as error:
            self.message_user(request, str(error), messages.ERROR)

    def get_formset_kwargs(
        self,
        request: HttpRequest,
        obj: ResearchJob,
        inline: admin.options.InlineModelAdmin[Any, ResearchJob],
        prefix: str,
    ) -> dict[str, Any]:
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        if not isinstance(inline, JobCompetitorInline) or obj.pk is None:
            return kwargs
        chains = MergeChains.load()
        # A posted change binds the rows the page showed, before the posted own
        # competitor hides any of them.
        shown_job = (
            ResearchJob.objects.get(pk=obj.pk) if request.method == "POST" else obj
        )
        displayed = load_displayed_job_competitors(shown_job, chains)
        inline.job = obj
        inline.displayed_by_row_id = {item.row.pk: item for item in displayed}
        inline.labels_by_competitor_id = load_competitor_labels(
            [item.competitor for item in displayed], chains
        )
        row_ids = [item.row.pk for item in displayed]
        kwargs["queryset"] = (
            JobCompetitor.objects.filter(pk__in=row_ids).order_by(
                Case(
                    *(
                        When(pk=row_id, then=Value(position))
                        for position, row_id in enumerate(row_ids)
                    ),
                    output_field=IntegerField(),
                )
            )
            if row_ids
            else JobCompetitor.objects.none()
        )
        return kwargs

    def response_add(
        self,
        request: HttpRequest,
        obj: ResearchJob,
        post_url_continue: str | None = None,
    ) -> HttpResponse:
        self.message_user(
            request,
            f"Research job #{obj.pk} created. Check the pin on the map.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(_job_url(obj))

    def response_change(self, request: HttpRequest, obj: ResearchJob) -> HttpResponse:
        # Runs after the inline research statuses are saved, so a transition sees
        # them.
        if "_transition" in request.POST:
            try:
                jobs.change_status(
                    job=obj,
                    status=request.POST["_transition"],
                    chains=MergeChains.load(),
                )
            except jobs.ResearchJobError as error:
                self.message_user(request, str(error), messages.ERROR)
            else:
                self.message_user(
                    request,
                    f"Research job #{obj.pk} is now {obj.get_status_display()}.",
                    messages.SUCCESS,
                )
            return HttpResponseRedirect(_job_url(obj))
        if (
            "_look_up_target_address" in request.POST
            or "_use_geocoding_candidate" in request.POST
        ):
            return HttpResponseRedirect(_job_url(obj))
        return super().response_change(request, obj)

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
                available_transitions=jobs.available_transitions(obj),
                is_draft=obj.status == JobStatus.DRAFT,
                search_available=obj.status in (JobStatus.DRAFT, JobStatus.RESEARCHING)
                and obj.is_target_point_confirmed,
                search_url=reverse("admin:research_researchjob_search", args=(obj.pk,)),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "<int:job_id>/search/",
                self.admin_site.admin_view(self.competitor_search_view),
                name="research_researchjob_search",
            ),
            path(
                "<int:job_id>/competitor-autocomplete/",
                self.admin_site.admin_view(
                    views.JobCompetitorAutocompleteView.as_view(
                        admin_site=self.admin_site
                    )
                ),
                name="research_researchjob_competitor_autocomplete",
            ),
            *super().get_urls(),
        ]

    def competitor_search_view(self, request: HttpRequest, job_id: int) -> HttpResponse:
        return views.competitor_search(
            request,
            self,
            job_id,
            carried_over_competitor_ids=self.carried_over_competitor_ids,
        )

    def carried_over_competitor_ids(
        self, job: ResearchJob, chains: MergeChains
    ) -> frozenset[int]:
        """Return the competitors a job's first search ticks besides its nearest ones.

        None here; the reports app's job admin adds a repeat job's earlier competitors.
        """
        return frozenset()


@admin.register(JobCompetitor)
class JobCompetitorAdmin(JobCompetitorAdminBase):
    list_display = (
        "__str__",
        "job",
        "competitor",
        "straight_line_distance",
        "research_status",
    )
    list_filter = ("research_status", ("job", admin.RelatedOnlyFieldListFilter))
    search_fields = (
        "competitor__display_name",
        "=competitor__id",
        "job__organization_name",
    )
    list_select_related = ("job", "competitor")

    @admin.display(description="Straight-line distance", ordering="distance_meters")
    def straight_line_distance(self, row: JobCompetitor) -> str:
        return format_miles(row.distance_meters)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: JobCompetitor | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: JobCompetitor | None = None
    ) -> bool:
        return False


class ObservationInline(ObservationInlineBase):
    """The contact's observations, each with its retraction state or control."""

    model = Observation
    fields = ("kind", "value_summary", "retraction")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def __init__(
        self, parent_model: type[Contact], admin_site: admin.AdminSite
    ) -> None:
        super().__init__(parent_model, admin_site)
        # Set per request by the contact admin.
        self.can_retract = False

    def get_queryset(self, request: HttpRequest) -> QuerySet[Observation]:
        return (
            super()
            .get_queryset(request)
            .select_related("contact", "retracted_by", "age_group", "schedule")
        )

    def has_add_permission(
        self, request: HttpRequest, obj: Contact | None = None
    ) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Contact | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Contact | None = None
    ) -> bool:
        return False

    @admin.display(description="Retraction")
    def retraction(self, observation: Observation) -> str:
        retracted_by = observation.retracted_by
        if observation.retracted_at is not None and retracted_by is not None:
            retracted_on = timezone.localdate(observation.retracted_at).isoformat()
            return (
                f"Retracted on {retracted_on} by {retracted_by.get_username()}: "
                f"{observation.retraction_reason}"
            )
        if observation.contact.is_retracted:
            return "Retracted with its contact"
        if not self.can_retract:
            return ""
        url = reverse("admin:research_contact_retract", args=(observation.contact_id,))
        return format_html(
            '<a href="{}?observation={}">Retract</a>', url, observation.pk
        )


@admin.register(Contact)
class ContactAdmin(ContactAdminBase):
    inlines = (ObservationInline,)
    change_form_template = "admin/research/contact/change_form.html"
    # Notes appear only on the contact's own page and the call sheet, never in a
    # list, a search, or a filter.
    list_display = (
        "__str__",
        "competitor",
        "contact_date",
        "method",
        "researcher",
        "job",
        "person_role",
        "observation_count",
        "is_retracted",
    )
    list_filter = (
        "method",
        ("researcher", admin.RelatedOnlyFieldListFilter),
        ("job", admin.RelatedOnlyFieldListFilter),
        ContactRetractionListFilter,
    )
    search_fields = ("competitor__display_name", "=competitor__id")
    list_select_related = ("competitor", "researcher", "job")
    fields = _CONTACT_FIELD_NAMES
    readonly_fields = _CONTACT_FIELD_NAMES

    def get_queryset(self, request: HttpRequest) -> QuerySet[Contact]:
        return (
            super()
            .get_queryset(request)
            .annotate(observation_count=Count("observations"))
        )

    @admin.display(description="Observations", ordering="observation_count")
    def observation_count(self, contact: Contact) -> int:
        return int(getattr(contact, "observation_count", 0))

    @admin.display(boolean=True, description="Retracted")
    def is_retracted(self, contact: Contact) -> bool:
        return contact.is_retracted

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Contact | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Contact | None = None
    ) -> bool:
        return False

    def get_formset_kwargs(
        self,
        request: HttpRequest,
        obj: Contact,
        inline: admin.options.InlineModelAdmin[Any, Contact],
        prefix: str,
    ) -> dict[str, Any]:
        if isinstance(inline, ObservationInline):
            inline.can_retract = request.user.has_perm("research.retract_contact")
        return super().get_formset_kwargs(request, obj, inline, prefix)

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "call-sheet/<int:competitor_id>/",
                self.admin_site.admin_view(self.call_sheet_view),
                name="research_contact_call_sheet",
            ),
            path(
                "<int:contact_id>/retract/",
                self.admin_site.admin_view(self.retraction_view),
                name="research_contact_retract",
            ),
            *super().get_urls(),
        ]

    def call_sheet_view(self, request: HttpRequest, competitor_id: int) -> HttpResponse:
        return views.call_sheet(request, self, competitor_id)

    def retraction_view(self, request: HttpRequest, contact_id: int) -> HttpResponse:
        return views.retraction(request, self, contact_id)


@admin.register(Observation)
class ObservationAdmin(ObservationAdminBase):
    list_display = (
        "__str__",
        "kind",
        "competitor",
        "contact_date",
        "method",
        "researcher",
        "value_summary",
        "is_retracted",
    )
    list_filter = ("kind", CompetitorHistoryListFilter, ObservationRetractionListFilter)
    search_fields = (
        "contact__competitor__display_name",
        "=contact__competitor__id",
        "name",
    )
    list_select_related = (
        "contact__competitor",
        "contact__researcher",
        "age_group",
        "schedule",
    )
    ordering = ("-contact__contact_date", "-contact__entered_at", "-contact_id", "pk")
    fields = _OBSERVATION_FIELD_NAMES
    readonly_fields = _OBSERVATION_FIELD_NAMES

    @admin.display(description="Competitor", ordering="contact__competitor")
    def competitor(self, observation: Observation) -> str:
        return str(observation.contact.competitor)

    @admin.display(description="Contact date", ordering="contact__contact_date")
    def contact_date(self, observation: Observation) -> str:
        return observation.contact.contact_date.isoformat()

    @admin.display(description="Method", ordering="contact__method")
    def method(self, observation: Observation) -> str:
        return observation.contact.get_method_display()

    @admin.display(description="Researcher", ordering="contact__researcher")
    def researcher(self, observation: Observation) -> str:
        return observation.contact.researcher.get_username()

    @admin.display(boolean=True, description="Retracted")
    def is_retracted(self, observation: Observation) -> bool:
        return observation.is_retracted or observation.contact.is_retracted

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Observation | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Observation | None = None
    ) -> bool:
        return False


def _job_url(job: ResearchJob) -> str:
    return reverse("admin:research_researchjob_change", args=(job.pk,))


def _candidate_index(request: HttpRequest) -> int | None:
    raw_index = request.POST.get("_use_geocoding_candidate")
    if raw_index is None:
        return None
    if not raw_index.isdecimal():
        raise jobs.ResearchJobError("That match is no longer listed.")
    return int(raw_index)
