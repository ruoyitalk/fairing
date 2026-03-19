import re
import socket
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser

from .config import RssSource

logger = logging.getLogger(__name__)

_FEED_TIMEOUT = 20   # seconds per attempt
_FEED_RETRIES = 2    # retry count after first failure
_RETRY_DELAY  = 4    # seconds between retries

# arXiv RSS abstracts start with boilerplate like:
#   "arXiv:2603.17168v1 Announce Type: new Abstract: ..."
_ARXIV_PREFIX = re.compile(
    r"^arXiv:\S+\s+Announce Type:\s+[\w-]+\s+Abstract:\s*", re.IGNORECASE
)


@contextmanager
def _socket_timeout(seconds: int):
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(old)


def _fetch_with_retry(url: str, timeout: int, retries: int, delay: int):
    """Fetch a feed URL, retrying on timeout/error before giving up.

    @return: feedparser result, or None if all attempts failed
    """
    last_exc = None
    for attempt in range(1, retries + 2):   # attempts = retries + 1
        try:
            with _socket_timeout(timeout):
                return feedparser.parse(url)
        except Exception as exc:
            last_exc = exc
            if attempt <= retries:
                logger.warning("attempt %d/%d failed (%s) — retrying in %ds",
                               attempt, retries + 1, exc, delay)
                time.sleep(delay)
    logger.warning("all %d attempts failed: %s", retries + 1, last_exc)
    return None


def _parse_entry_date(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            return datetime.fromtimestamp(mktime(t), tz=timezone.utc)
    return None


def _extract_image(entry) -> str:
    """Extract first image URL from a feed entry (media, enclosure, or inline img)."""
    # media:content or media:thumbnail
    for media in getattr(entry, "media_content", []):
        url = media.get("url", "")
        if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return url
    for media in getattr(entry, "media_thumbnail", []):
        url = media.get("url", "")
        if url:
            return url
    # enclosure (podcasts / image RSS)
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("href", "")
    # img tag inside summary/content HTML
    for field in ("summary", "content"):
        html = ""
        val = getattr(entry, field, None)
        if isinstance(val, list) and val:
            html = val[0].get("value", "")
        elif isinstance(val, str):
            html = val
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
    return ""


def _clean_excerpt(text: str, source_name: str) -> str:
    """Strip HTML, decode entities, and remove source-specific boilerplate."""
    import html
    text = re.sub(r"<[^>]+>", "", text)   # strip HTML tags
    text = html.unescape(text)             # decode &amp; &nbsp; etc.
    text = re.sub(r"\s+", " ", text).strip()
    # Remove arXiv announce boilerplate
    if "arxiv" in source_name.lower():
        text = _ARXIV_PREFIX.sub("", text).strip()
    return text


def fetch_rss(sources: list[RssSource],
              timeout: int = _FEED_TIMEOUT,
              retries: int = _FEED_RETRIES) -> list[dict]:
    """Fetch all RSS sources within each source's lookback window.

    Each feed is retried up to `retries` times before being skipped,
    so a single flaky source never blocks the whole run indefinitely.

    @param sources: list of RssSource from config
    @param timeout: per-attempt socket timeout in seconds
    @param retries: number of retries after first failure
    @return: list of article dicts
    """
    now = datetime.now(tz=timezone.utc)
    articles: list[dict] = []

    for source in sources:
        cutoff = now - timedelta(hours=source.lookback_hours)
        feed = _fetch_with_retry(source.url, timeout, retries, _RETRY_DELAY)

        if feed is None:
            logger.warning("[%s] skipped after %d failed attempts", source.name, retries + 1)
            continue

        if feed.bozo and not feed.entries:
            logger.warning("[%s] feed parse error: %s", source.name, feed.bozo_exception)
            continue

        count = 0
        for entry in feed.entries:
            pub = _parse_entry_date(entry)
            if pub and pub < cutoff:
                continue
            excerpt = _clean_excerpt(getattr(entry, "summary", "") or "", source.name)
            articles.append({
                "source": source.name,
                "category": source.category,
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "published": pub.strftime("%Y-%m-%d %H:%M UTC") if pub else "unknown",
                "excerpt": excerpt,
                "image_url": _extract_image(entry),
            })
            count += 1

        logger.info("[%s] %d articles (lookback %dh, feed total %d)",
                    source.name, count, source.lookback_hours, len(feed.entries))

    return articles
