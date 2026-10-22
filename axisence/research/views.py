"""The research pages inside the admin: competitor search, call sheet, retraction.

Each view is wired by a ModelAdmin's get_urls behind the admin login and checks its
own permissions.
"""

import dataclasses
import functools
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from django.contrib import messages
from django.contrib.admin import ModelAdmin
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from axisence.accounts.models import User
from axisence.providers.models import Competitor, Provider
from axisence.research import contacts, jobs, search
from axisence.research.call_sheet import CallSheet
from axisence.research.contacts import RecordingError, RetractionError
from axisence.research.current_values import (
    CurrentValues,
    compute_current_values,
    load_competitor_observations,
)
from axisence.research.distances import format_miles
from axisence.research.forms import CompetitorSelectionForm, RetractionForm
from axisence.research.job_competitors import load_displayed_job_competitors
from axisence.research.jobs import ResearchJobError
from axisence.research.labels import (
    CompetitorLabels,
    ages_served,
    load_competitor_labels,
)
from axisence.research.merges import MergeChains
from axisence.research.models import (
    Contact,
    JobStatus,
    Observation,
    ObservationKind,
    ResearchJob,
)
from axisence.research.search import SearchRow
from axisence.research.value_history import build_value_history

# Current values are listed in the order the observation kinds are declared.
_KIND_ORDER = {kind.value: position for position, kind in enumerate(ObservationKind)}


def researcher_from_request(request: HttpRequest) -> User:
    """Return the signed-in user, who acts as the researcher."""
    if isinstance(request.user, User):
        return request.user
    raise PermissionDenied


class JobCompetitorAutocompleteView(AutocompleteJsonView):
    """Suggests competitors to add to one job: active ones, never its own competitor."""

    def get_queryset(self) -> QuerySet[Competitor]:
        queryset: QuerySet[Competitor] = super().get_queryset()
        job = get_object_or_404(ResearchJob, pk=self.kwargs["job_id"])
        if job.own_competitor_id is not None:
            own_id = MergeChains.load().current_id(job.own_competitor_id)
            queryset = queryset.exclude(pk=own_id)
        return queryset


@dataclasses.dataclass(frozen=True, slots=True)
class SearchTableRow:
    competitor: Competitor
    distance: str
    labels: CompetitorLabels
    is_selected: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderRow:
    provider: Provider
    phone_number: str
    ages_served: tuple[str, ...]
    licensed_capacity: str


@dataclasses.dataclass(frozen=True, slots=True)
class CurrentValueRow:
    slot_label: str
    summaries: tuple[str, ...]
    contact: Contact


def competitor_search(
    request: HttpRequest,
    model_admin: ModelAdmin[Any],
    job_id: int,
    *,
    carried_over_competitor_ids: Callable[[ResearchJob, MergeChains], Collection[int]],
) -> HttpResponse:
    """List the job's candidate competitors, and save which ones it researches.

    A job's first search also ticks the competitors carried_over_competitor_ids
    returns for it.
    """
    if not request.user.has_perm("research.change_researchjob"):
        raise PermissionDenied
    job = get_object_or_404(ResearchJob, pk=job_id)
    job_url = reverse("admin:research_researchjob_change", args=(job.pk,))
    if job.status not in (JobStatus.DRAFT, JobStatus.RESEARCHING):
        messages.error(request, search.SEARCH_UNAVAILABLE_MESSAGE)
        return HttpResponseRedirect(job_url)
    if not job.is_target_point_confirmed:
        messages.error(request, jobs.UNCONFIRMED_POINT_MESSAGE)
        return HttpResponseRedirect(job_url)
    chains = MergeChains.load()
    page = search.build_search_page(
        job,
        chains,
        carried_over_ids=functools.partial(carried_over_competitor_ids, job, chains),
    )
    form = CompetitorSelectionForm(
        request.POST if request.method == "POST" else None,
        job_id=job.pk,
        displayed_competitor_ids=page.displayed_competitor_ids,
        result_ids=[row.competitor.pk for row in page.results],
    )
    if request.method == "POST" and form.is_valid():
        try:
            # The target button and the selection save together or not at all.
            with transaction.atomic():
                own_competitor_id = form.cleaned_data["own_competitor_from_results"]
                if own_competitor_id is not None:
                    jobs.set_own_competitor(
                        job=job, competitor=Competitor.objects.get(pk=own_competitor_id)
                    )
                changes = search.save_selection(
                    job=job,
                    displayed_competitor_ids=page.displayed_competitor_ids,
                    selected_competitor_ids=form.cleaned_data["selected"],
                    added_competitor=form.cleaned_data["added_competitor"],
                    chains=chains,
                )
        except ResearchJobError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                f"Selection saved: {changes.added} added, {changes.removed} removed.",
            )
            return HttpResponseRedirect(request.get_full_path())
    # A refused post shows the ticks as posted, so the researcher's changes survive.
    ticked_ids = (
        frozenset(
            int(value)
            for value in request.POST.getlist("selected")
            if value.isdecimal()
        )
        if request.method == "POST"
        else None
    )
    context = {
        **model_admin.admin_site.each_context(request),
        "opts": ResearchJob._meta,
        "title": f"Search competitors for research job #{job.pk}",
        "job": job,
        "job_url": job_url,
        "results": _search_table_rows(page.results, page.labels, ticked_ids),
        "kept_selections": _search_table_rows(
            page.kept_selections, page.labels, ticked_ids
        ),
        "form": form,
    }
    return TemplateResponse(request, "admin/research/researchjob/search.html", context)


def call_sheet(
    request: HttpRequest, model_admin: ModelAdmin[Any], competitor_id: int
) -> HttpResponse:
    """Show what is known about a competitor, and record one contact with it."""
    if not request.user.has_perm("research.view_contact"):
        raise PermissionDenied
    if request.method == "POST" and not request.user.has_perms(
        ("research.add_contact", "research.add_observation")
    ):
        raise PermissionDenied
    chains = MergeChains.load()
    current_id = chains.current_id(competitor_id)
    if current_id != competitor_id:
        url = reverse("admin:research_contact_call_sheet", args=(current_id,))
        query = request.GET.urlencode()
        return HttpResponseRedirect(f"{url}?{query}" if query else url)
    competitor = get_object_or_404(Competitor, pk=competitor_id)
    linked_job, job_notice = _linked_job(request, competitor, chains)
    # The current values and the history come from one query of observations.
    observations = load_competitor_observations(competitor.pk, chains)
    current_values = compute_current_values(observations)
    sheet = CallSheet(
        competitor=competitor,
        current_values=current_values,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and sheet.is_valid():
        entry = sheet.contact_entry(job=linked_job)
        try:
            contact = contacts.record_contact(
                entry=entry, researcher=researcher_from_request(request)
            )
        except RecordingError as error:
            sheet.errors.append(str(error))
        else:
            messages.success(
                request,
                f"Contact #{contact.pk} recorded with {entry.observation_count} "
                "observations.",
            )
            return HttpResponseRedirect(request.get_full_path())
    providers = sorted(
        competitor.providers.all(),
        key=lambda provider: (
            not provider.is_active,
            provider.source,
            provider.source_identifier,
        ),
    )
    provider_rows = [_provider_row(provider) for provider in providers]
    context = {
        **model_admin.admin_site.each_context(request),
        "opts": Contact._meta,
        "title": f"Call sheet: {competitor}",
        "competitor": competitor,
        "job_notice": job_notice,
        "linked_job": linked_job,
        "provider_rows": provider_rows,
        "phone_numbers": list(
            dict.fromkeys(
                row.phone_number for row in provider_rows if row.provider.is_active
            )
        ),
        "labels": load_competitor_labels([competitor], chains)[competitor.pk],
        "current_value_rows": _current_value_rows(current_values),
        "history": build_value_history(observations),
        "previous_contacts": Contact.objects.filter(
            competitor_id__in=chains.member_ids(competitor.pk)
        ).select_related("researcher", "job"),
        "sheet": sheet,
        "history_url": reverse("admin:research_observation_changelist")
        + f"?competitor={competitor.pk}",
    }
    return TemplateResponse(request, "admin/research/contact/call_sheet.html", context)


def retraction(
    request: HttpRequest, model_admin: ModelAdmin[Any], contact_id: int
) -> HttpResponse:
    """Retract a whole contact, or one observation of it, with a reason."""
    if not request.user.has_perm("research.retract_contact"):
        raise PermissionDenied
    contact = get_object_or_404(
        Contact.objects.select_related("competitor"), pk=contact_id
    )
    observation = _observation_to_retract(request, contact)
    contact_url = reverse("admin:research_contact_change", args=(contact.pk,))
    form = RetractionForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        researcher = researcher_from_request(request)
        reason = form.cleaned_data["reason"]
        try:
            if observation is None:
                contacts.retract_contact(
                    contact=contact, researcher=researcher, reason=reason
                )
                message = f"Contact #{contact.pk} retracted."
            else:
                contacts.retract_observation(
                    observation=observation, researcher=researcher, reason=reason
                )
                message = f"{observation} retracted."
        except RetractionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, message)
        return HttpResponseRedirect(contact_url)
    context = {
        **model_admin.admin_site.each_context(request),
        "opts": Contact._meta,
        "title": f"Retract {observation if observation is not None else contact}",
        "contact": contact,
        "observation": observation,
        "contact_url": contact_url,
        "form": form,
    }
    return TemplateResponse(request, "admin/research/contact/retract.html", context)


def _search_table_rows(
    rows: Sequence[SearchRow],
    labels: Mapping[int, CompetitorLabels],
    ticked_ids: frozenset[int] | None,
) -> list[SearchTableRow]:
    return [
        SearchTableRow(
            competitor=row.competitor,
            distance=format_miles(row.distance_meters),
            labels=labels[row.competitor.pk],
            is_selected=row.is_selected
            if ticked_ids is None
            else row.competitor.pk in ticked_ids,
        )
        for row in rows
    ]


def _linked_job(
    request: HttpRequest, competitor: Competitor, chains: MergeChains
) -> tuple[ResearchJob | None, str]:
    """Return the job a contact links to, or a notice saying why it does not."""
    raw_job_id = request.GET.get("job")
    if raw_job_id is None:
        return None, ""
    job = (
        ResearchJob.objects.filter(pk=int(raw_job_id)).first()
        if raw_job_id.isdecimal()
        else None
    )
    if job is None:
        reason = "it does not exist"
    elif job.status != JobStatus.RESEARCHING:
        reason = f"it is {job.get_status_display()}, not Researching"
    elif not any(
        displayed.competitor.pk == competitor.pk
        for displayed in load_displayed_job_competitors(job, chains)
    ):
        reason = "it does not list this competitor"
    else:
        return job, ""
    return None, f"This contact will not be linked to job #{raw_job_id}: {reason}."


def _provider_row(provider: Provider) -> ProviderRow:
    if not provider.is_active:
        phone_number = ""
    elif provider.ny_state_is_phone_omitted:
        phone_number = "Withheld by the regulator"
    else:
        phone_number = provider.phone_number.strip() or "Not published"
    return ProviderRow(
        provider=provider,
        phone_number=phone_number,
        ages_served=ages_served([provider]),
        licensed_capacity="Unpublished"
        if provider.total_capacity is None
        else str(provider.total_capacity),
    )


def _current_value_rows(current_values: CurrentValues) -> list[CurrentValueRow]:
    rows = []
    for slot, current_slot in current_values.slots.items():
        slot_label = ObservationKind(slot.kind).label
        if slot.subkind:
            first = current_slot.observations[0]
            subkind_label = (
                first.get_discount_kind_display()
                if slot.kind == ObservationKind.DISCOUNT
                else first.get_charge_kind_display()
            )
            slot_label = f"{slot_label}: {subkind_label}"
        rows.append(
            (
                _KIND_ORDER[slot.kind],
                CurrentValueRow(
                    slot_label=slot_label,
                    summaries=tuple(
                        observation.value_summary()
                        for observation in current_slot.observations
                    ),
                    contact=current_slot.contact,
                ),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1].slot_label, item[1].summaries))
    return [row for _, row in rows]


def _observation_to_retract(
    request: HttpRequest, contact: Contact
) -> Observation | None:
    raw_observation_id = request.GET.get("observation")
    if raw_observation_id is None:
        return None
    if not raw_observation_id.isdecimal():
        raise Http404
    return get_object_or_404(
        Observation.objects.select_related("age_group", "schedule"),
        pk=int(raw_observation_id),
        contact=contact,
    )
