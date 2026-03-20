"""Scrape McKinsey insight pages via Firecrawl API.

Requires FIRECRAWL_API_KEY in .env (free tier: 500 credits/month at firecrawl.dev).
Without it, McKinsey pages are skipped — they block headless browsers at the network level.

Extraction strategy:
  McKinsey pages render article cards as h5 headings in Firecrawl markdown:
    ##### [Article Title](https://www.mckinsey.com/...)
  We match only this pattern, ignoring nav links, image captions, and UI elements.
"""
import logging
import re
from datetime import datetime

from .config import McKinseySource

logger = logging.getLogger(__name__)

# Article cards appear as h5 links: ##### [Title](URL)
_ARTICLE_PATTERN = re.compile(
    r"^#{1,6}\s+\[([^\]]{15,200})\]\((https://www\.mckinsey\.com/[^\)]+)\)",
    re.MULTILINE,
)

# URL segments that indicate non-article pages
_SKIP_URL_SEGMENTS = (
    "/about", "/careers", "/privacy", "/contact", "/how-we-help",
    "/new-at-mckinsey", "#", "mailto:",
)

# Limit per page to avoid overwhelming daily digest
_MAX_PER_PAGE = 12


def _extract_articles(markdown: str, source_name: str) -> list[dict]:
    """Extract article entries from Firecrawl markdown using heading-link pattern."""
    articles: list[dict] = []
    seen: set[str] = set()

    for match in _ARTICLE_PATTERN.finditer(markdown):
        title = match.group(1).strip()
        url = match.group(2).strip()

        if any(seg in url for seg in _SKIP_URL_SEGMENTS):
            continue
        if url in seen:
            continue
        seen.add(url)

        # Parse date and excerpt from lines following the heading
        after = markdown[match.end():match.end() + 500].strip()
        published = "unknown"
        excerpt = ""

        for ln in after.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith("!") or ln.startswith("[") or ln.startswith("#"):
                continue   # skip images, links, headings
            # Try to extract a date like "March 5, 2026" or "June 13, 2025 \-"
            date_match = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+\d{1,2},\s+\d{4}", ln
            )
            if date_match and published == "unknown":
                try:
                    published = datetime.strptime(
                        date_match.group(0), "%B %d, %Y"
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass
                continue   # date line itself is not the excerpt
            # Skip short nav items (Learn more, Contact us, etc.)
            if len(ln) < 25:
                continue
            # First substantial line is the excerpt
            excerpt = re.sub(r"[\\*_`]", "", ln)[:200]
            break

        articles.append({
            "source": source_name,
            "category": "Strategy / AI",
            "title": title,
            "url": url,
            "published": published,
            "excerpt": excerpt,
            "image_url": "",
        })

        if len(articles) >= _MAX_PER_PAGE:
            break

    return articles


def fetch_mckinsey(sources: list[McKinseySource], api_key: str) -> list[dict]:
    """Scrape McKinsey pages using Firecrawl.

    @param sources: list of McKinseySource from config
    @param api_key: Firecrawl API key
    @return: list of article dicts (capped at _MAX_PER_PAGE per source)
    """
    if not api_key:
        logger.warning(
            "FIRECRAWL_API_KEY not set — McKinsey pages skipped. "
            "Get a free key at https://firecrawl.dev and add it to .env"
        )
        return []

    from firecrawl import Firecrawl
    app = Firecrawl(api_key=api_key)

    articles: list[dict] = []
    for source in sources:
        try:
            doc = app.scrape(source.url, formats=["markdown"])
            markdown = doc.markdown or ""
            found = _extract_articles(markdown, source.name)
            logger.info("[%s] %d articles extracted (cap %d)", source.name, len(found), _MAX_PER_PAGE)
            articles.extend(found)
        except Exception as exc:
            logger.warning("!" * 50)
            logger.warning("FIRECRAWL FAILED — McKinsey [%s] skipped", source.name)
            logger.warning("Reason: %s", exc)
            logger.warning("!" * 50)

    return articles
