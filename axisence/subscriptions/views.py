"""The subscription page's "Prepare repeat job" post, inside the admin, and the
permissions preparing needs.

The view is wired by the subscription admin's get_urls behind the admin login and
checks its own permissions.
"""

from typing import Any

from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.core.exceptions import PermissionDenied
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from axisence.research.models import ResearchJob
from axisence.research.views import researcher_from_request
from axisence.subscriptions import repeats
from axisence.subscriptions.models import Subscription


def may_prepare_repeat_jobs(request: HttpRequest) -> bool:
    """Return whether the user may prepare repeat jobs: the permission itself, adding
    research jobs, and viewing them.
    """
    return request.user.has_perms(
        ("subscriptions.prepare_repeat_job", "research.add_researchjob")
    ) and admin.site.get_model_admin(ResearchJob).has_view_permission(request)


def prepare_repeat_job(
    request: HttpRequest, model_admin: ModelAdmin[Any], subscription_id: int
) -> HttpResponse:
    """Prepare the repeat job of a subscription's latest job, for its earliest open
    due date.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not (
        model_admin.has_view_permission(request) and may_prepare_repeat_jobs(request)
    ):
        raise PermissionDenied
    subscription = get_object_or_404(Subscription, pk=subscription_id)
    try:
        prepared = repeats.prepare_repeat_job_for_subscription(
            subscription=subscription,
            user=researcher_from_request(request),
            today=timezone.localdate(),
        )
    except repeats.RepeatRefusedError as error:
        messages.error(request, str(error))
        return HttpResponseRedirect(
            reverse("admin:subscriptions_subscription_change", args=(subscription.pk,))
        )
    messages.success(request, repeats.prepared_message(prepared))
    return HttpResponseRedirect(
        reverse("admin:research_researchjob_change", args=(prepared.job.pk,))
    )
