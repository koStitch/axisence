"""The subscription form of the admin, checked with the subscription lifecycle's rules,
so their messages appear on the form.
"""

from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from axisence.subscriptions import lifecycle
from axisence.subscriptions.models import Subscription

# django-stubs declares ModelForm generic, but it has no runtime __class_getitem__,
# so the parameterized base exists only for the type checker.
if TYPE_CHECKING:
    SubscriptionFormBase = forms.ModelForm[Subscription]
else:
    SubscriptionFormBase = forms.ModelForm

SUBSCRIPTION_FIELD_NAMES = ("starting_job", "cadence", "start_date", "end_date")


class SubscriptionForm(SubscriptionFormBase):
    class Meta:
        model = Subscription
        fields = SUBSCRIPTION_FIELD_NAMES

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if self.errors:
            return cleaned
        # Fields the admin shows read-only keep their stored values.
        candidate = Subscription(
            pk=self.instance.pk,
            created_by_id=self.instance.created_by_id,
            **{
                field_name: self.cleaned_data[field_name]
                if field_name in self.fields
                else getattr(self.instance, field_name)
                for field_name in SUBSCRIPTION_FIELD_NAMES
            },
        )
        try:
            lifecycle.validate_subscription(candidate, today=timezone.localdate())
        except ValidationError as error:
            self.add_error(None, error)
        return cleaned
