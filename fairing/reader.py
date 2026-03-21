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
import subprocess
import sys
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

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
    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if firecrawl_key:
        try:
            from firecrawl import Firecrawl
            doc  = Firecrawl(api_key=firecrawl_key).scrape(url, formats=["markdown"])
            text = (doc.markdown or "").strip()
            if text:
                return text
        except Exception as e:
            logger.warning("Firecrawl failed: %s — falling back to requests", e)

    try:
        import requests
        r  = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" in ct:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                return soup.get_text(separator="\n", strip=True)[:20000]
            except ImportError:
                pass
        return r.text[:20000]
    except Exception as e:
        logger.warning("HTTP fetch failed: %s", e)
        return None