"""Load the 2020 cartographic boundaries of the NY State tabulation areas."""

from django.core.management.base import BaseCommand

from axisence.demographics import pipeline
from axisence.ingestion import runs


class Command(BaseCommand):
    help = "Load the 2020 cartographic boundaries of every NY State tabulation area."

    def handle(self, *args: object, **options: object) -> None:
        run = pipeline.run_boundaries_ingestion()
        runs.report_run(self.stdout, run)
