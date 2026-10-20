"""Load the NY State OCFS Child Care Regulated Programs into the provider table."""

from axisence.providers import pipeline
from axisence.providers.management.provider_ingestion_command import (
    ProviderIngestionCommand,
)


class Command(ProviderIngestionCommand):
    help = (
        "Fetch the NY State OCFS Child Care Regulated Programs export, then store its "
        "providers, group them into competitors, and geocode the ones without "
        "coordinates. Exits with a non-zero status when the run fails."
    )
    definition = pipeline.NY_STATE
