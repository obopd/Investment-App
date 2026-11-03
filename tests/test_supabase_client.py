from arena.db.client import SupabaseDB
from fakes import FakeResponse


class Recorder:
    def __init__(self):
        self.headers, self.calls = {}, []

    def _rec(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = FakeResponse(200, "[]", url)
        r.request = type("Req", (), {"method": method})()
        return r

    def get(self, url, **kw):
        return self._rec("GET", url, **kw)

    def post(self, url, **kw):
        return self._rec("POST", url, **kw)

    def patch(self, url, **kw):
        return self._rec("PATCH", url, **kw)


def test_new_secret_keys_skip_bearer_and_legacy_jwts_use_it():
    assert "Authorization" not in SupabaseDB("https://x.supabase.co", "sb_secret_abc", Recorder()).s.headers
    assert SupabaseDB("https://x.supabase.co", "eyJhbGc", Recorder()).s.headers["Authorization"] == "Bearer eyJhbGc"


def test_upsert_request_shape_and_chunking():
    rec = Recorder()
    db = SupabaseDB("https://x.supabase.co/", "sb_secret_abc", rec)
    db.upsert("articles", [{"url": str(i)} for i in range(1200)], on_conflict="url", ignore_duplicates=True)
    assert len(rec.calls) == 3
    method, url, kw = rec.calls[0]
    assert url == "https://x.supabase.co/rest/v1/articles"
    assert kw["params"] == {"on_conflict": "url"}
    assert kw["headers"]["Prefer"] == "resolution=ignore-duplicates,return=minimal"


def test_select_builds_filters():
    rec = Recorder()
    SupabaseDB("https://x.supabase.co", "k", rec).select(
        "prices_daily", {"ticker": "AAPL", "date": ("gte", "2026-01-01")}, columns="date", order="date.desc", limit=1)
    assert rec.calls[0][2]["params"] == {"select": "date", "ticker": "eq.AAPL", "date": "gte.2026-01-01",
                                         "order": "date.desc", "limit": "1"}


def test_url_with_rest_path_is_accepted():
    for url in ("https://x.supabase.co", "https://x.supabase.co/", "https://x.supabase.co/rest/v1/"):
        assert SupabaseDB(url, "k", Recorder()).base == "https://x.supabase.co/rest/v1"
