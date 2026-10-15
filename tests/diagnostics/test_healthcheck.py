"""Tests for the healthcheck management command."""

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

# A report line: the label, then a version made of dot-separated numbers.
VERSION_LINE = r"^{label}: \d+(\.\d+)+$"


@pytest.mark.django_db
def test_healthcheck_reports_every_check_and_never_the_password(monkeypatch):
    # The open test connection keeps its real credentials; the command only reads the
    # settings, so a sentinel there proves the password is never printed.
    sentinel = "sentinel-value-for-the-healthcheck-test"
    monkeypatch.setitem(connection.settings_dict, "PASSWORD", sentinel)
    stdout = StringIO()
    stderr = StringIO()

    # Returning normally is exit status 0 under manage.py.
    call_command("healthcheck", stdout=stdout, stderr=stderr)

    output = stdout.getvalue()
    database = connection.settings_dict
    target = (
        f"Database: {database['NAME']}@{database['HOST']}:{database['PORT']}"
        f" (user {database['USER']})"
    )
    assert output.startswith(target + "\n")
    for label in ("PostgreSQL", "PostGIS", "GDAL", "GEOS"):
        assert re.search(VERSION_LINE.format(label=label), output, re.MULTILINE)
    assert output.endswith("All checks passed.\n")
    assert stderr.getvalue() == ""
    assert sentinel not in output


def test_healthcheck_reports_a_failed_check_and_still_runs_the_others():
    # Without the django_db mark, pytest-django refuses database access, which the
    # command meets exactly as it would meet an unreachable database.
    stdout = StringIO()
    stderr = StringIO()

    with pytest.raises(CommandError) as raised:
        call_command("healthcheck", stdout=stdout, stderr=stderr)

    # manage.py exits with this status.
    assert raised.value.returncode != 0
    assert re.search(r"^PostgreSQL: failed: ", stderr.getvalue(), re.MULTILINE)
    assert re.search(r"^PostGIS: failed: ", stderr.getvalue(), re.MULTILINE)
    for label in ("GDAL", "GEOS"):
        assert re.search(
            VERSION_LINE.format(label=label), stdout.getvalue(), re.MULTILINE
        )
    assert "All checks passed." not in stdout.getvalue()
