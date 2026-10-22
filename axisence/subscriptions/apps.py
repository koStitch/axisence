from django.apps import AppConfig

from axisence.research.signals import job_status_changing


class SubscriptionsConfig(AppConfig):
    name = "axisence.subscriptions"
    verbose_name = "Subscriptions"

    def ready(self) -> None:
        # Imported here, because models load only once the app registry is ready.
        from axisence.research.models import ResearchJob
        from axisence.subscriptions.repeats import refuse_reopening_a_served_due_date

        job_status_changing.connect(
            refuse_reopening_a_served_due_date,
            sender=ResearchJob,
            dispatch_uid="subscriptions.refuse_reopening_a_served_due_date",
        )
