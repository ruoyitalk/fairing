"""Legacy McKinsey parsing helpers; hosted scraping is disabled.

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
    """Keep the old call signature but never invoke a hosted scraping API."""
    logger.info("McKinsey scraping skipped in the free-only route")
    return []
