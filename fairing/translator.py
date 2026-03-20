"""Translate English article titles to Chinese and generate Chinese summaries.

Pipeline contract:
  INPUT:  English articles (CJK content excluded at RSS ingestion)
  OUTPUT: Chinese translations of English titles and summaries
  The base pipeline (MD, NotebookLM, embeddings) always stays in English.
  --chinese adds a *parallel* Chinese note; it does not alter English output.

Backend selection via TRANSLATOR env var:

  TRANSLATOR=gemini    GEMINI_API_KEY=AIza...           (default, free tier)
  TRANSLATOR=openai    OPENAI_API_KEY=sk-...            (OpenAI or compatible)
  TRANSLATOR=claude    ANTHROPIC_API_KEY=sk-ant-...

Any OpenAI-compatible API (Groq, Together, Mistral, etc.) works via the
openai backend by setting OPENAI_BASE_URL to the provider endpoint.
"""
import json
import logging
import os
import re
import time

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


# ── LLM backends ──────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model   = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    client  = genai.Client(api_key=api_key)
    resp    = client.models.generate_content(model=model, contents=prompt)
    return resp.text


def _call_openai(prompt: str) -> str:
    """Works for OpenAI and any OpenAI-compatible API (Groq, Together, Mistral, etc.)."""
    from openai import OpenAI
    api_key  = os.environ.get("OPENAI_API_KEY", "")
    model    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    client   = OpenAI(api_key=api_key, base_url=base_url)
    resp     = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content


def _call_claude(prompt: str) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model   = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-20240307")
    client  = anthropic.Anthropic(api_key=api_key)
    msg     = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


_BACKENDS: dict[str, callable] = {
    "gemini": _call_gemini,
    "openai": _call_openai,
    "claude": _call_claude,
}


def _call_llm(prompt: str) -> str:
    backend = os.environ.get("TRANSLATOR", "gemini").lower()
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise ValueError(
            f"Unknown TRANSLATOR={backend!r}. "
            f"Valid: {', '.join(_BACKENDS)}"
        )
    return fn(prompt)


# ── public API ────────────────────────────────────────────────────────────────

def translate(articles: list[dict], api_key: str = "",
              model_name: str = "") -> list[dict]:
    """Translate English article titles/excerpts to Chinese.

    Operates on English input only — CJK articles are excluded upstream
    in rss.py. This function translates FROM English TO Chinese.

    Backend and credentials via environment variables:
      TRANSLATOR      gemini (default) | openai | claude
      GEMINI_API_KEY  for gemini (free tier at aistudio.google.com)
      OPENAI_API_KEY  for openai / openai-compatible APIs
      OPENAI_BASE_URL provider endpoint override (Groq, Together, etc.)
      ANTHROPIC_API_KEY for claude

    Falls back to raw English excerpt on any error, never raises.

    @param articles:   English article dicts to translate
    @param api_key:    ignored (backward compat); use GEMINI_API_KEY env var
    @param model_name: ignored (backward compat); use *_MODEL env vars
    """
    backend = os.environ.get("TRANSLATOR", "gemini")
    logger.info("Translator backend: %s (EN → ZH)", backend)

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
