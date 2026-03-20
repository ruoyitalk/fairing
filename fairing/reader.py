"""Deep-read an article: fetch full content and display with optional translation.

Content routing:
  - Article URLs  → fetch full text (Firecrawl if key present, else requests)
                    → write to temp .md file → open in $EDITOR (vim) or less
  - Image URLs    → open with OS default viewer
  - Video URLs    → open with OS default player / browser
"""
import logging
import os
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

import re

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


def _slug(title: str, max_len: int = 40) -> str:
    """Convert title to a safe filename slug."""
    s = re.sub(r"[^\w\s-]", "", title.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_len] or "article"


def save_readnote(url: str, title: str, content: str, source: str,
                  readnotes_dir: Path, translated: bool = False) -> Path:
    """Write fetched article content to a dated readnote markdown file.

    Format mirrors Obsidian convention with YAML frontmatter.

    @return: path of the written file
    """
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    readnotes_dir.mkdir(parents=True, exist_ok=True)
    filename  = f"{today}-{_slug(title)}.md"
    out_path  = readnotes_dir / filename

    frontmatter = (
        f"---\n"
        f"title: \"{title.replace(chr(34), chr(39))}\"\n"
        f"url: {url}\n"
        f"source: {source}\n"
        f"read_date: {today}\n"
        f"translated: {'true' if translated else 'false'}\n"
        f"tags: [readnote]\n"
        f"---\n\n"
    )
    out_path.write_text(frontmatter + content, encoding="utf-8")
    logger.info("Readnote saved → %s", out_path)
    return out_path


def read_article(url: str, title: str = "", translate: bool = False) -> str | None:
    """Deep-read an article.

    Routes by content type:
      - image / video → open externally, return None
      - article       → fetch full text, optionally translate, open in editor,
                        return the final markdown content string

    @param url:       article URL
    @param title:     article title for display header
    @param translate: if True, translate title + content to Chinese before displaying
    @return:          rendered content string (for saving), or None for media
    """
    kind = _url_type(url)
    if kind in ("image", "video"):
        _open_external(url)
        return None

    content = fetch_full(url)
    if content is None:
        logger.warning("Could not fetch content — opening in browser instead")
        _open_external(url)
        return None

    if translate:
        try:
            import copy
            from fairing.translator import translate as _translate
            dummy = [{
                "title":     title or url,
                "excerpt":   content[:4000],
                "url":       url,
                "source":    "",
                "published": "",
                "category":  "",
            }]
            result = _translate(copy.deepcopy(dummy))
            if result:
                title_zh   = result[0].get("title_zh") or title
                summary_zh = result[0].get("summary_zh", "")
                content = (
                    f"**（翻译摘要）**\n\n{summary_zh}\n\n"
                    f"---\n\n**（原文）**\n\n{content}"
                )
                title = title_zh
        except Exception as e:
            logger.warning("Translation failed: %s", e)

    header  = f"# {title}\n\n> {url}\n\n" if title else f"# {url}\n\n"
    payload = header + content

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(payload)
        tmp = f.name

    # Editor priority: $EDITOR → platform default → browser fallback
    editor = os.environ.get("EDITOR", "")
    opened = False
    if editor:
        try:
            subprocess.run([editor, tmp])
            opened = True
        except FileNotFoundError:
            pass
    if not opened:
        if sys.platform == "win32":
            try:
                os.startfile(tmp)   # opens with default Windows app
                opened = True
            except Exception:
                pass
        else:
            for fallback in (["vim", tmp], ["nano", tmp], ["less", tmp]):
                try:
                    subprocess.run(fallback)
                    opened = True
                    break
                except FileNotFoundError:
                    continue
    if not opened:
        _open_external(url)
    try:
        os.unlink(tmp)
    except OSError:
        pass

    return payload
