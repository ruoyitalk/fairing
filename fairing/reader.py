"""Article URL utilities and excerpt enrichment for fairing.

Responsibilities:
  - Classify URLs by content type (article / image / video)
  - Open URLs in the OS default browser
  - Fetch full article text for excerpt enrichment when RSS provides too little
    content to produce a quality embedding (used by the scoring pipeline only)

Full-text reading is the responsibility of the payload consumer, not fairing.
"""
import logging
import os
import requests
import subprocess
import sys
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SG_FETCH_TIMEOUT_MS = int(os.environ.get("FAIRING_SG_FETCH_TIMEOUT_MS", "20000"))
_SG_CLIENT_TIMEOUT_S = float(os.environ.get("FAIRING_SG_FETCH_TIMEOUT_S", "30"))

_IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif"}
_VIDEO_EXTS  = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".flv"}
_VIDEO_HOSTS = {"youtube.com", "youtu.be", "vimeo.com", "bilibili.com",
                "twitter.com", "x.com", "instagram.com", "tiktok.com"}


def _url_type(url: str) -> str:
    """Classify URL as 'article', 'image', or 'video'."""
    parsed = urlparse(url.lower())
    path   = parsed.path
    if any(path.endswith(ext) for ext in _IMAGE_EXTS):
        return "image"
    if any(path.endswith(ext) for ext in _VIDEO_EXTS):
        return "video"
    if any(host in parsed.netloc for host in _VIDEO_HOSTS):
        return "video"
    return "article"


def _open_external(url: str) -> None:
    """Open URL with the OS default application."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", url], check=False)
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception as e:
        logger.warning("Failed to open externally: %s", e)


def fetch_full(url: str) -> str | None:
    """Fetch full article text as markdown/plain text.

    Used by the scoring pipeline to enrich short RSS excerpts so that
    embeddings are computed on meaningful content. Not intended for
    user-facing reading — that is the payload consumer's responsibility.

    Tries Firecrawl first if FIRECRAWL_API_KEY is set, then falls back to
    requests + BeautifulSoup for plain-text extraction.

    @return: text string, or None on failure
    """
    result = fetch_full_result(url)
    return result.get("content") or None


def fetch_full_result(url: str, *, max_length: int = 20000) -> dict:
    """Fetch full article text with diagnostics.

    Search Gateway is preferred when configured because it centralizes fetch
    fallbacks and WAF classification for the homeserver stack. Firecrawl and a
    minimal HTTP parser remain local fallbacks for non-homeserver use.
    """
    sg_result = _fetch_via_search_gateway(url, max_length=max_length)
    if sg_result is not None:
        return sg_result

    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if firecrawl_key:
        try:
            from firecrawl import Firecrawl
            doc  = Firecrawl(api_key=firecrawl_key).scrape(url, formats=["markdown"])
            text = (doc.markdown or "").strip()
            if text:
                return {
                    "content": text[:max_length],
                    "engine": "firecrawl",
                    "blocked": False,
                    "error_type": None,
                    "block_reason": None,
                    "upstream_status": None,
                }
        except Exception as e:
            logger.warning("Firecrawl failed: %s — falling back to requests", e)

    try:
        r  = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" in ct:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                return {
                    "content": text[:max_length],
                    "engine": "requests",
                    "blocked": False,
                    "error_type": None,
                    "block_reason": None,
                    "upstream_status": r.status_code,
                }
            except ImportError:
                pass
        return {
            "content": r.text[:max_length],
            "engine": "requests",
            "blocked": False,
            "error_type": None,
            "block_reason": None,
            "upstream_status": r.status_code,
        }
    except Exception as e:
        logger.warning("HTTP fetch failed: %s", e)
        return {
            "content": "",
            "engine": "requests",
            "blocked": False,
            "error_type": "fetch_error",
            "block_reason": str(e)[:200],
            "upstream_status": None,
        }


def _fetch_via_search_gateway(url: str, *, max_length: int) -> dict | None:
    base_url = (
        os.environ.get("SEARCH_GATEWAY_URL")
        or os.environ.get("SG_URL")
        or ""
    ).strip()
    api_key = os.environ.get("SEARCH_GATEWAY_API_KEY", "").strip()
    if not base_url:
        return None

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        resp = requests.post(
            base_url.rstrip("/") + "/fetch",
            json={
                "url": url,
                "max_length": max_length,
                "extract_mode": "markdown",
                "timeout_ms": _SG_FETCH_TIMEOUT_MS,
            },
            headers=headers,
            timeout=_SG_CLIENT_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "content": (data.get("content") or "")[:max_length],
            "engine": data.get("engine") or "search-gateway",
            "blocked": bool(data.get("blocked")),
            "error_type": data.get("error_type"),
            "block_reason": data.get("block_reason"),
            "upstream_status": data.get("upstream_status"),
        }
    except Exception as exc:
        logger.warning("Search Gateway fetch failed: %s — falling back locally", exc)
        return None
