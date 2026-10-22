"""Fixtures every test gets: no network, no real secrets or contact details, no writes
into data/raw/, data/private/reports/, or staticfiles/.
"""

import pytest

from axisence.ingestion import http_session
from tests.ingestion.fake_transport import FakeTransport


@pytest.fixture(autouse=True)
def raw_data_directory(settings, tmp_path):
    settings.RAW_DATA_DIRECTORY = tmp_path / "raw"
    return settings.RAW_DATA_DIRECTORY


@pytest.fixture(autouse=True)
def report_directory(settings, tmp_path):
    settings.REPORT_DIRECTORY = tmp_path / "reports"
    return settings.REPORT_DIRECTORY


@pytest.fixture(autouse=True)
def static_root(settings, tmp_path):
    # WhiteNoise warns at startup when STATIC_ROOT does not exist, and collectstatic
    # must never write into the repository.
    settings.STATIC_ROOT = tmp_path / "static"
    settings.STATIC_ROOT.mkdir()
    return settings.STATIC_ROOT


@pytest.fixture(autouse=True)
def blank_secrets(settings):
    # The container's .env may hold real values; tests set synthetic ones when needed.
    settings.CENSUS_API_KEY = ""
    settings.SOCRATA_APP_TOKEN = ""


@pytest.fixture(autouse=True)
def blank_contact_settings(settings):
    # The container's .env may hold real contact details; tests set synthetic ones when
    # needed.
    settings.AXISENCE_WEBSITE_URL = ""
    settings.AXISENCE_CONTACT_EMAIL = ""
    settings.AXISENCE_CONTACT_PHONE = ""


@pytest.fixture(autouse=True)
def fake_transport(monkeypatch):
    # Both session builders are wrapped, so no session reaches the network.
    transport = FakeTransport()
    original_build_session = http_session.build_session
    original_build_lookup_session = http_session.build_lookup_session

    def build_session_with_fake_transport():
        session = original_build_session()
        session.mount("https://", transport)
        session.mount("http://", transport)
        return session

    def build_lookup_session_with_fake_transport():
        session = original_build_lookup_session()
        session.mount("https://", transport)
        session.mount("http://", transport)
        return session

    monkeypatch.setattr(
        http_session, "build_session", build_session_with_fake_transport
    )
    monkeypatch.setattr(
        http_session, "build_lookup_session", build_lookup_session_with_fake_transport
    )
    return transport
