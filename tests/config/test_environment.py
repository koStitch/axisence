"""Tests for the environment-variable readers."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import environment

VARIABLE = "AXISENCE_TEST_VARIABLE"

ALL_READERS = [
    environment.read_string,
    environment.read_boolean,
    environment.read_integer,
    environment.read_list,
    environment.read_https_url,
]


def _set_variable(monkeypatch, value):
    """Set the test variable, or remove it when the value is None."""
    if value is None:
        monkeypatch.delenv(VARIABLE, raising=False)
    else:
        monkeypatch.setenv(VARIABLE, value)


@pytest.mark.parametrize("read", ALL_READERS)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_variable_that_is_unset_or_blank_raises(monkeypatch, read, value):
    _set_variable(monkeypatch, value)

    with pytest.raises(ImproperlyConfigured, match=VARIABLE):
        read(VARIABLE)


@pytest.mark.parametrize(
    ("read", "default"),
    [
        (environment.read_string, "fallback"),
        (environment.read_boolean, True),
        (environment.read_integer, 5432),
        (environment.read_list, ["localhost"]),
        (environment.read_https_url, "https://axisence.example"),
    ],
)
@pytest.mark.parametrize("value", [None, ""])
def test_default_applies_when_variable_is_unset_or_blank(
    monkeypatch, read, default, value
):
    _set_variable(monkeypatch, value)

    assert read(VARIABLE, default=default) == default


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("True", True), ("FALSE", False), (" false ", False)],
)
def test_boolean_parses(monkeypatch, value, expected):
    monkeypatch.setenv(VARIABLE, value)

    assert environment.read_boolean(VARIABLE) is expected


@pytest.mark.parametrize(
    ("value", "expected"), [("5432", 5432), (" 15 ", 15), ("-1", -1)]
)
def test_integer_parses(monkeypatch, value, expected):
    monkeypatch.setenv(VARIABLE, value)

    assert environment.read_integer(VARIABLE) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost", ["localhost"]),
        ("localhost,127.0.0.1", ["localhost", "127.0.0.1"]),
        (" localhost , 127.0.0.1 , ", ["localhost", "127.0.0.1"]),
    ],
)
def test_list_parses(monkeypatch, value, expected):
    monkeypatch.setenv(VARIABLE, value)

    assert environment.read_list(VARIABLE) == expected


def test_https_url_parses(monkeypatch):
    monkeypatch.setenv(VARIABLE, " https://axisence.example/previews ")

    assert environment.read_https_url(VARIABLE) == "https://axisence.example/previews"


@pytest.mark.parametrize(
    ("read", "value"),
    [
        (environment.read_boolean, "yes"),
        (environment.read_boolean, "1"),
        (environment.read_integer, "five"),
        (environment.read_integer, "5.5"),
        (environment.read_https_url, "http://axisence.example"),
        (environment.read_https_url, "axisence.example"),
    ],
)
def test_invalid_value_raises_without_repeating_the_value(monkeypatch, read, value):
    monkeypatch.setenv(VARIABLE, value)

    with pytest.raises(ImproperlyConfigured, match=VARIABLE) as raised:
        read(VARIABLE)

    assert value not in str(raised.value)
