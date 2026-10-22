"""Load every subway station and bus stop of the MTA feeds, with their lines."""

from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from axisence.ingestion import runs
from axisence.transit import pipeline


class Command(BaseCommand):
    help = (
        "Fetch the seven MTA static GTFS feeds when the MTA has republished them, "
        "then store every subway station and bus stop with the lines that stop there "
        "on weekday daytime service. Exits with a non-zero status when the run fails."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--from-latest-snapshots",
            action="store_true",
            help=(
                "Read every feed's latest saved snapshot instead of requesting the "
                "feeds."
            ),
        )
        # argparse formats help with %, so a literal percent sign is doubled.
        parser.add_argument(
            "--allow-mass-disappearance",
            action="store_true",
            help=(
                "Let the run mark more than 10%% of either mode's active stops "
                "inactive."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        run = pipeline.run_transit_ingestion(
            from_latest_snapshots=cast("bool", options["from_latest_snapshots"]),
            allow_mass_disappearance=cast("bool", options["allow_mass_disappearance"]),
        )
        runs.report_run(self.stdout, run)
        pipeline.write_feed_outcomes(self.stdout, run)
