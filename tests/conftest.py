"""Fixtures every test gets: no network, no real secrets, no writes into data/raw/."""

import pytest

from axisence.ingestion import http_session
from tests.ingestion.fake_transport import FakeTransport


@pytest.fixture(autouse=True)
def raw_data_directory(settings, tmp_path):
    settings.RAW_DATA_DIRECTORY = tmp_path / "raw"
    return settings.RAW_DATA_DIRECTORY


@pytest.fixture(autouse=True)
def blank_secrets(settings):
    # The container's .env may hold real values; tests set synthetic ones when needed.
    settings.CENSUS_API_KEY = ""
    settings.SOCRATA_APP_TOKEN = ""


@pytest.fixture(autouse=True)
def fake_transport(monkeypatch):
    transport = FakeTransport()
    original = http_session.build_session

    def build_session_with_fake_transport():
        session = original()
        session.mount("https://", transport)
        session.mount("http://", transport)
        return session

    monkeypatch.setattr(
        http_session, "build_session", build_session_with_fake_transport
    )
    return transport
