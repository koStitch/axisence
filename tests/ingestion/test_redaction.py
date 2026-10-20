"""Tests for the redaction of secrets from messages and log records."""

import logging

from axisence.ingestion.redaction import RedactSecretsFilter, redact_secrets

SYNTHETIC_CENSUS_VALUE = "synthetic-census-value"
SYNTHETIC_SOCRATA_APP_VALUE = "synthetic-socrata-app-value"


def test_redact_secrets_removes_key_query_parameters_and_configured_secrets(settings):
    settings.CENSUS_API_KEY = SYNTHETIC_CENSUS_VALUE
    settings.SOCRATA_APP_TOKEN = SYNTHETIC_SOCRATA_APP_VALUE
    text = (
        "500 for url: /data?get=NAME&key=another-value&for=x; "
        f"header {SYNTHETIC_SOCRATA_APP_VALUE}; body {SYNTHETIC_CENSUS_VALUE}"
    )

    redacted = redact_secrets(text)

    assert redacted == (
        "500 for url: /data?get=NAME&key=[redacted]&for=x; "
        "header [redacted]; body [redacted]"
    )


def test_log_records_are_redacted_by_the_console_filter():
    console_filters = [
        log_filter
        for handler in logging.getLogger().handlers
        for log_filter in handler.filters
        if isinstance(log_filter, RedactSecretsFilter)
    ]
    record = logging.LogRecord(
        "urllib3.connectionpool",
        logging.WARNING,
        __file__,
        1,
        "Retrying after connection broken: %s",
        ("/data?get=NAME&key=synthetic-value",),
        None,
    )

    assert console_filters
    assert console_filters[0].filter(record)
    assert record.getMessage() == (
        "Retrying after connection broken: /data?get=NAME&key=[redacted]"
    )
