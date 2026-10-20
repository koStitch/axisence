"""Forms of the research job page, the competitor search, and the retraction page."""

from collections.abc import Collection, Mapping, MutableMapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import AutocompleteSelect
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Q
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from axisence.accounts.models import User
from axisence.providers.models import Competitor, ProviderKind
from axisence.providers.parsing import Coordinates, is_inside_new_york_state
from axisence.research.models import (
    DESCRIPTION_WARNING,
    RESEARCHERS_GROUP_NAME,
    RETRACTION_REASON_MAXIMUM_LENGTH,
    JobCompetitor,
    ResearchJob,
    default_provider_kinds,
)

# django-stubs declares ModelForm generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    ResearchJobFormBase = forms.ModelForm[ResearchJob]
else:
    ResearchJobFormBase = forms.ModelForm


class ResearchJobForm(ResearchJobFormBase):
    """The job page's form. The admin removes the fields a job's status locks."""

    # A declared field does not take the model's default, which a new job needs.
    provider_kinds = forms.TypedMultipleChoiceField(
        choices=ProviderKind.choices,
        coerce=str,
        initial=default_provider_kinds,
        widget=forms.CheckboxSelectMultiple,
    )
    confirm_target_point = forms.BooleanField(
        required=False,
        label="Confirm the target point",
        help_text=(
            "Tick after checking the pin on the map. Changing the address or the pin "
            "clears the confirmation."
        ),
    )

    class Meta:
        model = ResearchJob
        fields = (
            "purpose",
            "organization_name",
            "assigned_researcher",
            "own_competitor",
            "target_address",
            "target_point",
            "preselected_competitor_count",
            "search_radius_miles",
            "provider_kinds",
        )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        files: MultiValueDict[str, UploadedFile[Any]] | None = None,
        *,
        initial: MutableMapping[str, Any] | None = None,
        instance: ResearchJob | None = None,
    ) -> None:
        # The admin passes only these arguments.
        super().__init__(data, files, initial=initial, instance=instance)
        confirm_target_point = self.fields.get("confirm_target_point")
        if confirm_target_point is not None:
            confirm_target_point.initial = self.instance.is_target_point_confirmed
        assigned_researcher = self.fields.get("assigned_researcher")
        if isinstance(assigned_researcher, forms.ModelChoiceField):
            # Not limit_choices_to, which would also refuse a stored assignee who
            # has since left the group.
            assigned_researcher.queryset = (
                User.objects.filter(
                    Q(groups__name=RESEARCHERS_GROUP_NAME)
                    | Q(is_superuser=True)
                    | Q(pk=self.instance.assigned_researcher_id)
                )
                .distinct()
                .order_by("username")
            )

    def clean_target_point(self) -> Point | None:
        point: Point | None = self.cleaned_data.get("target_point")
        if point is not None and not is_inside_new_york_state(
            Coordinates(longitude=Decimal(str(point.x)), latitude=Decimal(str(point.y)))
        ):
            raise forms.ValidationError("The target must lie in New York State.")
        return point

    def clean_own_competitor(self) -> Competitor | None:
        competitor: Competitor | None = self.cleaned_data.get("own_competitor")
        if (
            competitor is not None
            and competitor.retired_at is not None
            and competitor.pk != self.instance.own_competitor_id
        ):
            raise forms.ValidationError(
                "Choose the current competitor; this one was merged into another."
            )
        return competitor

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if (
            self.cleaned_data.get("confirm_target_point")
            and self.cleaned_data.get("target_point") is None
            and "target_point" not in self.errors
            and "target_address" not in self.changed_data
        ):
            self.add_error(
                "confirm_target_point", "Place the pin before confirming it."
            )
        return cleaned


class JobCompetitorAutocompleteSelect(AutocompleteSelect):
    """The add-by-name widget, asking the job's own suggestions view."""

    def __init__(self, *, job_id: int) -> None:
        super().__init__(JobCompetitor._meta.get_field("competitor"), admin.site)
        self.job_id = job_id

    def get_url(self) -> str:
        return reverse(
            "admin:research_researchjob_competitor_autocomplete", args=(self.job_id,)
        )


class CompetitorSelectionForm(forms.Form):
    """The search page's selection, its add-by-name field, and its target buttons.

    The template renders the selection checkboxes itself, named "selected".
    """

    def __init__(
        self,
        data: Mapping[str, Any] | None,
        *,
        job_id: int,
        displayed_competitor_ids: Collection[int],
        result_ids: Sequence[int],
    ) -> None:
        super().__init__(data)
        self.fields["selected"] = forms.TypedMultipleChoiceField(
            choices=[
                (competitor_id, str(competitor_id))
                for competitor_id in sorted(displayed_competitor_ids)
            ],
            coerce=int,
            required=False,
        )
        self.fields["added_competitor"] = forms.ModelChoiceField(
            queryset=Competitor.objects.filter(is_active=True),
            required=False,
            label="Add a competitor by name",
            widget=JobCompetitorAutocompleteSelect(job_id=job_id),
            help_text=(
                "For a competitor outside the results, of another kind, or without a "
                "location."
            ),
        )
        self.fields["own_competitor_from_results"] = forms.TypedChoiceField(
            choices=[
                ("", ""),
                *((competitor_id, str(competitor_id)) for competitor_id in result_ids),
            ],
            coerce=int,
            empty_value=None,
            required=False,
        )


class RetractionForm(forms.Form):
    reason = forms.CharField(
        max_length=RETRACTION_REASON_MAXIMUM_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=DESCRIPTION_WARNING,
    )
