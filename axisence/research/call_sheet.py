"""The call sheet's entry forms and their translation into one contact entry.

Every pre-filled slot starts Not covered, so a contact records only what the
researcher covered. Definitions created in the same contact are referenced from price
and availability rows by letter, as "New age group A".
"""

import dataclasses
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.forms.boundfield import BoundField, BoundWidget
from django.forms.models import fields_for_model
from django.http import QueryDict
from django.utils import timezone
from django.utils.dates import MONTHS

from axisence.providers.models import Competitor
from axisence.research.contacts import (
    DEFINITION_USING_KINDS,
    OFFERED_AND_NOT_OFFERED_MESSAGE,
    RETIRED_DEFINITION_IN_USE_MESSAGE,
    SUBKIND_FIELD_BY_KIND,
    ContactEntry,
    DefinitionReference,
    NewDefinition,
    ObservationEntry,
    has_offered_and_not_offered_kind,
)
from axisence.research.current_values import CurrentSlot, CurrentValues, Slot
from axisence.research.models import (
    DEFINITION_REFERENCE_FIELD_NAMES,
    DESCRIPTION_MAXIMUM_LENGTH_BY_KIND,
    NOTES_MAXIMUM_LENGTH,
    NOTES_WARNING,
    OBSERVATION_FIELDS_BY_KIND,
    ChargeKind,
    ContactMethod,
    DiscountKind,
    Observation,
    ObservationKind,
    PersonRole,
    ResearchJob,
)

NEW_AGE_GROUP_KEYS = ("A", "B", "C", "D")
NEW_SCHEDULE_KEYS = ("A", "B", "C", "D")
NEW_PRICE_ROW_COUNT = 8
NEW_AVAILABILITY_ROW_COUNT = 4
NEW_DISCOUNT_ROW_COUNT = 3
NEW_CHARGE_ROW_COUNT = 3

LAST_FEE_RISE_EARLIEST_YEAR = 1990
LAST_FEE_RISE_LATEST_YEAR = 2100

_EXISTING_REFERENCE_PREFIX = "existing:"
_NEW_REFERENCE_PREFIX = "new:"
_BLANK_CHOICE = ("", "---------")

STALE_SHEET_MESSAGE = (
    "The age groups or schedules changed since this call sheet was opened. Check the "
    "values and save again."
)


class Coverage(models.TextChoices):
    """What the researcher did with a pre-filled slot; never stored."""

    NOT_COVERED = "not_covered", "Not covered"
    CONFIRMED = "confirmed", "Confirmed"
    NEW_VALUE = "new_value", "New value"


def _date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _time_input() -> forms.TimeInput:
    return forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


def _observation_fields(
    kind: str, field_names: Sequence[str]
) -> dict[str, forms.Field]:
    """Build optional form fields from the Observation columns of one kind.

    Model validators, choices, and help texts carry over, and a description takes
    its kind's length limit; requiredness is checked in the forms' clean.
    """
    built = fields_for_model(Observation, fields=field_names)
    fields: dict[str, forms.Field] = {}
    for field_name in field_names:
        field = built[field_name]
        field.required = False
        if isinstance(field, forms.DateField):
            field.widget = _date_input()
        elif isinstance(field, forms.TimeField):
            field.widget = _time_input()
        if field_name == "description" and isinstance(field, forms.CharField):
            description_limit = DESCRIPTION_MAXIMUM_LENGTH_BY_KIND[kind]
            field.max_length = description_limit
            field.validators.append(MaxLengthValidator(description_limit))
        fields[field_name] = field
    return fields


def _includes_discounts_field() -> forms.BooleanField:
    # A checkbox instead of the null-boolean select of the nullable column; unticked
    # records False.
    return forms.BooleanField(required=False, label="Amount includes discounts")


def _none_offered_field() -> forms.BooleanField:
    return forms.BooleanField(required=False, label="None offered")


class _ObservationForm(forms.Form):
    """A form whose value fields are the Observation columns of one kind."""

    value_field_names: ClassVar[tuple[str, ...]]

    def __init__(
        self,
        data: QueryDict | None,
        *,
        prefix: str,
        kind: str,
        empty_permitted: bool = False,
    ) -> None:
        self.kind = kind
        super().__init__(
            data,
            prefix=prefix,
            empty_permitted=empty_permitted,
            use_required_attribute=False,
        )
        self.fields.update(self.build_fields())

    def build_fields(self) -> dict[str, forms.Field]:
        return _observation_fields(self.kind, self.value_field_names)

    def fixed_values(self) -> dict[str, Any]:
        """Return the column values the form's position decides, not its fields."""
        return {}

    def observation_values(self) -> dict[str, Any]:
        """Return the model column values of the cleaned value fields."""
        return {
            field_name: self.cleaned_data[field_name]
            for field_name in self.value_field_names
        }

    def initial_values(self, observation: Observation) -> dict[str, Any]:
        return {
            field_name: getattr(observation, field_name)
            for field_name in self.value_field_names
        }

    def form_field_name(self, model_field_name: str) -> str | None:
        """Return the form field showing a column's errors, or None if none does."""
        if model_field_name in DEFINITION_REFERENCE_FIELD_NAMES:
            return None
        return model_field_name if model_field_name in self.fields else None

    def add_model_errors(self) -> None:
        """Check the values against the observation rules of the form's kind."""
        observation = Observation(
            kind=self.kind, **self.fixed_values(), **self.observation_values()
        )
        try:
            observation.clean()
        except ValidationError as error:
            for model_field_name, messages in error.message_dict.items():
                form_field_name = self.form_field_name(model_field_name)
                if form_field_name is not None:
                    for message in messages:
                        self.add_error(form_field_name, message)


class SlotForm(_ObservationForm):
    """A pre-filled row: the slot's current value and what the contact found.

    Confirmed records the current value again, taken from the database; New value
    records the fields; Not covered records nothing. An edited field needs New value.
    """

    def __init__(
        self,
        data: QueryDict | None,
        *,
        slot: Slot,
        current: CurrentSlot | None,
        label: str,
    ) -> None:
        self.slot = slot
        self.current = current
        self.label = label
        super().__init__(data, prefix=_slot_prefix(slot), kind=slot.kind)
        if current is not None:
            self.initial.update(self.initial_values(current.observations[0]))

    def build_fields(self) -> dict[str, forms.Field]:
        choices = [
            choice
            for choice in Coverage.choices
            if self.current is not None or choice[0] != Coverage.CONFIRMED
        ]
        return {
            "coverage": forms.ChoiceField(
                choices=choices,
                widget=forms.RadioSelect,
                initial=Coverage.NOT_COVERED,
            ),
            **super().build_fields(),
        }

    def fixed_values(self) -> dict[str, Any]:
        subkind_field = SUBKIND_FIELD_BY_KIND.get(self.slot.kind)
        return {} if subkind_field is None else {subkind_field: self.slot.subkind}

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        coverage = self.cleaned_data.get("coverage")
        if coverage in (Coverage.NOT_COVERED, Coverage.CONFIRMED):
            # Neither records the fields, so an edit there would be lost.
            if any(field_name != "coverage" for field_name in self.changed_data):
                self.add_error(
                    "coverage", "Choose New value to record a changed value."
                )
        elif coverage == Coverage.NEW_VALUE and not self.errors:
            self.add_model_errors()
        return cleaned

    def observation_entries(self) -> list[ObservationEntry]:
        coverage = self.cleaned_data["coverage"]
        if coverage == Coverage.CONFIRMED and self.current is not None:
            return [
                _entry_copying(observation) for observation in self.current.observations
            ]
        if coverage == Coverage.NEW_VALUE:
            return [
                ObservationEntry(
                    kind=self.kind,
                    values={**self.fixed_values(), **self.observation_values()},
                    age_group=_existing_reference(self.slot.age_group_id),
                    schedule=_existing_reference(self.slot.schedule_id),
                )
            ]
        return []


class PriceSlotForm(SlotForm):
    value_field_names = ("amount", "price_period", "includes_discounts")

    def build_fields(self) -> dict[str, forms.Field]:
        return {
            **super().build_fields(),
            "includes_discounts": _includes_discounts_field(),
        }


class AvailabilitySlotForm(SlotForm):
    value_field_names = (
        "availability_status",
        "earliest_start_on",
        "expected_wait_months",
    )


class DiscountSlotForm(SlotForm):
    value_field_names = ("is_none_offered", "percent", "amount", "description")

    def build_fields(self) -> dict[str, forms.Field]:
        return {**super().build_fields(), "is_none_offered": _none_offered_field()}


class ChargeSlotForm(SlotForm):
    value_field_names = (
        "is_none_offered",
        "amount",
        "months_of_tuition",
        "charge_frequency",
        "description",
    )

    def build_fields(self) -> dict[str, forms.Field]:
        return {**super().build_fields(), "is_none_offered": _none_offered_field()}


class OperatingStatusForm(SlotForm):
    value_field_names = ("operating_status",)


class WeeksOpenForm(SlotForm):
    value_field_names = ("weeks_open_per_year",)


class WeekdayHoursForm(SlotForm):
    value_field_names = ("opens_at", "closes_at")


class InspectionStatusForm(SlotForm):
    value_field_names = ("inspection_status", "inspected_on")


class StatedPlacesForm(SlotForm):
    value_field_names = ("stated_places",)


class MinimumBookingForm(SlotForm):
    value_field_names = ("days_per_week",)


class LastFeeRiseForm(SlotForm):
    """Takes the month and the year of the last rise, stored as its first day."""

    value_field_names = ("last_fee_rise_on",)

    def build_fields(self) -> dict[str, forms.Field]:
        fields = super().build_fields()
        del fields["last_fee_rise_on"]
        return {
            **fields,
            "month": forms.TypedChoiceField(
                choices=[_BLANK_CHOICE, *MONTHS.items()],
                coerce=int,
                empty_value=None,
                required=False,
            ),
            "year": forms.IntegerField(
                min_value=LAST_FEE_RISE_EARLIEST_YEAR,
                max_value=LAST_FEE_RISE_LATEST_YEAR,
                required=False,
            ),
        }

    def initial_values(self, observation: Observation) -> dict[str, Any]:
        if observation.last_fee_rise_on is None:
            return {}
        return {
            "month": observation.last_fee_rise_on.month,
            "year": observation.last_fee_rise_on.year,
        }

    def observation_values(self) -> dict[str, Any]:
        month = self.cleaned_data.get("month")
        year = self.cleaned_data.get("year")
        if month is None or year is None:
            return {"last_fee_rise_on": None}
        return {"last_fee_rise_on": date(year, month, 1)}

    def form_field_name(self, model_field_name: str) -> str | None:
        return "month" if model_field_name == "last_fee_rise_on" else None

    def clean(self) -> dict[str, Any] | None:
        # Checked before the slot's own rules, which would otherwise report a half-
        # filled date as a missing value.
        if (self.cleaned_data.get("month") is None) != (
            self.cleaned_data.get("year") is None
        ):
            self.add_error("year", "Give both the month and the year.")
        return super().clean()


class DescriptionForm(SlotForm):
    value_field_names = ("description",)

    def build_fields(self) -> dict[str, forms.Field]:
        fields = super().build_fields()
        fields["description"].widget = forms.Textarea(attrs={"rows": 3})
        return fields


class ParkingForm(SlotForm):
    value_field_names = ("parking", "description")

    def build_fields(self) -> dict[str, forms.Field]:
        fields = super().build_fields()
        fields["description"].label = "Note"
        return fields


class WebsiteForm(SlotForm):
    value_field_names = ("website_url",)


class YesNoForm(SlotForm):
    value_field_names = ("yes_no_answer",)

    def build_fields(self) -> dict[str, forms.Field]:
        return {
            **super().build_fields(),
            "yes_no_answer": forms.TypedChoiceField(
                choices=(("yes", "Yes"), ("no", "No")),
                coerce=lambda value: value == "yes",
                empty_value=None,
                required=False,
                widget=forms.RadioSelect,
                label="Answer",
            ),
        }

    def initial_values(self, observation: Observation) -> dict[str, Any]:
        # A string, because has_changed coerces both sides and a boolean initial
        # would always count as changed.
        return {"yes_no_answer": "yes" if observation.yes_no_answer else "no"}


_SINGLE_FACT_FORM_CLASSES: tuple[tuple[ObservationKind, type[SlotForm]], ...] = (
    (ObservationKind.OPERATING_STATUS, OperatingStatusForm),
    (ObservationKind.WEEKS_OPEN, WeeksOpenForm),
    (ObservationKind.WEEKDAY_HOURS, WeekdayHoursForm),
    (ObservationKind.INSPECTION_STATUS, InspectionStatusForm),
    (ObservationKind.STATED_PLACES, StatedPlacesForm),
    (ObservationKind.MINIMUM_BOOKING, MinimumBookingForm),
    (ObservationKind.LAST_FEE_RISE, LastFeeRiseForm),
    (ObservationKind.DESCRIPTION, DescriptionForm),
    (ObservationKind.PARKING, ParkingForm),
    (ObservationKind.WEBSITE, WebsiteForm),
    (ObservationKind.THREE_K_FOR_ALL, YesNoForm),
    (ObservationKind.PRE_K_FOR_ALL, YesNoForm),
    (ObservationKind.ACCEPTS_VOUCHERS, YesNoForm),
)


class _NewRowForm(_ObservationForm):
    """A blank row for a value no pre-filled row holds; a blank row is ignored."""

    observation_kind: ClassVar[str]
    reference_field_names: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        data: QueryDict | None,
        *,
        prefix: str,
        label: str,
        age_group_choices: Sequence[tuple[str, str]] = (),
        schedule_choices: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.label = label
        self._reference_choices = {
            "age_group": age_group_choices,
            "schedule": schedule_choices,
        }
        super().__init__(
            data, prefix=prefix, kind=self.observation_kind, empty_permitted=True
        )
        # The row's fields sit under column headings, their only visible labels.
        for field_name, field in self.fields.items():
            field.widget.attrs["aria-label"] = f"{label}: {field.label or field_name}"

    def build_fields(self) -> dict[str, forms.Field]:
        references: dict[str, forms.Field] = {
            field_name: forms.ChoiceField(
                choices=[_BLANK_CHOICE, *self._reference_choices[field_name]],
                required=False,
                label=field_name.replace("_", " ").capitalize(),
            )
            for field_name in self.reference_field_names
        }
        return {**references, **super().build_fields()}

    @property
    def is_filled(self) -> bool:
        return self.has_changed()

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        for field_name in self.reference_field_names:
            if not self.cleaned_data.get(field_name):
                self.add_error(field_name, "Choose one.")
        if not self.errors:
            self.add_model_errors()
        return cleaned

    def observation_entries(self) -> list[ObservationEntry]:
        if not self.is_filled:
            return []
        return [
            ObservationEntry(
                kind=self.kind,
                values=self.observation_values(),
                age_group=_parse_reference(self.cleaned_data.get("age_group", "")),
                schedule=_parse_reference(self.cleaned_data.get("schedule", "")),
            )
        ]


class NewPriceForm(_NewRowForm):
    observation_kind = ObservationKind.PRICE
    reference_field_names = ("age_group", "schedule")
    value_field_names = ("amount", "price_period", "includes_discounts")

    def build_fields(self) -> dict[str, forms.Field]:
        return {
            **super().build_fields(),
            "includes_discounts": _includes_discounts_field(),
        }


class NewAvailabilityForm(_NewRowForm):
    observation_kind = ObservationKind.AVAILABILITY
    reference_field_names = ("age_group",)
    value_field_names = (
        "availability_status",
        "earliest_start_on",
        "expected_wait_months",
    )


class NewDiscountForm(_NewRowForm):
    observation_kind = ObservationKind.DISCOUNT
    value_field_names = ("discount_kind", "percent", "amount", "description")


class NewChargeForm(_NewRowForm):
    observation_kind = ObservationKind.OTHER_CHARGE
    value_field_names = (
        "charge_kind",
        "amount",
        "months_of_tuition",
        "charge_frequency",
        "description",
    )


class _NewDefinitionForm(_NewRowForm):
    """A blank row for an age group or schedule first stated in this contact."""

    prefix_stem: ClassVar[str]

    def __init__(self, data: QueryDict | None, *, key: str) -> None:
        self.key = key
        kind_label = ObservationKind(self.observation_kind).label.lower()
        super().__init__(
            data, prefix=f"{self.prefix_stem}-{key}", label=f"New {kind_label} {key}"
        )

    def new_definition(self) -> NewDefinition | None:
        if not self.is_filled:
            return None
        return NewDefinition(
            key=self.key, kind=self.kind, values=self.observation_values()
        )


class NewAgeGroupForm(_NewDefinitionForm):
    observation_kind = ObservationKind.AGE_GROUP
    prefix_stem = "new-age-group"
    value_field_names = ("name", "youngest_age_months", "oldest_age_months")

    def build_fields(self) -> dict[str, forms.Field]:
        fields = super().build_fields()
        fields[
            "youngest_age_months"
        ].help_text = "Whole months, rounded down; 6 weeks is 1."
        return fields


class NewScheduleForm(_NewDefinitionForm):
    observation_kind = ObservationKind.SCHEDULE
    prefix_stem = "new-schedule"
    value_field_names = ("name", "days_per_week", "hours_per_day")


class ContactForm(forms.Form):
    contact_date = forms.DateField(initial=timezone.localdate, widget=_date_input())
    method = forms.ChoiceField(
        choices=ContactMethod.choices, initial=ContactMethod.CALL
    )
    person_role = forms.ChoiceField(
        choices=[_BLANK_CHOICE, *PersonRole.choices],
        required=False,
        label="Role of the person spoken to",
    )
    notes = forms.CharField(
        max_length=NOTES_MAXIMUM_LENGTH,
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=NOTES_WARNING,
    )

    def clean_contact_date(self) -> date:
        contact_date: date = self.cleaned_data["contact_date"]
        if contact_date > timezone.localdate():
            raise ValidationError("The contact date cannot be in the future.")
        return contact_date


class DefinitionRetirementForm(forms.Form):
    """The current age groups and schedules the contact retires."""

    def __init__(
        self,
        data: QueryDict | None,
        *,
        age_group_choices: Sequence[tuple[int, str]],
        schedule_choices: Sequence[tuple[int, str]],
    ) -> None:
        super().__init__(data, prefix="retirement", use_required_attribute=False)
        for field_name, choices in (
            ("retired_age_groups", age_group_choices),
            ("retired_schedules", schedule_choices),
        ):
            self.fields[field_name] = forms.TypedMultipleChoiceField(
                choices=choices,
                coerce=int,
                required=False,
                widget=forms.CheckboxSelectMultiple,
            )

    def retired_definition_ids(self) -> frozenset[int]:
        return frozenset(self.cleaned_data["retired_age_groups"]) | frozenset(
            self.cleaned_data["retired_schedules"]
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DefinitionRow:
    """A current definition beside its Retire checkbox."""

    definition: Observation
    label: str
    retire_checkbox: BoundWidget


@dataclasses.dataclass(frozen=True, slots=True)
class DefinitionSection:
    title: str
    rows: tuple[DefinitionRow, ...]
    # The Retire checkboxes' field, for its errors.
    retire_field: BoundField
    new_forms: tuple[_NewDefinitionForm, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ValueSection:
    title: str
    slot_forms: tuple[SlotForm, ...]
    new_forms: tuple[_NewRowForm, ...]


class CallSheet:
    """Every entry form of one competitor's call sheet, validated together."""

    def __init__(
        self,
        *,
        competitor: Competitor,
        current_values: CurrentValues,
        data: QueryDict | None,
    ) -> None:
        self.competitor = competitor
        self._data = data
        self.errors: list[str] = []
        age_groups = current_values.age_groups
        schedules = current_values.schedules
        slots = current_values.slots
        self.contact_form = ContactForm(data, prefix="contact")
        self.retirement_form = DefinitionRetirementForm(
            data,
            age_group_choices=[
                (definition.pk, self._definition_label(definition))
                for definition in age_groups
            ],
            schedule_choices=[
                (definition.pk, self._definition_label(definition))
                for definition in schedules
            ],
        )
        self.new_age_group_forms = tuple(
            NewAgeGroupForm(data, key=key) for key in NEW_AGE_GROUP_KEYS
        )
        self.new_schedule_forms = tuple(
            NewScheduleForm(data, key=key) for key in NEW_SCHEDULE_KEYS
        )
        age_group_choices = [
            *self._reference_choices(age_groups),
            *(
                (f"{_NEW_REFERENCE_PREFIX}{key}", f"New age group {key}")
                for key in NEW_AGE_GROUP_KEYS
            ),
        ]
        schedule_choices = [
            *self._reference_choices(schedules),
            *(
                (f"{_NEW_REFERENCE_PREFIX}{key}", f"New schedule {key}")
                for key in NEW_SCHEDULE_KEYS
            ),
        ]
        price_forms = []
        for age_group in age_groups:
            for schedule in schedules:
                slot = Slot(
                    ObservationKind.PRICE,
                    age_group_id=age_group.pk,
                    schedule_id=schedule.pk,
                )
                price_forms.append(
                    PriceSlotForm(
                        data,
                        slot=slot,
                        current=slots.get(slot),
                        label=f"{age_group.name} on {schedule.name}",
                    )
                )
        availability_forms = []
        for age_group in age_groups:
            slot = Slot(ObservationKind.AVAILABILITY, age_group_id=age_group.pk)
            availability_forms.append(
                AvailabilitySlotForm(
                    data, slot=slot, current=slots.get(slot), label=age_group.name
                )
            )
        discount_forms = []
        for discount_kind in DiscountKind:
            slot = Slot(ObservationKind.DISCOUNT, subkind=discount_kind.value)
            discount_forms.append(
                DiscountSlotForm(
                    data, slot=slot, current=slots.get(slot), label=discount_kind.label
                )
            )
        charge_forms = []
        for charge_kind in ChargeKind:
            slot = Slot(ObservationKind.OTHER_CHARGE, subkind=charge_kind.value)
            charge_forms.append(
                ChargeSlotForm(
                    data, slot=slot, current=slots.get(slot), label=charge_kind.label
                )
            )
        single_fact_forms = []
        for kind, form_class in _SINGLE_FACT_FORM_CLASSES:
            slot = Slot(kind.value)
            single_fact_forms.append(
                form_class(data, slot=slot, current=slots.get(slot), label=kind.label)
            )
        self.definition_sections = (
            DefinitionSection(
                title="Age groups",
                rows=self._definition_rows(age_groups, "retired_age_groups"),
                retire_field=self.retirement_form["retired_age_groups"],
                new_forms=self.new_age_group_forms,
            ),
            DefinitionSection(
                title="Schedules",
                rows=self._definition_rows(schedules, "retired_schedules"),
                retire_field=self.retirement_form["retired_schedules"],
                new_forms=self.new_schedule_forms,
            ),
        )
        self.value_sections = (
            ValueSection(
                title="Prices",
                slot_forms=tuple(price_forms),
                new_forms=tuple(
                    NewPriceForm(
                        data,
                        prefix=f"new-price-{number}",
                        label=f"New price {number}",
                        age_group_choices=age_group_choices,
                        schedule_choices=schedule_choices,
                    )
                    for number in range(1, NEW_PRICE_ROW_COUNT + 1)
                ),
            ),
            ValueSection(
                title="Availability",
                slot_forms=tuple(availability_forms),
                new_forms=tuple(
                    NewAvailabilityForm(
                        data,
                        prefix=f"new-availability-{number}",
                        label=f"New availability {number}",
                        age_group_choices=age_group_choices,
                    )
                    for number in range(1, NEW_AVAILABILITY_ROW_COUNT + 1)
                ),
            ),
            ValueSection(
                title="Discounts",
                slot_forms=tuple(discount_forms),
                new_forms=tuple(
                    NewDiscountForm(
                        data,
                        prefix=f"new-discount-{number}",
                        label=f"New discount {number}",
                    )
                    for number in range(1, NEW_DISCOUNT_ROW_COUNT + 1)
                ),
            ),
            ValueSection(
                title="Other charges",
                slot_forms=tuple(charge_forms),
                new_forms=tuple(
                    NewChargeForm(
                        data,
                        prefix=f"new-charge-{number}",
                        label=f"New other charge {number}",
                    )
                    for number in range(1, NEW_CHARGE_ROW_COUNT + 1)
                ),
            ),
            ValueSection(
                title="Single facts",
                slot_forms=tuple(single_fact_forms),
                new_forms=(),
            ),
        )

    def is_valid(self) -> bool:
        """Validate every form, then the rules that span several of them."""
        # A list, so every form is validated and shows its errors.
        results = [form.is_valid() for form in self._forms()]
        self.errors = self._stale_slot_errors()
        if not all(results) or self.errors:
            return False
        self.errors = self._entry_errors()
        return not self.errors

    @property
    def has_errors(self) -> bool:
        """Return whether a posted sheet holds an error anywhere."""
        return bool(self.errors) or any(form.errors for form in self._forms())

    def contact_entry(self, *, job: ResearchJob | None) -> ContactEntry:
        cleaned = self.contact_form.cleaned_data
        return ContactEntry(
            competitor=self.competitor,
            job=job,
            contact_date=cleaned["contact_date"],
            method=cleaned["method"],
            person_role=cleaned["person_role"],
            notes=cleaned["notes"],
            new_definitions=tuple(
                definition
                for form in (*self.new_age_group_forms, *self.new_schedule_forms)
                if (definition := form.new_definition()) is not None
            ),
            retired_definition_ids=self.retirement_form.retired_definition_ids(),
            observations=tuple(self._observation_entries()),
        )

    def _forms(self) -> Iterator[forms.Form]:
        yield self.contact_form
        yield self.retirement_form
        for definition_section in self.definition_sections:
            yield from definition_section.new_forms
        for value_section in self.value_sections:
            yield from value_section.slot_forms
            yield from value_section.new_forms

    def _observation_entries(self) -> list[ObservationEntry]:
        entries = []
        for section in self.value_sections:
            for slot_form in section.slot_forms:
                entries.extend(slot_form.observation_entries())
            for new_form in section.new_forms:
                entries.extend(new_form.observation_entries())
        return entries

    def _entry_errors(self) -> list[str]:
        errors: list[str] = []
        filled_new_keys = {
            (form.kind, form.key)
            for form in (*self.new_age_group_forms, *self.new_schedule_forms)
            if form.is_filled
        }
        retired_ids = self.retirement_form.retired_definition_ids()
        observation_entries = self._observation_entries()
        for entry in observation_entries:
            for kind, reference in (
                (ObservationKind.AGE_GROUP, entry.age_group),
                (ObservationKind.SCHEDULE, entry.schedule),
            ):
                if reference is None:
                    continue
                if (
                    reference.new_key
                    and (kind, reference.new_key) not in filled_new_keys
                ):
                    kind_label = ObservationKind(kind).label.lower()
                    errors.append(
                        f"New {kind_label} {reference.new_key} is chosen in a row but "
                        "left blank."
                    )
                if (
                    entry.kind in DEFINITION_USING_KINDS
                    and reference.existing_id in retired_ids
                ):
                    errors.append(RETIRED_DEFINITION_IN_USE_MESSAGE)
        if has_offered_and_not_offered_kind(observation_entries):
            errors.append(OFFERED_AND_NOT_OFFERED_MESSAGE)
        return list(dict.fromkeys(errors))

    def _stale_slot_errors(self) -> list[str]:
        """Refuse values posted for slots the sheet no longer has.

        An age group or schedule retired or retracted since the sheet was opened takes
        its price and availability slots with it, and a value entered there would
        otherwise be dropped unnoticed.
        """
        if self._data is None:
            return []
        slot_prefixes = {
            slot_form.prefix
            for section in self.value_sections
            for slot_form in section.slot_forms
        }
        for key, value in self._data.items():
            prefix, _, field_name = key.rpartition("-")
            if (
                field_name == "coverage"
                and prefix not in slot_prefixes
                and value != Coverage.NOT_COVERED
            ):
                return [STALE_SHEET_MESSAGE]
        return []

    def _definition_label(self, definition: Observation) -> str:
        label = definition.value_summary()
        if definition.contact.competitor_id != self.competitor.pk:
            label += f" (from merged competitor #{definition.contact.competitor_id})"
        return label

    def _reference_choices(
        self, definitions: Sequence[Observation]
    ) -> list[tuple[str, str]]:
        return [
            (
                f"{_EXISTING_REFERENCE_PREFIX}{definition.pk}",
                self._definition_label(definition),
            )
            for definition in definitions
        ]

    def _definition_rows(
        self, definitions: Sequence[Observation], field_name: str
    ) -> tuple[DefinitionRow, ...]:
        checkboxes = self.retirement_form[field_name].subwidgets
        return tuple(
            DefinitionRow(
                definition=definition,
                label=self._definition_label(definition),
                retire_checkbox=checkbox,
            )
            for definition, checkbox in zip(definitions, checkboxes, strict=True)
        )


def _slot_prefix(slot: Slot) -> str:
    match slot.kind:
        case ObservationKind.PRICE:
            return f"price-{slot.age_group_id}-{slot.schedule_id}"
        case ObservationKind.AVAILABILITY:
            return f"availability-{slot.age_group_id}"
        case ObservationKind.DISCOUNT:
            return f"discount-{slot.subkind}"
        case ObservationKind.OTHER_CHARGE:
            return f"charge-{slot.subkind}"
    return slot.kind


def _entry_copying(observation: Observation) -> ObservationEntry:
    """Return an entry repeating a current observation, its definitions included."""
    fields = OBSERVATION_FIELDS_BY_KIND[observation.kind]
    return ObservationEntry(
        kind=observation.kind,
        values={
            field_name: getattr(observation, field_name)
            for field_name in (*fields.required, *fields.optional)
            if field_name not in DEFINITION_REFERENCE_FIELD_NAMES
        },
        age_group=_existing_reference(observation.age_group_id),
        schedule=_existing_reference(observation.schedule_id),
    )


def _existing_reference(definition_id: int | None) -> DefinitionReference | None:
    if definition_id is None:
        return None
    return DefinitionReference(existing_id=definition_id)


def _parse_reference(value: str) -> DefinitionReference | None:
    if value.startswith(_EXISTING_REFERENCE_PREFIX):
        return DefinitionReference(
            existing_id=int(value.removeprefix(_EXISTING_REFERENCE_PREFIX))
        )
    if value.startswith(_NEW_REFERENCE_PREFIX):
        return DefinitionReference(new_key=value.removeprefix(_NEW_REFERENCE_PREFIX))
    return None
