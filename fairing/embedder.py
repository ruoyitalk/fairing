"""Compute and cache scoring payloads for articles.

Each article is enriched with:
  text_for_scoring: cleaned concatenation of title + excerpt + full_text snippet
  embedding:        384-dim vector from all-MiniLM-L6-v2

Results are persisted to .scoring_store.jsonl so embeddings are computed
only once per URL.
"""
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_NAME  = "sentence-transformers/all-MiniLM-L6-v2"
from .paths import scoring_store_file as _scoring_store_file
_model_cache = None


def _get_model():
    global _model_cache
    if _model_cache is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s...", _MODEL_NAME)
        _model_cache = SentenceTransformer(_MODEL_NAME)
    return _model_cache


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s.,!?-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_text(article: dict) -> str:
    parts = [
        _clean(article.get("title", "")),
        _clean(article.get("excerpt", ""))[:200],
        _clean(article.get("full_text", ""))[:300],
    ]
    return " ".join(p for p in parts if p)


def _load_store() -> dict[str, dict]:
    if not _scoring_store_file().exists():
        return {}
    store = {}
    for line in _scoring_store_file().read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            store[entry["url"]] = entry
    return store


def _append_store(entry: dict) -> None:
    with _scoring_store_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # Keep lightweight title index in sync (no embedding — fast for search)
    from .export import article_id_for
    from .paths import title_index_file as _title_index_file
    title_entry = {
        "article_id": article_id_for(entry["url"]),
        "url":        entry["url"],
        "title":      entry.get("title", ""),
        "source":     entry.get("source", ""),
        "date":       entry.get("date", ""),
        "category":   entry.get("category", ""),
        "tags":       entry.get("tags", []),
    }
    with _title_index_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(title_entry, ensure_ascii=False) + "\n")


# ── Fan-out to subscriber output files ────────────────────────────────────────

def fan_out(articles: list[dict], subscriptions: list) -> dict:
    """Write articles to per-subscriber output files based on tag matching.

    Each subscriber declares a set of tags. An article matches a subscriber
    if ANY of the article's source tags is in the subscriber's tag set.
    Matched articles are appended to the subscriber's output JSONL file.

    @param articles: processed article list (must have 'tags' field from source)
    @param subscriptions: list of Subscription objects from config
    @return: stats dict {subscriber_name: {"matched": N, "written": N}}
    """
    from pathlib import Path

    stats = {}
    for sub in subscriptions:
        sub_tags = set(sub.tags)
        matched = [a for a in articles if set(a.get("tags", [])) & sub_tags]
        stats[sub.name] = {"matched": len(matched), "written": 0}

        if not matched:
            continue

        out_path = Path(sub.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing URLs for dedup within subscriber file
        from .state import normalize_url
        existing_urls: set[str] = set()
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        existing_urls.add(normalize_url(json.loads(line).get("url", "")))
                    except (json.JSONDecodeError, KeyError):
                        continue

        written = 0
        with out_path.open("a", encoding="utf-8") as f:
            for a in matched:
                normalized_url = normalize_url(a.get("url", ""))
                if normalized_url in existing_urls:
                    continue
                entry = {
                    "url":   a.get("url", ""),
                    "title": a.get("title", ""),
                    "date":  a.get("published", ""),
                    "source": a.get("source", ""),
                    "category": a.get("category", ""),
                    "tags":  a.get("tags", []),
                    "text_for_scoring": a.get("text_for_scoring", ""),
                    "fetch_engine": a.get("fetch_engine", ""),
                    "fetch_blocked": a.get("fetch_blocked", False),
                    "fetch_error_type": a.get("fetch_error_type"),
                    "fetch_block_reason": a.get("fetch_block_reason"),
                    "upstream_status": a.get("upstream_status"),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                existing_urls.add(normalized_url)
                written += 1
        stats[sub.name]["written"] = written
        if written:
            logger.info("[fan-out] %s: %d/%d articles written to %s",
                        sub.name, written, len(matched), sub.output)

    return stats


def enrich(articles: list[dict]) -> list[dict]:
    """Add text_for_scoring and embedding to each article, using cache.

    Articles already in .scoring_store.jsonl are not re-embedded.
    New articles are embedded and appended to the store.

    @param articles: list of article dicts
    @return: same list with text_for_scoring and embedding added in-place
    """
    store = _load_store()
    to_embed: list[tuple[int, dict]] = []

    for i, a in enumerate(articles):
        url = a.get("url", "")
        if url in store:
            cached = store[url]
            a["text_for_scoring"] = cached["text_for_scoring"]
            a["embedding"]        = cached["embedding"]
        else:
            a["text_for_scoring"] = _build_text(a)
            to_embed.append((i, a))

    if to_embed:
        model  = _get_model()
        texts  = [a["text_for_scoring"] for _, a in to_embed]
        vecs   = model.encode(texts, show_progress_bar=False)
        for (i, a), vec in zip(to_embed, vecs):
            a["embedding"] = vec.tolist()
            _append_store({
                "url":              a["url"],
                "date":             a.get("published", ""),
                "source":           a.get("source", ""),
                "category":         a.get("category", ""),
                "tags":             a.get("tags", []),
                "title":            a.get("title", ""),
                "text_for_scoring": a["text_for_scoring"],
                "full_text":        a.get("full_text", ""),
                "fetch_engine":     a.get("fetch_engine", ""),
                "fetch_blocked":    a.get("fetch_blocked", False),
                "fetch_error_type": a.get("fetch_error_type"),
                "fetch_block_reason": a.get("fetch_block_reason"),
                "upstream_status":  a.get("upstream_status"),
                "embedding":        a["embedding"],
            })
        logger.info("Embedded %d new articles", len(to_embed))

    return articles


def load_store() -> dict[str, dict]:
    """Return full scoring store as {url: entry}."""
    return _load_store()
