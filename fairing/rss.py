import json
import re
import socket
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser

from .config import RssSource
from .paths import feed_errors_file as _feed_errors_file

logger = logging.getLogger(__name__)

_FEED_TIMEOUT = 20   # seconds per attempt
_FEED_RETRIES = 2    # retry count after first failure
_RETRY_DELAY  = 4    # seconds between retries

LOOKBACK_MIN_HOURS = 25

# arXiv RSS abstracts start with boilerplate like:
#   "arXiv:2603.17168v1 Announce Type: new Abstract: ..."
_ARXIV_PREFIX = re.compile(
    r"^arXiv:\S+\s+Announce Type:\s+[\w-]+\s+Abstract:\s*", re.IGNORECASE
)

# CJK Unicode block (covers most Chinese/Japanese/Korean characters)
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")

# Pipeline is English-only. Articles with >25% CJK characters are excluded.
_CJK_THRESHOLD = 0.25


def _is_cjk_dominant(text: str) -> bool:
    """Return True if more than CJK_THRESHOLD of characters are CJK.

    The scoring model (all-MiniLM-L6-v2) is English-biased.
    The entire pipeline — MD, NotebookLM, embeddings — assumes English input.
    Chinese articles are excluded at ingestion to maintain consistency.
    """
    if not text:
        return False
    cjk_count = len(_CJK_RE.findall(text))
    return cjk_count / len(text) > _CJK_THRESHOLD


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
            try:
                return datetime.fromtimestamp(mktime(t), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
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
              retries: int = _FEED_RETRIES,
              min_lookback_hours: float = 0) -> list[dict]:
    """Fetch all RSS sources within a dynamic lookback window.

    Each feed is retried up to `retries` times before being skipped,
    so a single flaky source never blocks the whole run indefinitely.
    The effective lookback is at least LOOKBACK_MIN_HOURS, ensuring weekend
    gaps (e.g. arXiv not publishing on weekends) are always covered.

    @param sources: list of RssSource from config
    @param timeout: per-attempt socket timeout in seconds
    @param retries: number of retries after first failure
    @param min_lookback_hours: minimum lookback to guarantee coverage; the
        effective window is max(LOOKBACK_MIN_HOURS, ceil(min_lookback_hours))
    @return: list of article dicts
    """
    import math
    now = datetime.now(tz=timezone.utc)
    effective_hours = max(LOOKBACK_MIN_HOURS, math.ceil(min_lookback_hours))
    cutoff = now - timedelta(hours=effective_hours)
    articles: list[dict] = []

    for source in sources:
        if not source.enabled:
            logger.info("[%s] disabled — skipping", source.name)
            continue
        feed = _fetch_with_retry(source.url, timeout, retries, _RETRY_DELAY)

        if feed is None:
            logger.warning("[%s] skipped after %d failed attempts", source.name, retries + 1)
            _record_feed_error(source.name, f"all {retries + 1} attempts failed")
            continue

        if feed.bozo and not feed.entries:
            logger.warning("[%s] feed parse error: %s", source.name, feed.bozo_exception)
            _record_feed_error(source.name, str(feed.bozo_exception))
            continue

        _clear_feed_error(source.name)

        count = 0
        skipped_cjk = 0
        for entry in feed.entries:
            pub = _parse_entry_date(entry)
            if pub and pub < cutoff:
                continue
            title   = entry.get("title", "").strip()
            excerpt = _clean_excerpt(getattr(entry, "summary", "") or "", source.name)
            # Enforce English-only pipeline: exclude CJK-dominant articles
            if _is_cjk_dominant(title + " " + excerpt):
                skipped_cjk += 1
                continue
            articles.append({
                "source": source.name,
                "category": source.category,
                "title": title,
                "url": entry.get("link", ""),
                "published": pub.strftime("%Y-%m-%d %H:%M UTC") if pub else "unknown",
                "excerpt": excerpt,
                "image_url": _extract_image(entry),
            })
            count += 1

        if skipped_cjk:
            logger.info("[%s] skipped %d CJK-dominant article(s) (English-only pipeline)",
                        source.name, skipped_cjk)
        logger.info("[%s] %d articles (lookback %dh, feed total %d)",
                    source.name, count, effective_hours, len(feed.entries))

    return articles


# ── Feed error tracking ────────────────────────────────────────────────────────

def _load_feed_errors() -> dict:
    f = _feed_errors_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_feed_errors(errors: dict) -> None:
    _feed_errors_file().write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_feed_error(source_name: str, error_msg: str) -> None:
    """Increment consecutive failure count for a source."""
    errors = _load_feed_errors()
    today  = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    entry  = errors.get(source_name, {"consecutive_failures": 0})
    entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
    entry["last_error"] = str(error_msg)[:200]
    entry["last_failed"] = today
    errors[source_name] = entry
    _save_feed_errors(errors)
    if entry["consecutive_failures"] >= 5:
        logger.warning("[%s] consecutive failures: %d — check \\l for details",
                       source_name, entry["consecutive_failures"])


def _clear_feed_error(source_name: str) -> None:
    """Reset consecutive failure count when a source fetches successfully."""
    errors = _load_feed_errors()
    if source_name in errors:
        del errors[source_name]
        _save_feed_errors(errors)


def load_feed_errors() -> dict:
    """Return current feed error state (public API for display in \\l)."""
    return _load_feed_errors()
