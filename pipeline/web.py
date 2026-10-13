"""Polite, cached fetcher for company homepages (not SEC).

One GET per page, robots.txt respected, a User-Agent naming the project and a contact,
short timeouts, a 1.5 MB cap, and every result (including failures) cached on disk so
reruns cost nothing. Pages are parsed with the standard library only.
"""

import gzip
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha1
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from pipeline import CACHE_DIR
from pipeline.sec_client import _user_agent

PAGE_DIR = CACHE_DIR / "web" / "pages"
ROBOTS_DIR = CACHE_DIR / "web" / "robots"
MAX_BYTES = 1_500_000
TIMEOUT = (5, 10)
REFRESH_DAYS = 90

_session = requests.Session()
_session.headers.update({"Accept": "text/html,application/xhtml+xml",
                         "Accept-Language": "en-US,en;q=0.8"})


def user_agent() -> str:
    # Same contact as the SEC client: "Name email". Identify the project too.
    return f"FormD-Tracker/0.1 (research; {_user_agent()})"


def _cache_path(folder, key: str):
    return folder / f"{sha1(key.encode()).hexdigest()[:20]}.json.gz"


def _read_cache(path):
    if not path.exists():
        return None
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, ValueError):
        return None  # truncated or corrupt: treat as a miss and refetch
    if time.time() - data.get("fetched_at", 0) > REFRESH_DAYS * 86400:
        return None
    return data


def _write_cache(path, data) -> None:
    # Write to a unique temp file, then rename: two threads checking the same domain
    # (common for guessed names) must never see each other's half-written file.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(gzip.compress(json.dumps(data).encode()))
    os.replace(tmp, path)


# A hostname DNS can resolve: labels of 1-63 letters, digits or hyphens, 253 chars total.
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def valid_domain(domain: str) -> bool:
    return bool(_HOST.match(domain or ""))


def robots_allows(domain: str) -> bool:
    path = _cache_path(ROBOTS_DIR, domain)
    cached = _read_cache(path)
    if cached is None:
        try:
            resp = _session.get(f"https://{domain}/robots.txt", timeout=TIMEOUT,
                                headers={"User-Agent": user_agent()}, allow_redirects=True)
            body = resp.text[:200_000] if resp.status_code == 200 else ""
        except Exception:  # noqa: BLE001
            body = ""  # unreachable robots.txt: treat as no restrictions
        cached = {"fetched_at": time.time(), "body": body}
        _write_cache(path, cached)
    parser = RobotFileParser()
    parser.parse(cached["body"].splitlines())
    return parser.can_fetch("FormD-Tracker", f"https://{domain}/")


def fetch(domain: str) -> dict:
    """Homepage of domain -> {status, final_url, html, error}. Cached, failures included."""
    path = _cache_path(PAGE_DIR, domain)
    cached = _read_cache(path)
    if cached is not None:
        return cached
    result = {"domain": domain, "fetched_at": time.time(), "status": None,
              "final_url": None, "html": "", "error": None}
    if not valid_domain(domain):
        result["error"] = "invalid domain"
        return result
    if not robots_allows(domain):
        result["error"] = "robots.txt disallows"
    else:
        try:
            with _session.get(f"https://{domain}/", timeout=TIMEOUT, stream=True,
                              headers={"User-Agent": user_agent()}, allow_redirects=True) as r:
                result["status"] = r.status_code
                result["final_url"] = r.url
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and "html" in ctype:
                    body = b""
                    for chunk in r.iter_content(65536):
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    result["html"] = body.decode(r.encoding or "utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - one bad site must never stop a run
            result["error"] = type(exc).__name__
    _write_cache(path, result)
    return result


@dataclass
class Page:
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def hero(self) -> str:
        """What a company says about itself up front: title, description, top headings."""
        return " | ".join([self.title, self.description, *self.headings[:3]])


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._skip = 0
        self._in_title = False
        self._heading: list[str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2"):
            self._heading = []
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("name", "").lower() == "description" or a.get("property", "").lower() in (
                    "og:description", "og:title"):
                if a.get("content") and a["content"] not in self.page.description:
                    self.page.description = (self.page.description + " " + a["content"]).strip()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2") and self._heading is not None:
            h = " ".join("".join(self._heading).split())
            if h:
                self.page.headings.append(h)
            self._heading = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.page.title += data
        if self._heading is not None:
            self._heading.append(data)
        self._text.append(data)

    def close(self):
        super().close()
        self.page.title = " ".join(self.page.title.split())
        self.page.text = " ".join(" ".join(self._text).split())


def parse_page(html: str) -> Page:
    ex = _Extractor()
    try:
        ex.feed(html)
        ex.close()
    except Exception:  # noqa: BLE001 - malformed HTML: keep what was parsed
        pass
    return ex.page


PARKED = re.compile(
    r"(?i)(domain (is )?for sale|buy this domain|this domain (name )?(is|may be) for sale|"
    r"hugedomains|sedo\b|dan\.com|afternic|parked (free|domain)|godaddy\.com/domainsearch|"
    r"domain has expired|coming soon|under construction)")


def final_domain(result: dict) -> str | None:
    if not result.get("final_url"):
        return None
    host = urlparse(result["final_url"]).hostname or ""
    return host.removeprefix("www.")
