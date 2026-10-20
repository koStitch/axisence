"""Load the NYC Health Active Child Care Programs into the provider table."""

from axisence.providers import pipeline
from axisence.providers.management.provider_ingestion_command import (
    ProviderIngestionCommand,
)


class Command(ProviderIngestionCommand):
    help = (
        "Fetch the NYC Health Active Child Care Programs export, then store its "
        "providers, group them into competitors, and geocode the ones without "
        "coordinates. Exits with a non-zero status when the run fails."
    )
    definition = pipeline.NYC_HEALTH
