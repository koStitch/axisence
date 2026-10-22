"""Lines of repeat jobs: which repeat job that is not Cancelled follows each job, loaded
in one query.
"""

from collections.abc import Mapping

from axisence.research.models import JobStatus, ResearchJob


class RepeatLines:
    """Where every job's open repeats lead, loaded in one query."""

    def __init__(self, open_repeat_id_by_job_id: Mapping[int, int]) -> None:
        self._open_repeat_id_by_job_id = dict(open_repeat_id_by_job_id)

    @classmethod
    def load(cls) -> RepeatLines:
        # The partial unique constraint keeps one open repeat per job.
        return cls(
            {
                predecessor_id: repeat_id
                for predecessor_id, repeat_id in ResearchJob.objects.filter(
                    predecessor__isnull=False
                )
                .exclude(status=JobStatus.CANCELLED)
                .values_list("predecessor_id", "pk")
                if predecessor_id is not None
            }
        )

    def open_repeat_id(self, job_id: int) -> int | None:
        """Return the repeat job that is not Cancelled and follows the job, if any."""
        return self._open_repeat_id_by_job_id.get(job_id)

    def line_from(self, job_id: int) -> tuple[int, ...]:
        """Return the job and every job reached by following open repeats, in order."""
        line = [job_id]
        # A line visits each repeat job at most once.
        for _ in range(len(self._open_repeat_id_by_job_id) + 1):
            repeat_id = self._open_repeat_id_by_job_id.get(line[-1])
            if repeat_id is None:
                return tuple(line)
            line.append(repeat_id)
        raise RuntimeError(f"The line of research job {job_id} is broken.")
