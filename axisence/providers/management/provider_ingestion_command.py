"""The options and behaviour both provider ingestion commands share."""

from typing import ClassVar, cast

from django.core.management.base import BaseCommand, CommandParser

from axisence.ingestion import runs
from axisence.providers import pipeline


class ProviderIngestionCommand(BaseCommand):
    """Ingest the source a subclass names, then report the run or fail."""

    definition: ClassVar[pipeline.ProviderSourceDefinition]

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--snapshot",
            metavar="PATH",
            help=(
                "Re-ingest this existing snapshot under the source's data/raw "
                "directory instead of fetching."
            ),
        )
        # argparse formats help with %, so a literal percent sign is doubled.
        parser.add_argument(
            "--allow-mass-disappearance",
            action="store_true",
            help=(
                "Let the run mark more than 10%% of the source's active providers "
                "missing."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        run = pipeline.run_provider_ingestion(
            self.definition,
            snapshot_argument=cast("str | None", options["snapshot"]),
            allow_mass_disappearance=cast("bool", options["allow_mass_disappearance"]),
        )
        runs.report_run(self.stdout, run)
