"""The HTTP sessions every outbound request uses: the pipeline's retrying session and
the research tool's single-attempt lookup session."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

USER_AGENT = "Axisence-Data-Pipeline (public data ingestion)"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 120
TIMEOUT = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
RETRY_ATTEMPTS = 5
RETRY_BACKOFF_FACTOR = 2
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def build_session() -> requests.Session:
    """Return a session that retries throttled and failed requests with backoff."""
    retry = Retry(
        total=RETRY_ATTEMPTS,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_CODES,
        # The batch geocoder is a POST; repeating it is safe because it only looks up.
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    return session


LOOKUP_USER_AGENT = "Axisence-Research-Tool (target address lookup)"
LOOKUP_CONNECT_TIMEOUT_SECONDS = 5
LOOKUP_READ_TIMEOUT_SECONDS = 10
LOOKUP_TIMEOUT = (LOOKUP_CONNECT_TIMEOUT_SECONDS, LOOKUP_READ_TIMEOUT_SECONDS)


def build_lookup_session() -> requests.Session:
    """Return a session for interactive lookups that tries each request once.

    A researcher waits on the page, so a failure is reported at once instead of
    retried with backoff.
    """
    adapter = HTTPAdapter(max_retries=Retry(total=0, read=False, raise_on_status=False))
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = LOOKUP_USER_AGENT
    return session
