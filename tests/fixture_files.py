"""Locate the redacted provider rows and recorded public responses in tests/fixtures."""

from pathlib import Path

FIXTURES_DIRECTORY = Path(__file__).parent / "fixtures"


def fixture_bytes(relative_path):
    return (FIXTURES_DIRECTORY / relative_path).read_bytes()
