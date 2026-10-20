"""Remove research target addresses from log records.

urllib3 logs the single-address geocoder's request URL, whose address query parameter
holds the target address. Settings load this module during django.setup(), before the
app registry is ready, so it imports only the standard library and REDACTED.
"""

import logging
import re

from axisence.ingestion.redaction import REDACTED

_ADDRESS_QUERY_PARAMETER = re.compile(r"(?i)([?&]address=)[^&\s'\"]+")


def redact_target_addresses(text: str) -> str:
    """Return the text with every address query parameter value removed."""
    return _ADDRESS_QUERY_PARAMETER.sub(lambda match: match.group(1) + REDACTED, text)


class RedactTargetAddressesFilter(logging.Filter):
    """Logging filter that removes target addresses from each record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_target_addresses(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
