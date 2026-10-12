"""Read-only article selection for the Fairing web page."""

from urllib.parse import urlsplit


def is_web_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except (TypeError, ValueError):
        return False


def latest_articles(rows: list[dict], *, query: str = "", source: str = "",
                    limit: int = 30) -> list[dict]:
    words = query.casefold().split()
    seen: set[str] = set()
    selected: list[dict] = []
    for row in sorted(rows, key=lambda item: item.get("date") or "", reverse=True):
        url = row.get("url", "")
        if not is_web_url(url) or url in seen:
            continue
        seen.add(url)
        if source and row.get("source") != source:
            continue
        title = str(row.get("title") or "")
        haystack = f"{title} {row.get('source') or ''}".casefold()
        if all(word in haystack for word in words):
            selected.append(row)
        if len(selected) >= limit:
            break
    return selected
