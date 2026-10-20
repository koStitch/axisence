"""Tests for the redaction of target addresses from log records."""

import logging

from axisence.research.redaction import RedactTargetAddressesFilter


def test_the_console_filter_redacts_address_query_parameters():
    console_filters = [
        log_filter
        for handler in logging.getLogger().handlers
        for log_filter in handler.filters
        if isinstance(log_filter, RedactTargetAddressesFilter)
    ]
    # Shaped like urllib3's request line.
    record = logging.LogRecord(
        "urllib3.connectionpool",
        logging.DEBUG,
        __file__,
        1,
        '%s://%s:%s "%s %s %s" %s %s',
        (
            "https",
            "geocoding.geo.census.gov",
            443,
            "GET",
            "/geocoder/locations/onelineaddress"
            "?address=100+Example+Street&benchmark=Public_AR_Current",
            "HTTP/1.1",
            200,
            651,
        ),
        None,
    )

    assert console_filters
    assert console_filters[0].filter(record)
    assert "?address=[redacted]&benchmark=Public_AR_Current" in record.getMessage()
    assert "Example" not in record.getMessage()
