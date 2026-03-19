"""Summarize articles in Chinese using Gemini API.

Token-saving strategy:
  - Only title + category + 150-char excerpt sent per article (no full text in prompt).
  - Compact JSON-only prompt, no examples or role padding.
  - Batched in chunks of 25 to stay within rate limits.
  - Falls back to raw excerpt on any error, with prominent log warning.
"""
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25
_EXCERPT_CHARS = 500   # chars sent to Gemini per article

_PROMPT = """\
Translate titles to Chinese and write a 2-sentence Chinese summary for each article.
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


def _apply_fallback(articles: list[dict]) -> None:
    """Fill title_zh/summary_zh from raw data when Gemini is unavailable."""
    for a in articles:
        a.setdefault("title_zh", a["title"])
        a.setdefault("summary_zh", a.get("excerpt", ""))


def _call_gemini(client, model_name: str, articles: list[dict]) -> list[dict]:
    prompt = _PROMPT.format(items=_build_items(articles))
    response = client.models.generate_content(model=model_name, contents=prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```[\w]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    parsed = json.loads(raw)
    for a, t in zip(articles, parsed):
        a["title_zh"] = t.get("t", a["title"])
        a["summary_zh"] = t.get("s", "")
    return articles


def translate(articles: list[dict], api_key: str,
              model_name: str = "gemini-2.0-flash") -> list[dict]:
    """Add 'title_zh' and 'summary_zh' to each article using Gemini.

    Processes articles in batches of 25. On any failure, falls back to raw
    excerpts and logs a prominent warning — never raises.

    @param articles: list of article dicts
    @param api_key: Gemini API key
    @param model_name: Gemini model identifier
    @return: articles with 'title_zh' and 'summary_zh' added in-place
    """
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as exc:
        logger.warning("!" * 60)
        logger.warning("GEMINI INIT FAILED — falling back to raw excerpts")
        logger.warning("Reason: %s", exc)
        logger.warning("!" * 60)
        _apply_fallback(articles)
        return articles

    failed_batches = 0
    for i in range(0, len(articles), _BATCH_SIZE):
        batch = articles[i: i + _BATCH_SIZE]
        try:
            _call_gemini(client, model_name, batch)
            logger.info("Gemini: batch %d-%d translated (%d articles)",
                        i + 1, i + len(batch), len(batch))
        except Exception as exc:
            failed_batches += 1
            logger.warning("!" * 60)
            logger.warning("GEMINI BATCH FAILED (articles %d-%d) — falling back",
                           i + 1, i + len(batch))
            logger.warning("Reason: %s", exc)
            logger.warning("!" * 60)
            _apply_fallback(batch)
        if i + _BATCH_SIZE < len(articles):
            time.sleep(1)   # stay under rate limit between batches

    if failed_batches:
        logger.warning("Gemini: %d/%d batch(es) used fallback",
                       failed_batches, -(-len(articles) // _BATCH_SIZE))
    return articles
