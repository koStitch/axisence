"""Load the ACS 5-year data profile tables of one vintage."""

from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from axisence.demographics import pipeline
from axisence.ingestion import runs


class Command(BaseCommand):
    help = (
        "Load the ACS 5-year data profile tables DP02 to DP05 for every NY State "
        "tabulation area for one vintage."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--vintage",
            type=int,
            required=True,
            metavar="YEAR",
            help="ACS 5-year vintage to load, for example 2024.",
        )

    def handle(self, *args: object, **options: object) -> None:
        run = pipeline.run_demographics_ingestion(cast("int", options["vintage"]))
        runs.report_run(self.stdout, run)
