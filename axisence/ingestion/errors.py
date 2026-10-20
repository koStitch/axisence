"""The error that fails an ingestion run with a message for the user."""


class IngestionError(Exception):
    """A structural problem or a refused run.

    Its message is stored on the run record and shown to the user, so it never holds a
    secret or personal data.
    """
