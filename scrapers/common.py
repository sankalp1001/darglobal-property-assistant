"""Shared fetch layer: polite delays, retries with backoff, and a disk cache."""
from __future__ import annotations

import hashlib
import random
import time
from pathlib import Path

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

# An honest, self-identifying User-Agent. We do not impersonate a browser.
USER_AGENT = "property-research-bot/0.1 (take-home assignment; respects robots.txt)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en"}


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def cache_path(site: str, url: str, ext: str) -> Path:
    d = RAW_DIR / site
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{url_hash(url)}.{ext}"


class RetryableStatus(Exception):
    pass


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableStatus, requests.ConnectionError, requests.Timeout))


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


@retry(
    retry=retry_if_exception(_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _get(url: str) -> requests.Response:
    r = session().get(url, timeout=30)
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryableStatus(f"{r.status_code} for {url}")
    r.raise_for_status()
    return r


def polite_get(url: str, min_delay: float = 1.0, max_delay: float = 2.0) -> str:
    """GET with a 1-2s delay before each live request and retry on 429/5xx."""
    time.sleep(random.uniform(min_delay, max_delay))
    r = _get(url)
    r.encoding = "utf-8"
    return r.text


def fetch_cached(site: str, url: str, ext: str = "html", refresh: bool = False) -> str:
    """Return the raw body for url, from disk cache if present."""
    path = cache_path(site, url, ext)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    body = polite_get(url)
    path.write_text(body, encoding="utf-8")
    return body
