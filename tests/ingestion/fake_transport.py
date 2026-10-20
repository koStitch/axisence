"""A requests transport that answers from registered routes and never the network."""

import io

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

DEFAULT_HEADERS = {"Content-Type": "application/octet-stream"}


class FakeTransport(BaseAdapter):
    """Answers each request from the first matching route, and records each request and
    its timeout.

    A route matches on the method, a URL prefix, and a text the URL must contain. A
    request without a matching route fails as an unreachable host would.
    """

    def __init__(self):
        super().__init__()
        self.routes = []
        self.requests = []
        self.timeouts = []

    def add(
        self, method, url_prefix, body, *, query_contains="", status=200, headers=None
    ):
        self.routes.append(
            (method.upper(), url_prefix, query_contains, body, status, headers)
        )

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        self.requests.append(request)
        self.timeouts.append(timeout)
        for method, url_prefix, query_contains, body, status, headers in self.routes:
            if (
                request.method == method
                and request.url.startswith(url_prefix)
                and query_contains in request.url
            ):
                response = requests.Response()
                response.status_code = status
                response.reason = "Fake"
                response.headers = CaseInsensitiveDict(headers or DEFAULT_HEADERS)
                response.raw = io.BytesIO(body(request) if callable(body) else body)
                response.url = request.url
                response.request = request
                return response
        raise requests.ConnectionError("No fake route.")

    def close(self):
        pass
