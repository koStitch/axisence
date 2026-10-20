"""Why a report was not generated; the messages are shown to the user."""


class ReportError(Exception):
    """A generation that produced no report; its message is shown in the admin."""


class ReportRefusedError(ReportError):
    """A rule refuses the generation; the message names what to fix."""


class ReportGenerationError(ReportError):
    """An unexpected failure; its message never holds report content."""


class ExternalResourceRefusedError(Exception):
    """Rendering asked for a resource outside the report's own HTML."""
