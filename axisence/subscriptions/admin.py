"""Admin pages for subscriptions, which the superuser adds and changes and researchers
view, and the research job page's repeat additions.

Subscriptions are never deleted. Their list orders and filters by a due state computed
in Python, annotated onto the rows in a fixed number of queries.
"""

import dataclasses
from datetime import date
from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import Case, CharField, DateField, F, QuerySet, Value, When
from django.db.models.expressions import OrderBy
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import URLPattern, path, reverse
from django.utils import timezone

from axisence.reports.admin import ResearchJobWithReportsAdmin
from axisence.reports.models import Report, ReportKind
from axisence.research.models import ResearchJob
from axisence.research.views import researcher_from_request
from axisence.subscriptions import lifecycle, repeats, schedule, views
from axisence.subscriptions.forms import SUBSCRIPTION_FIELD_NAMES, SubscriptionForm
from axisence.subscriptions.models import ServingJob, Subscription
from axisence.subscriptions.schedule import DueState

# django-stubs declares ModelAdmin generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    # The stubs' fieldset type, which exists only for the type checker.
    from django.contrib.admin.options import _FieldsetSpec

    SubscriptionAdminBase = admin.ModelAdmin[Subscription]
else:
    SubscriptionAdminBase = admin.ModelAdmin

_SCHEDULE_FIELD_NAMES = ("next_due_date", "due_state", "created_by", "created_at")
# Read-only once a repeat job serves a due date of the subscription.
_LOCKED_FIELD_NAMES = ("starting_job", "cadence", "start_date")


@dataclasses.dataclass(frozen=True, slots=True)
class DueDateRow:
    due_date: date
    serving_job: ResearchJob | None
    final_report: Report | None


class DueStateListFilter(admin.SimpleListFilter):
    title = "due state"
    parameter_name = "due_state"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return DueState.choices

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        value = self.value()
        if value is None:
            return queryset
        return queryset.filter(due_state_value=value)


@admin.register(Subscription)
class SubscriptionAdmin(SubscriptionAdminBase):
    form = SubscriptionForm
    change_form_template = "admin/subscriptions/subscription/change_form.html"
    autocomplete_fields = ("starting_job",)
    list_display = (
        "__str__",
        "organization",
        "starting_job",
        "cadence",
        "start_date",
        "end_date",
        "next_due_date",
        "due_state",
    )
    list_filter = (DueStateListFilter, "cadence")
    search_fields = ("=starting_job__id",)
    list_select_related = ("starting_job",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Subscription]:
        # Due dates are computed in Python, so every subscription's progress is
        # annotated by number.
        progress = schedule.load_progress(
            list(Subscription.objects.all()), today=timezone.localdate()
        )
        return (
            super()
            .get_queryset(request)
            .select_related("starting_job", "created_by")
            .annotate(
                next_due_on=Case(
                    *(
                        When(pk=pk, then=Value(item.next_due_date))
                        for pk, item in progress.items()
                    ),
                    default=Value(None),
                    output_field=DateField(),
                ),
                due_state_value=Case(
                    *(
                        When(
                            pk=pk,
                            then=Value(
                                "" if item.due_state is None else item.due_state.value
                            ),
                        )
                        for pk, item in progress.items()
                    ),
                    default=Value(""),
                    output_field=CharField(),
                ),
            )
        )

    def get_ordering(self, request: HttpRequest) -> tuple[OrderBy | str, ...]:
        # The annotation exists only in get_queryset, so the ordering is set here.
        return (F("next_due_on").asc(nulls_last=True), "pk")

    @admin.display(
        description="Organization", ordering="starting_job__organization_name"
    )
    def organization(self, subscription: Subscription) -> str:
        return subscription.starting_job.organization_name

    @admin.display(description="Next due date", ordering="next_due_on")
    def next_due_date(self, subscription: Subscription) -> str:
        next_due_on: date | None = getattr(subscription, "next_due_on", None)
        # A read-only method returning None would print "None" on the change page.
        if next_due_on is None:
            return str(self.get_empty_value_display())
        return next_due_on.isoformat()

    @admin.display(description="Due state", empty_value="")
    def due_state(self, subscription: Subscription) -> str:
        value: str = getattr(subscription, "due_state_value", "")
        # Blank rather than the admin's empty value, so the list shows nothing.
        return DueState(value).label if value else ""

    def get_fieldsets(
        self, request: HttpRequest, obj: Subscription | None = None
    ) -> _FieldsetSpec:
        if obj is None:
            return ((None, {"fields": SUBSCRIPTION_FIELD_NAMES}),)
        return (
            ("Subscription", {"fields": SUBSCRIPTION_FIELD_NAMES}),
            ("Schedule", {"fields": _SCHEDULE_FIELD_NAMES}),
        )

    def get_readonly_fields(
        self, request: HttpRequest, obj: Subscription | None = None
    ) -> tuple[str, ...]:
        if obj is None:
            return ()
        if ServingJob.objects.filter(subscription=obj).exists():
            return (*_SCHEDULE_FIELD_NAMES, *_LOCKED_FIELD_NAMES)
        return _SCHEDULE_FIELD_NAMES

    def has_delete_permission(
        self, request: HttpRequest, obj: Subscription | None = None
    ) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: Subscription,
        form: SubscriptionForm,
        change: bool,
    ) -> None:
        # The lifecycle validates again under the locks the form's check took.
        lifecycle.save_subscription(
            obj, user=researcher_from_request(request), today=timezone.localdate()
        )

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Subscription | None = None,
    ) -> HttpResponse:
        if obj is not None and obj.pk is not None:
            today = timezone.localdate()
            progress = schedule.load_progress([obj], today=today)[obj.pk]
            open_serving_jobs = progress.open_serving_jobs
            final_reports: dict[int, Report] = {}
            for report in Report.objects.filter(
                job_id__in=[serving.job_id for serving in open_serving_jobs.values()],
                kind=ReportKind.FINAL,
            ).order_by("job_id", "-pk"):
                final_reports.setdefault(report.job_id, report)
            rows = []
            for due in schedule.listed_due_dates(obj, progress, today):
                serving = open_serving_jobs.get(due)
                rows.append(
                    DueDateRow(
                        due_date=due,
                        serving_job=None if serving is None else serving.job,
                        final_report=None
                        if serving is None
                        else final_reports.get(serving.job_id),
                    )
                )
            context.update(
                due_date_rows=rows,
                can_prepare_repeat_job=progress.is_running
                and views.may_prepare_repeat_jobs(request),
                prepare_url=reverse(
                    "admin:subscriptions_subscription_prepare_repeat_job",
                    args=(obj.pk,),
                ),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "<int:subscription_id>/prepare-repeat-job/",
                self.admin_site.admin_view(self.prepare_repeat_job_view),
                name="subscriptions_subscription_prepare_repeat_job",
            ),
            *super().get_urls(),
        ]

    def prepare_repeat_job_view(
        self, request: HttpRequest, subscription_id: int
    ) -> HttpResponse:
        return views.prepare_repeat_job(request, self, subscription_id)


admin.site.unregister(ResearchJob)


@admin.register(ResearchJob)
class ResearchJobWithSubscriptionsAdmin(ResearchJobWithReportsAdmin):
    """The research job page with its repeat jobs, its subscription, and the Prepare
    repeat job button.

    Registered in place of the reports app's admin, so neither research nor reports
    imports the subscriptions app.
    """

    change_form_template = "admin/subscriptions/research_job_change_form.html"

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
            # The button posts this change form, which needs the change permission.
            context.update(
                predecessor_id=obj.predecessor_id,
                repeated_by=list(
                    ResearchJob.objects.filter(predecessor=obj)
                    .order_by("-pk")
                    .only("pk", "status")
                ),
                serving=ServingJob.objects.filter(job=obj).first(),
                can_prepare_repeat_job=views.may_prepare_repeat_jobs(request)
                and self.has_change_permission(request, obj)
                and repeats.preparation_refusal(obj, today=timezone.localdate())
                is None,
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def response_change(self, request: HttpRequest, obj: ResearchJob) -> HttpResponse:
        # Runs after the page's own changes are saved, so the repeat copies them.
        if "_prepare_repeat_job" not in request.POST:
            return super().response_change(request, obj)
        if not (
            views.may_prepare_repeat_jobs(request)
            and self.has_view_permission(request, obj)
        ):
            raise PermissionDenied
        try:
            prepared = repeats.prepare_repeat_job(
                predecessor=obj,
                user=researcher_from_request(request),
                today=timezone.localdate(),
            )
        except repeats.RepeatRefusedError as error:
            self.message_user(request, str(error), messages.ERROR)
            return HttpResponseRedirect(
                reverse("admin:research_researchjob_change", args=(obj.pk,))
            )
        self.message_user(request, repeats.prepared_message(prepared), messages.SUCCESS)
        return HttpResponseRedirect(
            reverse("admin:research_researchjob_change", args=(prepared.job.pk,))
        )
