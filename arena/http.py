"""One polite HTTP session for all collectors: identifies itself, times out, retries briefly."""
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_UA = "AI-Trading-Arena research project"


def user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "").strip() or DEFAULT_UA


def make_session(ua: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = ua or user_agent()
    # Retry only transient server errors; 4xx means "no" and we respect it.
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET",))  # writes are not retried blindly
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


TIMEOUT = 20
