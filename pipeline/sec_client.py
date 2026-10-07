"""Polite, cached HTTP client for SEC endpoints.

SEC fair-access rules: at most 10 requests/second across all machines, and a
User-Agent naming a person and a contact email. We throttle to 5/s and cache
every file on disk, so reruns cost zero requests.
"""

import os
import threading
import time
from pathlib import Path

import requests

from pipeline import ROOT

MIN_INTERVAL = 0.2  # seconds between requests -> 5 req/s
# SEC answers rate-limit blocks with 403 as well as 429/503, so those are retried too.
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}

_lock = threading.Lock()
_last_request = 0.0
_session: requests.Session | None = None


def _user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "SEC_USER_AGENT":
                    ua = value.strip().strip('"').strip("'")
    if not ua or "@" not in ua:
        raise SystemExit(
            "Set SEC_USER_AGENT to 'Your Name your@email' (in .env or the environment). "
            "SEC blocks requests without a contact email."
        )
    return ua


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"})
    return _session


def _throttle() -> None:
    global _last_request
    with _lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def get(url: str, *, attempts: int = 5, timeout: float = 60) -> requests.Response:
    """GET with throttling and exponential backoff on transient errors."""
    session = _get_session()
    for attempt in range(attempts):
        _throttle()
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
        else:
            if resp.status_code not in RETRY_STATUSES:
                resp.raise_for_status()
                return resp
            if attempt == attempts - 1:
                resp.raise_for_status()
        # A 429/503 from SEC usually means a ~10 minute block; back off hard.
        time.sleep(min(2 ** attempt * 5, 120))
    raise RuntimeError("unreachable")


def fetch_to_cache(url: str, dest: Path, *, refresh: bool = False) -> tuple[Path, bool]:
    """Download url to dest unless it is already cached. Returns (path, downloaded)."""
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest, False
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = get(url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
    return dest, True
