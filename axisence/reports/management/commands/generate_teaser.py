"""Generate the teaser, the one-page market preview, of one prospect research job."""

from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from axisence.reports import generation
from axisence.reports.errors import ReportError
from axisence.research.models import ResearchJob


class Command(BaseCommand):
    help = (
        "Generate the teaser, the one-page market preview, of one prospect research "
        "job."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "job_id", type=int, metavar="JOB_ID", help="Number of the research job."
        )

    def handle(self, *args: object, **options: object) -> None:
        job_id = cast("int", options["job_id"])
        job = ResearchJob.objects.filter(pk=job_id).first()
        if job is None:
            raise CommandError(f"Research job {job_id} does not exist.")
        try:
            teaser = generation.generate_teaser(job=job, generated_by=None)
        except ReportError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f"Teaser #{teaser.pk} generated: {teaser.file_name}")
