"""Read the local daily note as plain article fields for the web page."""

import re
import html
from pathlib import Path

from .web_articles import is_web_url


_FILE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})(-zh)?\.md$")
_URL_CELL = re.compile(r"^\| URL \| \[[^\]]*\]\((https?://[^\s)]+)\) \|$")


def plain_markdown(value: str) -> str:
    """Show feed text without interpreting its links, images, or formatting."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", html.escape(str(value)))


def parse_digest(text: str) -> list[dict[str, str]]:
    """Extract writer-generated article blocks without rendering feed Markdown."""
    articles: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    category = ""

    def finish() -> None:
        if current and current["title"] and is_web_url(current["url"]):
            articles.append(current.copy())

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            finish()
            current = {"title": line[4:][:180], "source": "", "category": category,
                       "published": "", "url": "", "summary": ""}
            continue
        if line.startswith("## "):
            finish()
            current = None
            category = line.split(" · ", 1)[1][:100] if " · " in line else ""
            continue
        if line == "---":
            finish()
            current = None
            category = ""
            continue
        if current is None:
            continue
        if line.startswith("| Source |"):
            current["source"] = line.removeprefix("| Source |").removesuffix("|").strip()[:100]
        elif line.startswith("| Published |"):
            current["published"] = line.removeprefix("| Published |").removesuffix("|").strip()[:40]
        elif line.startswith("| URL |"):
            match = _URL_CELL.fullmatch(line)
            if match:
                current["url"] = match.group(1)
        elif line and not line.startswith(("|", "![")):
            current["summary"] = (current["summary"] + " " + line).strip()[:500]
    finish()
    return articles


def latest_digest(news_dir: Path) -> tuple[Path, str, bool, list[dict[str, str]]] | None:
    """Find the newest readable note, preferring Chinese on the same date."""
    if not news_dir.is_dir():
        return None
    candidates = []
    for path in news_dir.glob("*/*.md"):
        match = _FILE_NAME.fullmatch(path.name)
        if match and path.is_file():
            candidates.append((match.group(1), bool(match.group(2)), path))
    for day, chinese, path in sorted(candidates, reverse=True):
        try:
            articles = parse_digest(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if articles:
            return path, day, chinese, articles
    return None
