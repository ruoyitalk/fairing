"""Export interface for downstream pipeline stages (payload).

payload_queue.json is the handoff file between fairing (stage 1) and payload (stage 2).
Articles are added by explicit user action — not automatically from training labels.

Article ID
----------
``article_id`` = first 16 hex characters of SHA-256(normalize_url(url)).

16 hex = 64 bits. Collision probability over 100 years of daily use
(~1.8 M articles) is ~9 × 10⁻⁸ (1 in 11 million) — effectively impossible
for a personal tool.

payload_queue.json format
--------------------------
JSON array, newest-queued first::

    [
      {
        "article_id":  "3a7f9c2d1e4b8f2c",
        "url":         "https://example.com/post/?utm_source=rss",
        "title":       "Article Title",
        "source":      "Hacker News",
        "queued_date": "2026-03-21"
      }
    ]

payload reads this file, maintains its own "already downloaded" state,
and never writes back to fairing.
"""
import fcntl
import hashlib
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

from .paths import payload_queue_file, scoring_store_file, title_index_file, last_run_file
from .state import normalize_url, today_beijing


# ── Article ID ─────────────────────────────────────────────────────────────────

def article_id_for(url: str) -> str:
    """Return the 16-hex-char article ID for a URL.

    ID = first 16 characters of SHA-256(normalize_url(url)).
    16 hex = 64 bits; collision probability < 0.000009 % over 100 years of use.
    Strips tracking parameters before hashing so the same article
    always gets the same ID regardless of referral source.
    """
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


# ── Queue I/O ──────────────────────────────────────────────────────────────────

@contextmanager
def _queue_lock(*, exclusive: bool):
    queue_file = payload_queue_file()
    lock_file = queue_file.with_name(queue_file.name + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_payload_queue_unlocked() -> list[dict]:
    f = payload_queue_file()
    if not f.exists():
        return []
    try:
        value = json.loads(f.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_queue_unlocked(queue: list[dict]) -> None:
    destination = payload_queue_file()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = os.fdopen(descriptor, "w", encoding="utf-8")
    try:
        json.dump(queue, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary_name, destination)
    except Exception:
        temporary.close()
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_payload_queue() -> list[dict]:
    """Load the current payload queue from DATA_DIR."""
    with _queue_lock(exclusive=False):
        return _load_payload_queue_unlocked()


def write_payload_queue(queue: list[dict]) -> None:
    """Atomically replace the queue while excluding concurrent writers."""
    with _queue_lock(exclusive=True):
        _write_queue_unlocked(queue)


def add_to_payload_queue(article: dict) -> bool:
    """Add an article to the payload queue.

    No-op and returns False if the article_id is already present.

    @param article: dict with at least 'url', 'title', 'source' fields
    @return: True if added, False if already queued
    """
    url  = article.get("url", "")
    aid  = article_id_for(url)
    with _queue_lock(exclusive=True):
        queue = _load_payload_queue_unlocked()
        if any(e.get("article_id") == aid for e in queue):
            logger.debug("Already in payload queue: %s", aid)
            return False
        queue.insert(0, {
            "article_id":  aid,
            "url":         url,
            "title":       article.get("title", ""),
            "source":      article.get("source", ""),
            "queued_date": today_beijing(),
        })
        _write_queue_unlocked(queue)
    logger.info("Added to payload queue: %s — %s", aid, article.get("title", "")[:60])
    return True


def remove_from_payload_queue(article_id: str) -> bool:
    """Remove an entry from the payload queue by article_id."""
    with _queue_lock(exclusive=True):
        queue = _load_payload_queue_unlocked()
        updated = [e for e in queue if e.get("article_id") != article_id]
        if len(updated) == len(queue):
            return False
        _write_queue_unlocked(updated)
        return True


# ── Search pool ────────────────────────────────────────────────────────────────

def _load_search_pool() -> list[dict]:
    """Build a merged article pool for title search.

    Primary source: title_index.jsonl — lightweight, no embeddings, fast to read.
    Fallback: scoring_store.jsonl — used if title_index has not been built yet.
    Supplement: last_run_articles.json — adds score field for today's articles.
    """
    pool: dict[str, dict] = {}

    if title_index_file().exists():
        for line in title_index_file().read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                aid   = entry.get("article_id", "")
                if aid and aid not in pool:
                    pool[aid] = entry
            except (json.JSONDecodeError, KeyError):
                continue
    elif scoring_store_file().exists():
        for line in scoring_store_file().read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                url   = entry.get("url", "")
                if not url:
                    continue
                aid = article_id_for(url)
                if aid not in pool:
                    pool[aid] = {
                        "article_id": aid,
                        "url":        url,
                        "title":      entry.get("title", ""),
                        "source":     entry.get("source", ""),
                        "date":       entry.get("date", ""),
                    }
            except (json.JSONDecodeError, KeyError):
                continue

    if last_run_file().exists():
        try:
            for entry in json.loads(last_run_file().read_text(encoding="utf-8")):
                url = entry.get("url", "")
                if not url:
                    continue
                aid = article_id_for(url)
                if aid not in pool:
                    pool[aid] = {
                        "article_id": aid,
                        "url":        url,
                        "title":      entry.get("title", ""),
                        "source":     entry.get("source", ""),
                        "date":       entry.get("published", ""),
                    }
                if "score" in entry:
                    pool[aid]["score"] = entry["score"]
        except (json.JSONDecodeError, OSError):
            pass

    return list(pool.values())


def search_by_title(query: str) -> list[dict]:
    """Search all known articles by English title (case-insensitive, all words must match).

    When query is empty, returns all known articles sorted by date descending.

    @param query: space-separated keywords; all must appear in title; empty = return all
    @return: list of matching article dicts sorted by date descending
    """
    words = query.lower().split()
    pool = _load_search_pool()
    matches = (
        pool if not words
        else [a for a in pool if all(w in a.get("title", "").lower() for w in words)]
    )
    matches.sort(key=lambda a: a.get("date", ""), reverse=True)
    return matches


def find_by_id(article_id: str) -> Optional[dict]:
    """Find an article by its 16-char article_id.

    @return: article dict or None if not found
    """
    for a in _load_search_pool():
        if a["article_id"] == article_id:
            return a
    return None
