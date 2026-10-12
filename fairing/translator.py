"""Translate English article titles to Chinese and generate Chinese summaries.

Pipeline contract:
  INPUT:  English articles (CJK content excluded at RSS ingestion)
  OUTPUT: Chinese translations of English titles and summaries
  The base pipeline (MD, NotebookLM, embeddings) always stays in English.
  --chinese adds a *parallel* Chinese note; it does not alter English output.

Translation uses LLM Gateway's dedicated Fairing identity. Fairing never
holds a model-provider key.
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
Translate each English title into plain Chinese. Summarize the excerpt in one \
or two short factual Chinese sentences. Keep names and numbers accurate. Do not \
add opinions, praise, advice, or facts missing from the excerpt.
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


_GATEWAY_HOSTS = frozenset({"localhost", "127.0.0.1", "llm-gateway"})


def _gateway_endpoint() -> str:
    base_url = os.environ.get("LLM_GATEWAY_URL", "http://127.0.0.1:8525")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in _GATEWAY_HOSTS
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("LLM_GATEWAY_URL must be a local HTTP Gateway service")
    return base_url.rstrip("/") + "/chat"


def _call_gateway(prompt: str) -> str:
    import requests

    key = os.environ.get("LLM_GATEWAY_API_KEY", "").strip()
    if not key:
        raise ValueError("LLM_GATEWAY_API_KEY is required for translation")
    response = requests.post(
        _gateway_endpoint(),
        headers={"X-API-Key": key},
        json={
            "messages": [{"role": "user", "content": prompt}],
            "caller": "fairing/translation",
            "information_class": "public",
            "max_tokens": 4096,
            "temperature": 0.2,
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json().get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM Gateway returned no translation content")
    return content


def translation_available() -> bool:
    backend = os.environ.get("TRANSLATOR", "gateway").lower()
    return backend == "gateway" and bool(os.environ.get("LLM_GATEWAY_API_KEY", "").strip())


# ── public API ────────────────────────────────────────────────────────────────

def translate(articles: list[dict], api_key: str = "",
              model_name: str = "") -> list[dict]:
    """Translate English article titles/excerpts to Chinese.

    Operates on English input only — CJK articles are excluded upstream
    in rss.py. This function translates FROM English TO Chinese.

    Backend via environment variables:
      TRANSLATOR      gateway (default)
      LLM_GATEWAY_URL local Gateway service, default http://127.0.0.1:8525
      LLM_GATEWAY_API_KEY dedicated Fairing client key

    Falls back to raw English excerpt on any error, never raises.

    @param articles:   English article dicts to translate
    @param api_key:    ignored (backward compatibility)
    @param model_name: ignored (backward compatibility)
    """
    backend = os.environ.get("TRANSLATOR", "gateway")
    logger.info("Translator backend: %s (EN → ZH)", backend)
    public_articles = [a for a in articles if a.get("information_class") == "public"]
    _apply_fallback([a for a in articles if a.get("information_class") != "public"])
    if not public_articles:
        return articles
    if not translation_available():
        logger.warning("translation backend unavailable; keeping English source text")
        _apply_fallback(public_articles)
        return articles

    failed = 0
    for i in range(0, len(public_articles), _BATCH_SIZE):
        batch  = public_articles[i: i + _BATCH_SIZE]
        prompt = _PROMPT_TMPL.format(items=_build_items(batch))
        try:
            raw    = _call_gateway(prompt)
            parsed = _parse_response(raw)
            if (not isinstance(parsed, list) or len(parsed) != len(batch)
                    or any(not isinstance(item, dict)
                           or not isinstance(item.get("t"), str)
                           or not isinstance(item.get("s"), str)
                           for item in parsed)):
                raise ValueError("LLM returned an invalid translation list")
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
        if i + _BATCH_SIZE < len(public_articles):
            time.sleep(0.5)

    if failed:
        logger.warning("Translation: %d/%d batch(es) used fallback",
                       failed, -(-len(public_articles) // _BATCH_SIZE))
    return articles
