"""Recording contacts with their observations, and retracting them.

A contact and its observations are recorded in one transaction and never changed
afterwards. A mistake is retracted with a reason, and the correct value is recorded
in a new contact. Notes, descriptions, and values are never logged. A new age group or
schedule identical to a current one, or to another new one of the same contact, is
refused, so re-recording a definition never splits a slot's history.
"""

import dataclasses
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from axisence.accounts.models import User
from axisence.providers.models import Competitor
from axisence.research.current_values import CurrentValues, load_current_values
from axisence.research.job_competitors import load_displayed_job_competitors
from axisence.research.merges import MergeChains
from axisence.research.models import (
    RETRACTION_FIELD_NAMES,
    RETRACTION_REASON_MAXIMUM_LENGTH,
    SINGLE_FACT_KINDS,
    Contact,
    JobStatus,
    Observation,
    ObservationKind,
    ResearchJob,
)

logger = logging.getLogger(__name__)

# Kinds whose rows refer to definitions, and so cannot use one retired in the
# same contact.
DEFINITION_USING_KINDS = frozenset(
    {ObservationKind.PRICE, ObservationKind.AVAILABILITY}
)
# Kinds that can be recorded as none offered, with the column naming their kind.
SUBKIND_FIELD_BY_KIND: dict[str, str] = {
    ObservationKind.DISCOUNT: "discount_kind",
    ObservationKind.OTHER_CHARGE: "charge_kind",
}

RETIRED_DEFINITION_IN_USE_MESSAGE = (
    "A retired age group or schedule cannot get new facts in the same contact."
)
OFFERED_AND_NOT_OFFERED_MESSAGE = (
    "A kind cannot be both offered and not offered in one contact."
)


class RecordingError(Exception):
    """A contact that cannot be recorded; its message is shown on the call sheet."""


class RetractionError(Exception):
    """A retraction that cannot be applied; its message is shown in the admin."""


@dataclasses.dataclass(frozen=True, slots=True)
class DefinitionReference:
    # A current definition's observation id.
    existing_id: int | None = None
    # The letter of a definition created in the same contact.
    new_key: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class NewDefinition:
    # "A" to "D".
    key: str
    # Age group or schedule.
    kind: str
    values: Mapping[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class ObservationEntry:
    kind: str
    # Value columns other than age_group and schedule.
    values: Mapping[str, Any]
    age_group: DefinitionReference | None = None
    schedule: DefinitionReference | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ContactEntry:
    competitor: Competitor
    job: ResearchJob | None
    contact_date: date
    method: str
    person_role: str
    notes: str
    new_definitions: tuple[NewDefinition, ...]
    retired_definition_ids: frozenset[int]
    observations: tuple[ObservationEntry, ...]

    @property
    def observation_count(self) -> int:
        """Return how many observations recording the entry creates."""
        return (
            len(self.new_definitions)
            + len(self.retired_definition_ids)
            + len(self.observations)
        )


def has_offered_and_not_offered_kind(
    observations: Iterable[ObservationEntry],
) -> bool:
    """Return whether one discount or charge kind is both offered and not offered."""
    offered: set[tuple[str, str]] = set()
    not_offered: set[tuple[str, str]] = set()
    for observation in observations:
        subkind_field = SUBKIND_FIELD_BY_KIND.get(observation.kind)
        if subkind_field is None:
            continue
        key = (observation.kind, observation.values.get(subkind_field, ""))
        if observation.values.get("is_none_offered"):
            not_offered.add(key)
        else:
            offered.add(key)
    return bool(offered & not_offered)


@transaction.atomic
def record_contact(*, entry: ContactEntry, researcher: User) -> Contact:
    """Record one contact and every observation of the entry, or nothing."""
    competitor = Competitor.objects.select_for_update().get(pk=entry.competitor.pk)
    if competitor.retired_at is not None:
        raise RecordingError("Contacts are recorded only for current competitors.")
    chains = MergeChains.load()
    if entry.job is not None and not _may_link(entry.job, competitor, chains):
        raise RecordingError("This contact cannot be linked to the job.")
    if entry.contact_date > timezone.localdate():
        raise RecordingError("The contact date cannot be in the future.")
    current = load_current_values(competitor.pk, chains)
    current_ids_by_kind: dict[str, frozenset[int]] = {
        ObservationKind.AGE_GROUP: frozenset(
            definition.pk for definition in current.age_groups
        ),
        ObservationKind.SCHEDULE: frozenset(
            definition.pk for definition in current.schedules
        ),
    }
    _check_entry(entry, current, current_ids_by_kind)
    contact = Contact(
        competitor=competitor,
        job=entry.job,
        contact_date=entry.contact_date,
        method=entry.method,
        researcher=researcher,
        entered_at=timezone.now(),
        person_role=entry.person_role,
        notes=entry.notes,
    )
    try:
        contact.full_clean(
            exclude=("competitor", "job", "researcher", "retracted_by"),
            validate_constraints=False,
        )
        contact.save()
        new_definitions = {
            (definition.kind, definition.key): Observation(
                contact=contact, kind=definition.kind, **definition.values
            )
            for definition in entry.new_definitions
        }
        _validate_observations(new_definitions.values())
        Observation.objects.bulk_create(list(new_definitions.values()))
        observations = [
            _retirement(contact, definition_id, current_ids_by_kind)
            for definition_id in sorted(entry.retired_definition_ids)
        ]
        for observation_entry in entry.observations:
            observation = Observation(
                contact=contact, kind=observation_entry.kind, **observation_entry.values
            )
            observation.age_group_id = _resolve(
                observation_entry.age_group, ObservationKind.AGE_GROUP, new_definitions
            )
            observation.schedule_id = _resolve(
                observation_entry.schedule, ObservationKind.SCHEDULE, new_definitions
            )
            observations.append(observation)
        _validate_observations(observations)
        Observation.objects.bulk_create(observations)
    except ValidationError as error:
        raise RecordingError("The entry has invalid values.") from error
    logger.info(
        "Recorded contact %d for competitor %d with %d observations",
        contact.pk,
        competitor.pk,
        entry.observation_count,
    )
    return contact


@transaction.atomic
def retract_contact(*, contact: Contact, researcher: User, reason: str) -> None:
    """Retract a whole contact, which takes every observation of it with it."""
    reason = _checked_reason(reason)
    stored = Contact.objects.select_for_update().get(pk=contact.pk)
    if stored.is_retracted:
        raise RetractionError("This entry is already retracted.")
    _retract(stored, researcher, reason)
    logger.info("Retracted contact %d", stored.pk)


@transaction.atomic
def retract_observation(
    *, observation: Observation, researcher: User, reason: str
) -> None:
    """Retract one observation of a contact that is not retracted."""
    reason = _checked_reason(reason)
    stored = (
        Observation.objects.select_for_update()
        .select_related("contact")
        .get(pk=observation.pk)
    )
    if stored.is_retracted:
        raise RetractionError("This entry is already retracted.")
    if stored.contact.is_retracted:
        raise RetractionError("Its contact is already retracted.")
    _retract(stored, researcher, reason)
    logger.info("Retracted observation %d", stored.pk)


def _may_link(job: ResearchJob, competitor: Competitor, chains: MergeChains) -> bool:
    stored_job = ResearchJob.objects.get(pk=job.pk)
    return stored_job.status == JobStatus.RESEARCHING and any(
        displayed.competitor.pk == competitor.pk
        for displayed in load_displayed_job_competitors(stored_job, chains)
    )


def _check_entry(
    entry: ContactEntry,
    current: CurrentValues,
    current_ids_by_kind: Mapping[str, frozenset[int]],
) -> None:
    current_ids = frozenset().union(*current_ids_by_kind.values())
    if not entry.retired_definition_ids <= current_ids:
        raise RecordingError("A definition that is not current was referenced.")
    new_keys = {
        (definition.kind, definition.key) for definition in entry.new_definitions
    }
    for observation_entry in entry.observations:
        for kind, reference in (
            (ObservationKind.AGE_GROUP, observation_entry.age_group),
            (ObservationKind.SCHEDULE, observation_entry.schedule),
        ):
            if reference is None:
                continue
            if reference.existing_id is not None:
                if reference.existing_id not in current_ids_by_kind[kind]:
                    raise RecordingError(
                        "A definition that is not current was referenced."
                    )
                if (
                    observation_entry.kind in DEFINITION_USING_KINDS
                    and reference.existing_id in entry.retired_definition_ids
                ):
                    raise RecordingError(RETIRED_DEFINITION_IN_USE_MESSAGE)
            elif (kind, reference.new_key) not in new_keys:
                raise RecordingError(
                    "A new age group or schedule that the entry does not define was "
                    "referenced."
                )
    if has_offered_and_not_offered_kind(entry.observations):
        raise RecordingError(OFFERED_AND_NOT_OFFERED_MESSAGE)
    single_fact_kinds = [
        observation_entry.kind
        for observation_entry in entry.observations
        if observation_entry.kind in SINGLE_FACT_KINDS
    ]
    if len(single_fact_kinds) != len(set(single_fact_kinds)):
        raise RecordingError("The entry has invalid values.")
    duplicate = _duplicate_definition_message(entry.new_definitions, current)
    if duplicate is not None:
        raise RecordingError(duplicate)


def _normalized_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _definition_key(kind: str, values: Mapping[str, Any]) -> tuple[object, ...]:
    if kind == ObservationKind.AGE_GROUP:
        return (
            _normalized_name(values["name"]),
            values["youngest_age_months"],
            values.get("oldest_age_months"),
        )
    return (
        _normalized_name(values["name"]),
        values["days_per_week"],
        values.get("hours_per_day"),
    )


def _stored_definition_key(definition: Observation) -> tuple[object, ...]:
    if definition.kind == ObservationKind.AGE_GROUP:
        return (
            _normalized_name(definition.name),
            definition.youngest_age_months,
            definition.oldest_age_months,
        )
    return (
        _normalized_name(definition.name),
        definition.days_per_week,
        definition.hours_per_day,
    )


def _duplicate_definition_message(
    new_definitions: Sequence[NewDefinition], current: CurrentValues
) -> str | None:
    """Name the first new definition identical to a current one of its kind, or to an
    earlier new one of the same contact.

    Names compare without case and extra spaces; a blank limit or blank hours match
    only a blank.
    """
    for position, definition in enumerate(new_definitions):
        kind_label = ObservationKind(definition.kind).label.lower()
        key = _definition_key(definition.kind, definition.values)
        existing_definitions = (
            current.age_groups
            if definition.kind == ObservationKind.AGE_GROUP
            else current.schedules
        )
        for existing in existing_definitions:
            if _stored_definition_key(existing) == key:
                return (
                    f"New {kind_label} {definition.key} matches the current "
                    f'{kind_label} "{existing.name}". Choose that {kind_label} '
                    "instead."
                )
        for earlier in new_definitions[:position]:
            if earlier.kind == definition.kind and (
                _definition_key(earlier.kind, earlier.values) == key
            ):
                return (
                    f"New {kind_label} {definition.key} matches new {kind_label} "
                    f"{earlier.key}."
                )
    return None


def _retirement(
    contact: Contact,
    definition_id: int,
    current_ids_by_kind: Mapping[str, frozenset[int]],
) -> Observation:
    if definition_id in current_ids_by_kind[ObservationKind.AGE_GROUP]:
        return Observation(
            contact=contact,
            kind=ObservationKind.AGE_GROUP_RETIREMENT,
            age_group_id=definition_id,
        )
    return Observation(
        contact=contact,
        kind=ObservationKind.SCHEDULE_RETIREMENT,
        schedule_id=definition_id,
    )


def _resolve(
    reference: DefinitionReference | None,
    kind: str,
    new_definitions: Mapping[tuple[str, str], Observation],
) -> int | None:
    if reference is None:
        return None
    if reference.existing_id is not None:
        return reference.existing_id
    return new_definitions[(kind, reference.new_key)].pk


def _validate_observations(observations: Iterable[Observation]) -> None:
    # Foreign keys are excluded, because validating them would query each one.
    for observation in observations:
        observation.full_clean(
            exclude=("contact", "age_group", "schedule", "retracted_by"),
            validate_constraints=False,
        )


def _checked_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise RetractionError("A reason is required.")
    if len(reason) > RETRACTION_REASON_MAXIMUM_LENGTH:
        raise RetractionError(
            f"A reason holds at most {RETRACTION_REASON_MAXIMUM_LENGTH} characters."
        )
    return reason


def _retract(record: Contact | Observation, researcher: User, reason: str) -> None:
    record.retracted_at = timezone.now()
    record.retracted_by = researcher
    record.retraction_reason = reason
    record.save(update_fields=RETRACTION_FIELD_NAMES)
