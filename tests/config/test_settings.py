"""Tests for the settings the live copy's environment turns on."""

import runpy

import pytest
from django.core.management import call_command

import config.settings

VARIABLE = "DJANGO_BEHIND_HTTPS_PROXY"

HTTPS_SETTINGS_ON = {
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_SSL_REDIRECT": True,
    "SESSION_COOKIE_SECURE": True,
    "CSRF_COOKIE_SECURE": True,
    "SECURE_HSTS_SECONDS": 31_536_000,
    "SECURE_HSTS_INCLUDE_SUBDOMAINS": True,
}

HTTPS_SETTINGS_OFF = {
    "SECURE_PROXY_SSL_HEADER": None,
    "SECURE_SSL_REDIRECT": False,
    "SESSION_COOKIE_SECURE": False,
    "CSRF_COOKIE_SECURE": False,
    "SECURE_HSTS_SECONDS": 0,
    "SECURE_HSTS_INCLUDE_SUBDOMAINS": False,
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", HTTPS_SETTINGS_ON),
        ("false", HTTPS_SETTINGS_OFF),
        (None, HTTPS_SETTINGS_OFF),
    ],
)
def test_behind_https_proxy_switches_every_https_setting(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(VARIABLE, raising=False)
    else:
        monkeypatch.setenv(VARIABLE, value)

    # Executes the settings module afresh, without touching the configured settings.
    settings_values = runpy.run_path(config.settings.__file__)

    assert {name: settings_values[name] for name in expected} == expected


@pytest.mark.parametrize(
    "path", ["/static/admin/css/base.css", "/static/gis/js/OLMapWidget.js"]
)
def test_collected_static_files_are_served_with_debug_off(client, path):
    # Tests run with DEBUG off, as the live copy does. The test client starts the
    # middleware at its first request, after the files are collected.
    call_command("collectstatic", interactive=False, verbosity=0)

    response = client.get(path)

    assert response.status_code == 200
    # Reading the file to its end lets the test client close the response, as a WSGI
    # server does, without the database access a direct close() would ask for.
    assert b"".join(response.streaming_content)
