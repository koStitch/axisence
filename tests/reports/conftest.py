"""Fixtures of the report and teaser tests."""

import pytest

from tests.reports.factories import set_contact_settings


@pytest.fixture
def contact_settings(settings):
    set_contact_settings(settings)
    return settings
