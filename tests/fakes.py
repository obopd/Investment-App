"""Offline stand-ins for HTTP so tests never touch the network."""
import json as _json


class FakeResponse:
    def __init__(self, status=200, body=b"", url=""):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.url = url

    def json(self):
        return _json.loads(self.text)


class FakeSession:
    """Routes GET requests by URL prefix. Unrouted URLs return 404."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.headers = {"User-Agent": "test-agent test@example.com"}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        for prefix, resp in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                r = resp(url, params) if callable(resp) else resp
                r.url = url
                return r
        return FakeResponse(404, b"not found", url)


class FakePriceProvider:
    """Serves bars from a dict {ticker: [rows]} filtered by date range."""
    name = "fake"

    def __init__(self, bars: dict):
        self.bars = bars
        self.calls = []

    def fetch(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        return [dict(r) for r in self.bars.get(ticker, [])
                if start.isoformat() <= r["date"] <= end.isoformat()]
