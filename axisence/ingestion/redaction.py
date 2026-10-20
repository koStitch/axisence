"""Remove secrets from text before it is logged, stored, or shown."""

import logging
import re

from django.conf import settings

REDACTED = "[redacted]"

_KEY_QUERY_PARAMETER = re.compile(r"(?i)([?&]key=)[^&\s'\"]+")


def redact_secrets(text: str) -> str:
    """Return the text with every key query parameter and configured secret removed."""
    redacted = _KEY_QUERY_PARAMETER.sub(lambda match: match.group(1) + REDACTED, text)
    for secret in (settings.CENSUS_API_KEY, settings.SOCRATA_APP_TOKEN):
        if secret.strip():
            redacted = redacted.replace(secret, REDACTED)
    return redacted


class RedactSecretsFilter(logging.Filter):
    """Logging filter that redacts secrets from each record's formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
