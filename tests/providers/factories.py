"""Factories for synthetic researchers, runs, competitors, providers, and records."""

import itertools

from django.utils import timezone

from axisence.accounts.models import User
from axisence.ingestion.models import IngestionRun, IngestionSource
from axisence.providers import normalization
from axisence.providers.models import (
    Borough,
    Competitor,
    LocationSource,
    Provider,
    ProviderKind,
    ProviderSource,
)
from axisence.providers.parsing import ProviderRecord

_identifier_numbers = itertools.count(9000001)


def make_researcher(username="researcher"):
    return User.objects.create_user(username=username, is_staff=True)


def make_run(source=IngestionSource.NYC_HEALTH_PROVIDERS):
    return IngestionRun.objects.create(source=source, started_at=timezone.now())


def make_competitor(**fields):
    return Competitor.objects.create(**fields)


def make_provider(
    *,
    source=ProviderSource.NYC_HEALTH,
    source_identifier=None,
    original_name="Synthetic Day Care",
    street_address="100 Example Street",
    published_zip_code="11217",
    kind=ProviderKind.DAY_CARE_CENTER,
    competitor=None,
    missing_since=None,
    location=None,
    **fields,
):
    now = timezone.now()
    values = _record_values(
        source, source_identifier, original_name, street_address, published_zip_code
    )
    values.update(
        kind=kind,
        competitor=competitor,
        missing_since=missing_since,
        location=location,
        location_source=LocationSource.NONE
        if location is None
        else LocationSource.PUBLISHER,
        first_seen_at=now,
        last_seen_at=now,
    )
    values.update(fields)
    return Provider.objects.create(**values)


def provider_record(
    *,
    source=ProviderSource.NYC_HEALTH,
    source_identifier=None,
    original_name="Synthetic Day Care",
    street_address="100 Example Street",
    published_zip_code="11217",
    kind=ProviderKind.DAY_CARE_CENTER,
    **fields,
):
    values = _record_values(
        source, source_identifier, original_name, street_address, published_zip_code
    )
    values.update(kind=kind)
    values.update(fields)
    return ProviderRecord(**values)


def _record_values(
    source, source_identifier, original_name, street_address, published_zip_code
):
    number = next(_identifier_numbers)
    values = {
        "source": source,
        "source_identifier": source_identifier
        or (f"DC{number}" if source == ProviderSource.NYC_HEALTH else str(number)),
        "original_name": original_name,
        "display_name": normalization.display_name(original_name),
        "name_key": normalization.name_key(original_name),
        "street_address": street_address,
        "address_key": normalization.address_key(street_address),
        "published_zip_code": published_zip_code,
        "zip_key": normalization.zip_key(published_zip_code),
        "borough": Borough.BROOKLYN,
    }
    if source == ProviderSource.NY_STATE:
        street_number, _, street_name = street_address.partition(" ")
        values.update(
            ny_state_street_number=street_number,
            ny_state_street_name=street_name,
            ny_state_city="Brooklyn",
            ny_state_is_address_omitted=street_address == "",
            ny_state_is_phone_omitted=False,
        )
    return values
