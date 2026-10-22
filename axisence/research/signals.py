"""Signals through which other apps add rules to research jobs, so research never
imports them.
"""

from django.dispatch import Signal

# Sent by jobs.change_status while the stored job row is locked, after research's own
# checks and before the new status is stored, with the keyword arguments job (the
# stored job, still in its current status) and status (the target JobStatus). A receiver
# refuses the change by raising jobs.ResearchJobError.
job_status_changing = Signal()
