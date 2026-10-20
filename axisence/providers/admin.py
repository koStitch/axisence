"""Admin pages where researchers review providers, competitors, and review flags.

Every researcher change goes through a service in review.py, never through a plain
model save, so each one is logged as a CompetitorDecision.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, QuerySet
from django.http import HttpRequest

from axisence.accounts.models import User
from axisence.providers import review
from axisence.providers.models import (
    Competitor,
    CompetitorDecision,
    Provider,
    ReviewFlag,
)

# django-stubs declares these classes generic, but they have no runtime
# __class_getitem__, so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    ProviderAdminBase = admin.ModelAdmin[Provider]
    ProviderCompetitorFormBase = forms.ModelForm[Provider]
    ProviderInlineBase = admin.TabularInline[Provider, Competitor]
    CompetitorAdminBase = admin.ModelAdmin[Competitor]
    CompetitorMergeFormBase = forms.ModelForm[Competitor]
    ReviewFlagAdminBase = admin.ModelAdmin[ReviewFlag]
    CompetitorDecisionAdminBase = admin.ModelAdmin[CompetitorDecision]
else:
    ProviderAdminBase = admin.ModelAdmin
    ProviderCompetitorFormBase = forms.ModelForm
    ProviderInlineBase = admin.TabularInline
    CompetitorAdminBase = admin.ModelAdmin
    CompetitorMergeFormBase = forms.ModelForm
    ReviewFlagAdminBase = admin.ModelAdmin
    CompetitorDecisionAdminBase = admin.ModelAdmin

RETIRED_TARGET_MESSAGE = "A retired competitor cannot receive providers."


def _researcher(request: HttpRequest) -> User:
    if isinstance(request.user, User):
        return request.user
    raise PermissionDenied


class ProviderActivityListFilter(admin.SimpleListFilter):
    title = "activity"
    parameter_name = "activity"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return [("active", "Active"), ("missing", "Missing")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "active":
            return queryset.filter(missing_since__isnull=True)
        if self.value() == "missing":
            return queryset.filter(missing_since__isnull=False)
        return queryset


class CompetitorRetirementListFilter(admin.SimpleListFilter):
    title = "retirement"
    parameter_name = "retirement"

    def lookups(
        self, request: HttpRequest, model_admin: ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        return [("current", "Current"), ("retired", "Retired")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "current":
            return queryset.filter(retired_at__isnull=True)
        if self.value() == "retired":
            return queryset.filter(retired_at__isnull=False)
        return queryset


class ProviderCompetitorForm(ProviderCompetitorFormBase):
    """Edits only the provider's competitor, which moves the provider."""

    class Meta:
        model = Provider
        fields = ("competitor",)

    def clean_competitor(self) -> Competitor:
        competitor = self.cleaned_data.get("competitor")
        if not isinstance(competitor, Competitor):
            raise forms.ValidationError("Choose a competitor.")
        if competitor.retired_at is not None:
            raise forms.ValidationError(RETIRED_TARGET_MESSAGE)
        return competitor


@admin.register(Provider)
class ProviderAdmin(ProviderAdminBase):
    form = ProviderCompetitorForm
    list_display = (
        "display_name",
        "source",
        "source_identifier",
        "kind",
        "borough",
        "ny_state_facility_status",
        "total_capacity",
        "competitor",
        "is_active",
        "location_source",
    )
    list_filter = (
        "source",
        "kind",
        "ny_state_facility_status",
        "borough",
        ProviderActivityListFilter,
        "location_source",
    )
    search_fields = (
        "original_name",
        "display_name",
        "source_identifier",
        "street_address",
    )
    list_select_related = ("competitor",)
    autocomplete_fields = ("competitor",)
    readonly_fields = (
        *(
            field.name
            for field in Provider._meta.concrete_fields
            if not field.primary_key and field.name != "competitor"
        ),
        "is_active",
    )
    fieldsets = (
        ("Identity", {"fields": ("source", "source_identifier", "kind")}),
        ("Names", {"fields": ("original_name", "display_name", "name_key")}),
        (
            "Address and contact",
            {
                "fields": (
                    "street_address",
                    "published_zip_code",
                    "zip_key",
                    "address_key",
                    "borough",
                    "phone_number",
                )
            },
        ),
        (
            "Capacity",
            {
                "fields": (
                    "total_capacity",
                    "ny_state_infant_capacity",
                    "ny_state_toddler_capacity",
                    "ny_state_preschool_capacity",
                    "ny_state_school_age_capacity",
                    "ny_state_capacity_description",
                )
            },
        ),
        (
            "NY State",
            {
                "fields": (
                    "ny_state_program_type",
                    "ny_state_region_code",
                    "ny_state_county",
                    "ny_state_facility_status",
                    "ny_state_facility_opened_on",
                    "ny_state_license_issued_on",
                    "ny_state_license_expires_on",
                    "ny_state_is_address_omitted",
                    "ny_state_is_phone_omitted",
                    "ny_state_street_number",
                    "ny_state_street_name",
                    "ny_state_additional_address",
                    "ny_state_floor",
                    "ny_state_apartment",
                    "ny_state_city",
                    "ny_state_state",
                    "ny_state_phone_extension",
                    "ny_state_licensee_name",
                    "ny_state_school_district_name",
                    "ny_state_program_profile_url",
                )
            },
        ),
        (
            "NYC Health",
            {
                "fields": (
                    "nyc_health_permit_number",
                    "nyc_health_facility_type",
                    "nyc_health_program_type",
                    "nyc_health_child_age_range",
                    "nyc_health_administers_medication",
                    "nyc_health_building_identification_number",
                    "nyc_health_borough_block_lot",
                    "nyc_health_community_board",
                    "nyc_health_council_district",
                    "nyc_health_census_tract",
                )
            },
        ),
        (
            "Location",
            {
                "fields": (
                    "location",
                    "location_source",
                    "geocoding_request_address",
                    "geocoding_matched_address",
                    "geocoding_attempted_at",
                    "geocoding_outcome",
                )
            },
        ),
        (
            "Activity",
            {"fields": ("first_seen_at", "last_seen_at", "missing_since", "is_active")},
        ),
        ("Competitor", {"fields": ("competitor",)}),
    )
    actions = ("move_to_new_competitor",)

    @admin.display(boolean=True, description="Active")
    def is_active(self, provider: Provider) -> bool:
        return provider.is_active

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Provider | None = None
    ) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: Provider,
        form: ProviderCompetitorForm,
        change: bool,
    ) -> None:
        # Moving through review.py logs the decision and retires an emptied
        # competitor; a plain save would do neither.
        if "competitor" not in form.changed_data:
            return
        try:
            review.move_provider(
                provider=Provider.objects.select_related("competitor").get(pk=obj.pk),
                target=form.cleaned_data["competitor"],
                researcher=_researcher(request),
            )
        except review.ReviewError as error:
            self.message_user(request, str(error), messages.ERROR)

    @admin.action(
        description="Move the selected providers to a new competitor of their own",
        permissions=("change",),
    )
    def move_to_new_competitor(
        self, request: HttpRequest, queryset: QuerySet[Provider]
    ) -> None:
        researcher = _researcher(request)
        moved_count = 0
        for provider_id in queryset.order_by("pk").values_list("pk", flat=True):
            provider = Provider.objects.select_related("competitor").get(pk=provider_id)
            try:
                review.move_provider(
                    provider=provider, target=None, researcher=researcher
                )
            except review.ReviewError as error:
                self.message_user(request, f"{provider}: {error}", messages.WARNING)
            else:
                moved_count += 1
        if moved_count:
            self.message_user(
                request,
                f"Moved {moved_count} providers to new competitors.",
                messages.SUCCESS,
            )


class ProviderInline(ProviderInlineBase):
    model = Provider
    fields = (
        "display_name",
        "source",
        "source_identifier",
        "kind",
        "street_address",
        "location_source",
    )
    readonly_fields = fields
    show_change_link = True
    extra = 0
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: Competitor | None = None
    ) -> bool:
        return False


class CompetitorMergeForm(CompetitorMergeFormBase):
    """Edits only merged_into, which merges the competitor into the chosen one."""

    class Meta:
        model = Competitor
        fields = ("merged_into",)

    def clean_merged_into(self) -> Competitor | None:
        receiving = self.cleaned_data.get("merged_into")
        if not isinstance(receiving, Competitor):
            return None
        if receiving.pk == self.instance.pk:
            raise forms.ValidationError("A competitor cannot be merged into itself.")
        if receiving.retired_at is not None:
            raise forms.ValidationError(RETIRED_TARGET_MESSAGE)
        return receiving


_COMPETITOR_FIELD_NAMES = (
    "display_name",
    "street_address",
    "borough",
    "location",
    "is_active",
    "is_school_based",
    "created_at",
    "retired_at",
    "merged_into",
)


@admin.register(Competitor)
class CompetitorAdmin(CompetitorAdminBase):
    form = CompetitorMergeForm
    fields = _COMPETITOR_FIELD_NAMES
    list_display = (
        "display_name",
        "street_address",
        "borough",
        "is_active",
        "is_school_based",
        "provider_count",
        "retired_at",
        "merged_into",
    )
    list_filter = (
        "is_active",
        "is_school_based",
        "borough",
        CompetitorRetirementListFilter,
    )
    search_fields = (
        "display_name",
        "street_address",
        "providers__source_identifier",
        "providers__display_name",
    )
    list_select_related = ("merged_into",)
    autocomplete_fields = ("merged_into",)
    inlines = (ProviderInline,)
    # The provider count's GROUP BY drops the model's default ordering, which the
    # autocomplete pagination needs.
    ordering = ("display_name", "pk")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Competitor]:
        # Distinct, because a search on provider fields joins the providers again and
        # would multiply a plain count.
        return (
            super()
            .get_queryset(request)
            .annotate(provider_count=Count("providers", distinct=True))
        )

    @admin.display(description="Providers", ordering="provider_count")
    def provider_count(self, competitor: Competitor) -> int:
        return int(getattr(competitor, "provider_count", 0))

    def get_readonly_fields(
        self, request: HttpRequest, obj: Competitor | None = None
    ) -> tuple[str, ...]:
        if obj is not None and obj.retired_at is not None:
            return _COMPETITOR_FIELD_NAMES
        return tuple(name for name in _COMPETITOR_FIELD_NAMES if name != "merged_into")

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Competitor], search_term: str
    ) -> tuple[QuerySet[Competitor], bool]:
        queryset, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        # Autocomplete never offers a retired competitor. Autocomplete requests name
        # the form field they fill.
        if request.GET.get("field_name"):
            queryset = queryset.filter(retired_at__isnull=True)
        return queryset, may_have_duplicates

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Competitor | None = None
    ) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: Competitor,
        form: CompetitorMergeForm,
        change: bool,
    ) -> None:
        # Saving the form's instance would store merged_into without retired_at and
        # violate competitor_retired_with_receiver, so the merge goes through
        # review.py instead.
        receiving = form.cleaned_data.get("merged_into")
        if "merged_into" not in form.changed_data or receiving is None:
            return
        try:
            review.merge_competitors(
                absorbed=Competitor.objects.get(pk=obj.pk),
                receiving=receiving,
                researcher=_researcher(request),
            )
        except review.ReviewError as error:
            self.message_user(request, str(error), messages.ERROR)


_REVIEW_FLAG_FIELD_NAMES = (
    "reason",
    "state",
    "provider",
    "flagged_competitor",
    "proposed_competitor",
    "raised_by_run",
    "created_at",
    "decision",
    "resolved_by",
    "resolved_at",
)


def _merge_flagged_into_proposed(review_flag: ReviewFlag, researcher: User) -> None:
    if review_flag.proposed_competitor is None:
        raise review.ReviewError("A changed-keys flag proposes no competitor to merge.")
    review.merge_competitors(
        absorbed=review_flag.flagged_competitor,
        receiving=review_flag.proposed_competitor,
        researcher=researcher,
        review_flag=review_flag,
    )


def _declare_different_operators(review_flag: ReviewFlag, researcher: User) -> None:
    review.declare_different_operators(review_flag=review_flag, researcher=researcher)


def _keep_provider(review_flag: ReviewFlag, researcher: User) -> None:
    review.keep_provider(review_flag=review_flag, researcher=researcher)


@admin.register(ReviewFlag)
class ReviewFlagAdmin(ReviewFlagAdminBase):
    list_display = (
        "__str__",
        "reason",
        "state",
        "provider",
        "flagged_competitor",
        "proposed_competitor",
        "created_at",
        "decision",
        "resolved_by",
        "resolved_at",
    )
    list_filter = ("reason", "state", "decision")
    search_fields = (
        "provider__display_name",
        "provider__source_identifier",
        "flagged_competitor__display_name",
        "proposed_competitor__display_name",
    )
    list_select_related = (
        "provider",
        "flagged_competitor",
        "proposed_competitor",
        "resolved_by",
    )
    fields = _REVIEW_FLAG_FIELD_NAMES
    readonly_fields = _REVIEW_FLAG_FIELD_NAMES
    actions = (
        "resolve_as_same_operator",
        "resolve_as_different_operators",
        "resolve_as_provider_stays",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: ReviewFlag | None = None
    ) -> bool:
        return False

    @admin.action(
        description=(
            "Same operator: merge the flagged competitor into the proposed competitor"
        ),
        permissions=("change",),
    )
    def resolve_as_same_operator(
        self, request: HttpRequest, queryset: QuerySet[ReviewFlag]
    ) -> None:
        self._resolve_each(request, queryset, _merge_flagged_into_proposed)

    @admin.action(description="Different operators", permissions=("change",))
    def resolve_as_different_operators(
        self, request: HttpRequest, queryset: QuerySet[ReviewFlag]
    ) -> None:
        self._resolve_each(request, queryset, _declare_different_operators)

    @admin.action(
        description="Provider stays in its competitor", permissions=("change",)
    )
    def resolve_as_provider_stays(
        self, request: HttpRequest, queryset: QuerySet[ReviewFlag]
    ) -> None:
        self._resolve_each(request, queryset, _keep_provider)

    def _resolve_each(
        self,
        request: HttpRequest,
        queryset: QuerySet[ReviewFlag],
        resolve: Callable[[ReviewFlag, User], None],
    ) -> None:
        researcher = _researcher(request)
        resolved_count = 0
        for review_flag_id in queryset.order_by("pk").values_list("pk", flat=True):
            # Reloaded one at a time, because resolving one flag can re-point others.
            review_flag = ReviewFlag.objects.select_related(
                "provider__competitor", "flagged_competitor", "proposed_competitor"
            ).get(pk=review_flag_id)
            try:
                resolve(review_flag, researcher)
            except review.ReviewError as error:
                self.message_user(request, f"{review_flag}: {error}", messages.WARNING)
            else:
                resolved_count += 1
        if resolved_count:
            self.message_user(
                request, f"Resolved {resolved_count} review flags.", messages.SUCCESS
            )


@admin.register(CompetitorDecision)
class CompetitorDecisionAdmin(CompetitorDecisionAdminBase):
    list_display = (
        "__str__",
        "action",
        "provider",
        "from_competitor",
        "to_competitor",
        "researcher",
        "decided_at",
    )
    list_filter = ("action",)
    search_fields = (
        "provider__display_name",
        "provider__source_identifier",
        "from_competitor__display_name",
        "to_competitor__display_name",
    )
    list_select_related = ("provider", "from_competitor", "to_competitor", "researcher")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: CompetitorDecision | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: CompetitorDecision | None = None
    ) -> bool:
        return False
