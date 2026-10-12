"""Translate English article titles to Chinese and generate Chinese summaries.

Pipeline contract:
  INPUT:  English articles (CJK content excluded at RSS ingestion)
  OUTPUT: Chinese translations of English titles and summaries
  The base pipeline (MD, NotebookLM, embeddings) always stays in English.
  --chinese adds a *parallel* Chinese note; it does not alter English output.

The free-only route uses a locally hosted Ollama model. No external model
provider is invoked, even when old API keys remain in the environment.
"""
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_BATCH_SIZE    = 25
_EXCERPT_CHARS = 500

_PROMPT_TMPL = """\
Translate the following English article titles to Chinese and write a \
2-sentence Chinese summary for each.
Output ONLY a JSON array, same order as input, no extra text:
[{{"t":"<title_zh>","s":"<summary_zh>"}},...]

Articles:
{items}"""


def _build_items(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        excerpt = (a.get("excerpt") or "")[:_EXCERPT_CHARS]
        lines.append(f"{i}.{a['source']}|{a['category']}|{a['title']}|{excerpt}")
    return "\n".join(lines)


def _parse_response(raw: str) -> list[dict] | None:
    raw = raw.strip()
    raw = re.sub(r"^```[\w]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _apply_fallback(articles: list[dict]) -> None:
    for a in articles:
        a.setdefault("title_zh", a["title"])
        a.setdefault("summary_zh", a.get("excerpt", ""))


# ── local model backend ──────────────────────────────────────────────────────

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "ollama"})
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _ollama_endpoint() -> str:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in _LOCAL_HOSTS
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("OLLAMA_BASE_URL must be a local HTTP Ollama service")
    return base_url.rstrip("/") + "/api/generate"


def _call_ollama(prompt: str) -> str:
    import requests

    model = os.environ.get("OLLAMA_MODEL", "").strip()
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("OLLAMA_MODEL must name an installed local model")
    response = requests.post(
        _ollama_endpoint(),
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return str(response.json().get("response", ""))


def _call_llm(prompt: str) -> str:
    backend = os.environ.get("TRANSLATOR", "ollama").lower()
    if backend != "ollama":
        raise ValueError("free-only translation supports only local Ollama")
    return _call_ollama(prompt)


# ── public API ────────────────────────────────────────────────────────────────

def translate(articles: list[dict], api_key: str = "",
              model_name: str = "") -> list[dict]:
    """Translate English article titles/excerpts to Chinese.

    Operates on English input only — CJK articles are excluded upstream
    in rss.py. This function translates FROM English TO Chinese.

    Backend via environment variables:
      TRANSLATOR      ollama (default)
      OLLAMA_BASE_URL local Ollama service, default http://127.0.0.1:11434
      OLLAMA_MODEL    installed local model; if absent, keep English text

    Falls back to raw English excerpt on any error, never raises.

    @param articles:   English article dicts to translate
    @param api_key:    ignored (backward compatibility)
    @param model_name: ignored (backward compatibility)
    """
    backend = os.environ.get("TRANSLATOR", "ollama")
    logger.info("Translator backend: %s (EN → ZH)", backend)
    if backend.lower() != "ollama" or not os.environ.get("OLLAMA_MODEL", "").strip():
        logger.warning("local Ollama translation unavailable; keeping English source text")
        _apply_fallback(articles)
        return articles

    failed = 0
    for i in range(0, len(articles), _BATCH_SIZE):
        batch  = articles[i: i + _BATCH_SIZE]
        prompt = _PROMPT_TMPL.format(items=_build_items(batch))
        try:
            raw    = _call_llm(prompt)
            parsed = _parse_response(raw)
            if parsed is None:
                raise ValueError("LLM returned non-JSON")
            for a, t in zip(batch, parsed):
                a["title_zh"]   = t.get("t", a["title"])
                a["summary_zh"] = t.get("s", "")
            logger.info("Translated batch %d-%d (EN→ZH)", i + 1, i + len(batch))
        except Exception as exc:
            failed += 1
            logger.warning("!" * 50)
            logger.warning("TRANSLATION FAILED (articles %d-%d) — fallback to English",
                           i + 1, i + len(batch))
            logger.warning("Backend: %s  |  %s", backend, exc)
            logger.warning("!" * 50)
            _apply_fallback(batch)
        if i + _BATCH_SIZE < len(articles):
            time.sleep(0.5)

    if failed:
        logger.warning("Translation: %d/%d batch(es) used fallback",
                       failed, -(-len(articles) // _BATCH_SIZE))
    return articles
