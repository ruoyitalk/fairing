"""Persist seen article URLs and titles to deduplicate across all runs.

State file: .seen_urls.json  (gitignored)
Format:
  {
    "2026-03-19": {
      "urls":   ["https://normalized-url/", ...],
      "titles": ["normalized title", ...]
    }
  }

Dedup layers (applied in order):
  1. Normalized URL match  — same article, possibly with different tracking params
  2. Normalized title match — same content cross-posted at a different URL

URL normalization removes:
  - Tracking query params (utm_*, ref, fbclid, etc.)
  - URL fragment (#anchor)
  - Trailing slash on path
  - Lowercases scheme and host

Title normalization:
  - Lowercase
  - Strip punctuation and extra whitespace

Entries older than RETAIN_DAYS are pruned automatically.
"""
import json
import logging
import re
import string
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

logger = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent.parent / ".seen_urls.json"
RETAIN_DAYS = 30

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
}


# ── normalization ──────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Strip tracking params, fragment, trailing slash; lowercase scheme/host."""
    try:
        p = urlparse(url)
        clean_params = {
            k: v for k, v in parse_qs(p.query).items()
            if k.lower() not in _TRACKING_PARAMS
        }
        cleaned = p._replace(
            scheme=p.scheme.lower(),
            netloc=p.netloc.lower(),
            path=p.path.rstrip("/") or "/",
            query=urlencode(clean_params, doseq=True),
            fragment="",
        )
        return urlunparse(cleaned)
    except Exception:
        return url


def normalize_title(title: str) -> str:
    """Lowercase and strip punctuation for fuzzy title matching."""
    title = title.lower()
    title = title.translate(str.maketrans("", "", string.punctuation))
    title = re.sub(r"\s+", " ", title).strip()
    return title


# ── state I/O ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    if _STATE_FILE.exists():
        try:
            raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            # Migrate old flat-list format {"date": [...]} → new dict format
            migrated = {}
            for day, val in raw.items():
                if isinstance(val, list):
                    migrated[day] = {"urls": val, "titles": []}
                else:
                    migrated[day] = val
            return migrated
        except Exception:
            return {}
    return {}


def _save(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── public API ────────────────────────────────────────────────────────────────

def filter_unseen(articles: list[dict]) -> list[dict]:
    """Return articles not seen in any previous run.

    Checks both normalized URL and normalized title, so cross-posted articles
    and URL-with-tracking-params variants are caught.

    @param articles: full article list from fetchers
    @return: truly new articles only
    """
    state = _load()
    all_urls:   set[str] = set()
    all_titles: set[str] = set()
    for day_data in state.values():
        all_urls.update(day_data.get("urls", []))
        all_titles.update(day_data.get("titles", []))

    fresh: list[dict] = []
    skipped_url = skipped_title = 0
    for a in articles:
        nurl   = normalize_url(a["url"])
        ntitle = normalize_title(a.get("title", ""))

        if nurl in all_urls:
            skipped_url += 1
            continue
        if ntitle and ntitle in all_titles:
            skipped_title += 1
            logger.debug("Title dedup: %s", a.get("title", "")[:60])
            continue
        fresh.append(a)

    total_skipped = skipped_url + skipped_title
    if total_skipped:
        logger.info(
            "Dedup: %d skipped (%d by URL, %d by title)",
            total_skipped, skipped_url, skipped_title,
        )
    return fresh


def mark_seen(articles: list[dict]) -> None:
    """Record normalized URLs and titles for today's processed articles."""
    today = date.today().isoformat()
    state = _load()

    entry = state.setdefault(today, {"urls": [], "titles": []})
    seen_urls   = set(entry["urls"])
    seen_titles = set(entry["titles"])

    for a in articles:
        seen_urls.add(normalize_url(a["url"]))
        ntitle = normalize_title(a.get("title", ""))
        if ntitle:
            seen_titles.add(ntitle)

    entry["urls"]   = list(seen_urls)
    entry["titles"] = list(seen_titles)

    cutoff = (date.today() - timedelta(days=RETAIN_DAYS)).isoformat()
    state  = {d: v for d, v in state.items() if d >= cutoff}

    _save(state)
