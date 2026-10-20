"""End-to-end tests of the Census demographics command, with recorded responses."""

import logging
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from axisence.demographics.census_api import group_labels_url, profile_url
from axisence.demographics.models import (
    CensusFigure,
    CensusTable,
    CensusVariable,
    TabulationArea,
)
from axisence.ingestion.models import IngestionOutcome, IngestionRun
from tests.fixture_files import fixture_bytes

SYNTHETIC_CENSUS_VALUE = "synthetic-census-value"
JSON_HEADERS = {"Content-Type": "application/json;charset=utf-8"}
HTML_HEADERS = {"Content-Type": "text/html"}


@pytest.fixture
def census_key(settings):
    settings.CENSUS_API_KEY = SYNTHETIC_CENSUS_VALUE


def _serve_vintage(fake_transport, vintage, *, status=200, headers=JSON_HEADERS):
    for table in CensusTable:
        fake_transport.add(
            "GET",
            f"{profile_url(vintage)}?",
            fixture_bytes(f"census/acs5-profile-2024-{table.value}.json"),
            query_contains=f"group%28{table.value}%29",
            status=status,
            headers=headers,
        )
        fake_transport.add(
            "GET",
            group_labels_url(vintage, table),
            fixture_bytes(f"census/acs5-profile-2024-{table.value}-variables.json"),
            status=status,
            headers=headers,
        )


def _run(vintage=2024):
    stdout = StringIO()
    call_command(
        "ingest_census_demographics",
        "--vintage",
        str(vintage),
        stdout=stdout,
        stderr=StringIO(),
    )
    return stdout.getvalue()


def _figure(area_code, variable_code, vintage=2024):
    return CensusFigure.objects.get(
        tabulation_area__code=area_code,
        variable__code=variable_code,
        variable__vintage=vintage,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_demographics_command_stores_figures_labels_and_tabulation_areas(
    fake_transport,
):
    _serve_vintage(fake_transport, 2024)

    output = _run()

    run = IngestionRun.objects.get()
    unavailable = _figure("06390", "DP03_0067")
    capped = _figure("11201", "DP04_0101")
    assert run.outcome == IngestionOutcome.SUCCEEDED
    assert list(TabulationArea.objects.values_list("code", flat=True)) == [
        "06390",
        "11201",
        "11217",
    ]
    assert CensusVariable.objects.filter(vintage=2024).count() == 12
    assert CensusFigure.objects.count() == 36
    assert (unavailable.estimate, unavailable.estimate_margin_of_error) == (None, None)
    assert capped.estimate == Decimal(4001)
    assert capped.estimate_margin_of_error is None
    assert _figure("06390", "DP05_0032").estimate is None
    assert _figure("11201", "DP02_0016").estimate == Decimal("1.96")
    assert CensusVariable.objects.get(code="DP05_0005").estimate_label == (
        "Estimate!!SEX AND AGE!!Total population!!Under 5 years"
    )
    assert run.snapshots.count() == 8
    # Three areas in each of the four tables.
    assert (run.rows_read_count, run.created_count, run.field_warning_count) == (
        12,
        36,
        0,
    )
    assert output.startswith(f"Census demographics run {run.pk} succeeded.\n")
    assert "Vintage: 2024\n" in output


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_rerunning_a_vintage_updates_in_place(fake_transport):
    _serve_vintage(fake_transport, 2024)
    _run()

    _run()

    run = IngestionRun.objects.latest("started_at")
    assert (run.created_count, run.updated_count, run.unchanged_count) == (0, 0, 36)
    assert CensusFigure.objects.count() == 36


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_vintages_are_kept_side_by_side(fake_transport):
    _serve_vintage(fake_transport, 2024)
    _serve_vintage(fake_transport, 2023)

    _run(2024)
    _run(2023)

    assert CensusVariable.objects.filter(vintage=2024).count() == 12
    assert CensusVariable.objects.filter(vintage=2023).count() == 12
    assert CensusFigure.objects.count() == 72
    assert TabulationArea.objects.count() == 3


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_census_key_travels_only_as_the_key_parameter_and_is_never_stored_or_logged(
    fake_transport, caplog
):
    caplog.set_level(logging.DEBUG)
    _serve_vintage(fake_transport, 2024)

    _run()

    run = IngestionRun.objects.get()
    assert len(fake_transport.requests) == 8
    for request in fake_transport.requests:
        assert f"key={SYNTHETIC_CENSUS_VALUE}" in request.url
        assert all(
            SYNTHETIC_CENSUS_VALUE not in value for value in request.headers.values()
        )
    assert SYNTHETIC_CENSUS_VALUE not in run.failure_message
    for snapshot in run.snapshots.all():
        assert SYNTHETIC_CENSUS_VALUE not in snapshot.path
    assert SYNTHETIC_CENSUS_VALUE not in caplog.text


@pytest.mark.django_db
def test_missing_census_key_stops_before_any_request(fake_transport):
    _serve_vintage(fake_transport, 2024)

    with pytest.raises(CommandError, match="CENSUS_API_KEY is not set"):
        _run()

    run = IngestionRun.objects.get()
    assert fake_transport.requests == []
    assert run.outcome == IngestionOutcome.FAILED
    assert run.census_vintage == 2024


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_rejected_key_fails_the_run(fake_transport):
    _serve_vintage(fake_transport, 2024, headers=HTML_HEADERS)

    with pytest.raises(CommandError, match="rejected the request"):
        _run()

    assert IngestionRun.objects.get().outcome == IngestionOutcome.FAILED
    assert not CensusFigure.objects.exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_invalid_json_response_fails_the_run(fake_transport):
    # Registered first, so it answers the DP02 data request.
    fake_transport.add(
        "GET",
        f"{profile_url(2024)}?",
        b"[not json",
        query_contains="group%28DP02%29",
        headers=JSON_HEADERS,
    )
    _serve_vintage(fake_transport, 2024)

    with pytest.raises(CommandError, match=r"DP02\.json is not valid JSON"):
        _run()

    assert IngestionRun.objects.get().outcome == IngestionOutcome.FAILED
    assert not CensusFigure.objects.exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("census_key")
def test_server_error_message_never_contains_the_key(fake_transport):
    _serve_vintage(fake_transport, 2024, status=500)

    with pytest.raises(CommandError) as raised:
        _run()

    run = IngestionRun.objects.get()
    assert run.failure_message.startswith("The Census Data API request failed: 500")
    assert "key=[redacted]" in run.failure_message
    assert SYNTHETIC_CENSUS_VALUE not in run.failure_message
    assert SYNTHETIC_CENSUS_VALUE not in str(raised.value)
