"""Typed readers for the environment variables that configure the project.

Each read is either required or has an explicit default. A variable that is unset,
empty, or only whitespace counts as unset. A missing required variable, or a value that
cannot be parsed, raises ImproperlyConfigured naming the variable. Messages never repeat
the value, because it may be a secret.
"""

import enum
import os

from django.core.exceptions import ImproperlyConfigured


class _Required(enum.Enum):
    """The default of a read that has none: the variable must be set."""

    REQUIRED = enum.auto()


def read_string(name: str, *, default: str | _Required = _Required.REQUIRED) -> str:
    """Return the variable's value unchanged."""
    value = _read_non_blank_value(name)
    if value is None:
        return _default_or_raise(name, default)
    return value


def read_boolean(name: str, *, default: bool | _Required = _Required.REQUIRED) -> bool:
    """Return True for "true" and False for "false", in any letter case."""
    value = _read_non_blank_value(name)
    if value is None:
        return _default_or_raise(name, default)
    normalized_value = value.strip().lower()
    if normalized_value == "true":
        return True
    if normalized_value == "false":
        return False
    raise ImproperlyConfigured(
        f"The environment variable {name} must be true or false."
    )


def read_integer(name: str, *, default: int | _Required = _Required.REQUIRED) -> int:
    """Return the variable's value as an integer."""
    value = _read_non_blank_value(name)
    if value is None:
        return _default_or_raise(name, default)
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(
            f"The environment variable {name} must be an integer."
        ) from None


def read_list(
    name: str, *, default: list[str] | _Required = _Required.REQUIRED
) -> list[str]:
    """Return the comma-separated items, stripped, with empty items dropped."""
    value = _read_non_blank_value(name)
    if value is None:
        return list(_default_or_raise(name, default))
    return [item.strip() for item in value.split(",") if item.strip()]


def _read_non_blank_value(name: str) -> str | None:
    value = os.environ.get(name, "")
    if value.strip() == "":
        return None
    return value


def _default_or_raise[T](name: str, default: T | _Required) -> T:
    if isinstance(default, _Required):
        raise ImproperlyConfigured(
            f"The environment variable {name} is required but is not set."
        )
    return default
