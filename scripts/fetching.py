"""Shared, polite HTTP: rate limiting, caching, robots.txt, readable text extraction.

Used by both discover.py and extract.py. Import-safe with no network at import time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.robotparser
from typing import Optional
from urllib.parse import urlparse

REPO_URL = "https://github.com/AkibMahdi/Checking_Account_Bonus_Automation"
USER_AGENT = f"bank-bonus-planner/1.0 (+{REPO_URL}; open-source bonus tracker)"
MIN_INTERVAL_SECONDS = 3.0          # ~1 request / 3s per host
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "cache")

_last_request: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _throttle(host: str) -> None:
    last = _last_request.get(host)
    if last is not None:
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request[host] = time.monotonic()


def robots_allows(url: str) -> bool:
    """True if robots.txt permits our User-Agent. Fail open only on network errors."""
    host = _host(url)
    if not host:
        return False
    parser = _robots.get(host)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        scheme = urlparse(url).scheme or "https"
        parser.set_url(f"{scheme}://{host}/robots.txt")
        try:
            parser.read()
        except Exception:
            parser = None
        _robots[host] = parser
    if parser is None:
        return True
    try:
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".json")


def _read_cache(url: str) -> dict:
    try:
        with open(_cache_path(url)) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(url: str, entry: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(url) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(entry, fh)
    os.replace(tmp, _cache_path(url))


def fetch(url: str, *, use_cache: bool = True, timeout: float = 25.0,
          ignore_robots: bool = False) -> Optional[str]:
    """Fetch a URL politely. Returns HTML, or None if blocked/unchanged/failed.

    Sends If-None-Match / If-Modified-Since from cache; a 304 returns the cached body.
    """
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required for fetching: pip install -r requirements.txt") from exc

    if not ignore_robots and not robots_allows(url):
        print(f"  robots.txt disallows {url} — skipping")
        return None

    cached = _read_cache(url) if use_cache else {}
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]

    _throttle(_host(url))
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        print(f"  fetch failed {url}: {type(exc).__name__}")
        return None

    if resp.status_code == 304 and cached.get("body"):
        return cached["body"]
    if resp.status_code >= 400:
        print(f"  HTTP {resp.status_code} for {url}")
        return None

    body = resp.text
    _write_cache(url, {
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
        "body": body,
        "fetched_at": time.time(),
    })
    return body


def fetch_rendered(url: str, timeout: float = 30.0) -> Optional[str]:
    """Escalation path for JS-rendered pages. Requires Playwright; returns None if absent."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed — cannot render JS page")
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        print(f"  render failed {url}: {type(exc).__name__}")
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n\s*\n+")
_DROP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe")


def clean_text(html: str) -> str:
    """Readability-style: strip chrome, keep the offer prose. BeautifulSoup if available."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = html
        for tag in _DROP_TAGS:
            text = re.sub(rf"<{tag}\b.*?</{tag}>", " ", text, flags=re.S | re.I)
        text = _TAG_RE.sub("\n", text)
    else:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text("\n")

    import html as html_mod
    text = html_mod.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    """Hash of normalised text. Whitespace and case changes must not churn the hash."""
    normalised = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()
