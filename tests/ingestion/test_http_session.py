"""Tests for the HTTP session every pipeline request uses."""

from requests.adapters import HTTPAdapter

from axisence.ingestion import http_session


def test_session_sends_the_pipeline_user_agent():
    session = http_session.build_session()

    assert (
        session.headers["User-Agent"]
        == "Axisence-Data-Pipeline (public data ingestion)"
    )


def test_session_retries_throttling_and_server_errors_with_backoff_and_retry_after(
    monkeypatch,
):
    # Restores the real build_session that the autouse fake transport wrapped. The
    # test only inspects the adapter and sends no request.
    monkeypatch.undo()

    adapter = http_session.build_session().get_adapter("https://")

    assert isinstance(adapter, HTTPAdapter)
    retry = adapter.max_retries
    assert retry.total == 5
    assert retry.backoff_factor == 2
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.allowed_methods == frozenset({"GET", "POST"})
    assert retry.respect_retry_after_header
    assert not retry.raise_on_status
