"""Factories for synthetic research jobs, located competitors, contacts, and facts."""

from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.utils import timezone

from axisence.providers.models import Borough, ProviderKind
from axisence.research.models import (
    RESEARCHERS_GROUP_NAME,
    Contact,
    ContactMethod,
    JobPurpose,
    JobStatus,
    Observation,
    ResearchJob,
    TargetPointSource,
)
from tests.providers.factories import make_competitor, make_provider, make_researcher

# The recorded geocoder match for Brooklyn Borough Hall, a public building.
BOROUGH_HALL = Point(-73.990738135081, 40.692568162618, srid=4326)

METERS_PER_MILE = 1609.344
# Meters per degree of latitude near New York, accurate to well under one percent.
METERS_PER_DEGREE_OF_LATITUDE = 111_050


def point_north_of(origin, miles):
    """Return a point due north of the origin; tests keep clear of radius edges."""
    return Point(
        origin.x,
        origin.y + miles * METERS_PER_MILE / METERS_PER_DEGREE_OF_LATITUDE,
        srid=4326,
    )


def make_researcher_in_group(username="researcher"):
    researcher = make_researcher(username)
    researcher.groups.add(Group.objects.get(name=RESEARCHERS_GROUP_NAME))
    return researcher


def make_located_competitor(
    point,
    *,
    name="Synthetic Day Care",
    kind=ProviderKind.DAY_CARE_CENTER,
    borough=Borough.BROOKLYN,
    **provider_fields,
):
    competitor = make_competitor(
        display_name=name,
        street_address="100 Example Street",
        borough=borough,
        location=point,
    )
    make_provider(
        original_name=name,
        kind=kind,
        competitor=competitor,
        location=point,
        borough=borough,
        **provider_fields,
    )
    return competitor


def make_job(
    *, researcher, point=None, confirmed=False, status=JobStatus.DRAFT, **fields
):
    values = {
        "purpose": JobPurpose.CLIENT_REPORT,
        "organization_name": "Synthetic Organization",
        "target_address": "100 Example Street, Brooklyn, NY 11217",
        "created_by": researcher,
        "status": status,
        "target_point": point,
        "target_point_source": "" if point is None else TargetPointSource.GEOCODER,
    }
    if confirmed:
        values.update(
            target_point_confirmed_at=timezone.now(),
            target_point_confirmed_by=researcher,
        )
    values.update(fields)
    return ResearchJob.objects.create(**values)


def make_contact(
    *,
    competitor,
    researcher,
    contact_date=None,
    entered_at=None,
    job=None,
    **fields,
):
    values = {
        "competitor": competitor,
        "researcher": researcher,
        "contact_date": contact_date or timezone.localdate(),
        "entered_at": entered_at or timezone.now(),
        "job": job,
        "method": ContactMethod.CALL,
    }
    values.update(fields)
    return Contact.objects.create(**values)


def make_observation(*, contact, kind, **values):
    return Observation.objects.create(contact=contact, kind=kind, **values)
