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
    }
    with _title_index_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(title_entry, ensure_ascii=False) + "\n")


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
                "title":            a.get("title", ""),
                "text_for_scoring": a["text_for_scoring"],
                "embedding":        a["embedding"],
            })
        logger.info("Embedded %d new articles", len(to_embed))

    return articles


def load_store() -> dict[str, dict]:
    """Return full scoring store as {url: entry}."""
    return _load_store()
