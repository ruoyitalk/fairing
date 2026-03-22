"""Output writers for fairing digests.

Writes plain Markdown daily digest files to NEWS_DIR/YYYY-WXX/YYYY-MM-DD.md.
Same-day re-runs auto-merge new articles (dedup by URL).
"""
import pathlib
import re
from datetime import date, datetime


# ── helpers ───────────────────────────────────────────────────────────────────

def _group_by_source(articles: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for a in articles:
        groups.setdefault(a["source"], []).append(a)
    return groups


def _week_dir(d: date) -> str:
    cal = d.isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


def _existing_urls(path: pathlib.Path) -> set[str]:
    """Extract all article URLs already present in a digest file."""
    if not path.exists():
        return set()
    content = path.read_text(encoding="utf-8")
    return set(re.findall(r"\| URL \| \[.*?\]\((https?://[^\)]+)\)", content))


def _article_block(a: dict, use_zh: bool = False) -> list[str]:
    from .export import article_id_for
    aid = article_id_for(a["url"])
    title = a.get("title_zh", a["title"]) if use_zh else a["title"]
    excerpt = a.get("summary_zh", a["excerpt"]) if use_zh else a["excerpt"]
    image_url = a.get("image_url", "")
    lines = [
        f"### {title}",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| ID | `{aid}` |",
        f"| Source | {a['source']} |",
        f"| Category | {a['category']} |",
        f"| Published | {a['published']} |",
        f"| URL | [{a['url']}]({a['url']}) |",
        "",
    ]
    if image_url:
        lines += [f"![cover]({image_url})", ""]
    if excerpt:
        lines += [excerpt, ""]
    return lines


# ── digest writer ─────────────────────────────────────────────────────────────

_TOP_RATIO = 0.3
_TOP_MIN   = 5
_TOP_MAX   = 50


def top_n(total: int) -> int:
    """Number of articles shown with full detail (title + excerpt + source).
    Proportional to article count: 30% of total, clamped to [5, 50].
    """
    return max(_TOP_MIN, min(_TOP_MAX, round(total * _TOP_RATIO)))


# Compatibility alias for tests that import TOP_N directly.
TOP_N = _TOP_MAX


def _build_digest_lines(articles: list[dict], today_str: str,
                         use_zh: bool = False) -> list[str]:
    """Build plain Markdown digest lines with tiered display when articles are scored.

    If articles have a 'score' field (model is active), the first top_n are
    shown in full; the remainder appear as a title-only list.
    Without scores, all articles are shown in full.
    """
    scored   = any("score" in a for a in articles)
    n        = top_n(len(articles))
    featured = articles[:n] if scored else articles
    rest     = articles[n:]  if scored else []

    sources  = list(dict.fromkeys(a["source"] for a in articles))
    summary  = (f"{len(featured)} full + {len(rest)} titles"
                if scored else f"{len(articles)} articles")

    lines: list[str] = [
        f"# Daily Digest — {today_str}",
        "",
        f"> {summary} from {len(sources)} sources",
        "",
    ]

    for source_name, items in _group_by_source(featured).items():
        category = items[0].get("category", "")
        lines += [
            f"## {source_name}  ·  {category}",
            "",
        ]
        for a in items:
            lines += _article_block(a, use_zh=use_zh)

    if rest:
        lines += ["", "---", "", f"## Remaining {len(rest)} articles", ""]
        for a in rest:
            from .export import article_id_for
            aid = article_id_for(a["url"])
            score_str = f" `{a['score']:.2f}`" if "score" in a else ""
            lines.append(
                f"- `{aid}` [{a['title']}]({a['url']})"
                f"  *{a['source']}*{score_str}"
            )
        lines.append("")

    return lines


def _write_or_merge(out_path: pathlib.Path, articles: list[dict],
                    use_zh: bool, today_str: str) -> tuple[pathlib.Path, int]:
    """Write a new file or append new articles to an existing one.

    @return: (path, count_new) where count_new is number of articles actually written.
    """
    existing_urls = _existing_urls(out_path)
    new_articles  = [a for a in articles if a["url"] not in existing_urls]

    if not out_path.exists():
        lines = _build_digest_lines(articles, today_str, use_zh)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path, len(articles)

    if not new_articles:
        return out_path, 0

    now_str = datetime.now().strftime("%H:%M")
    append_lines = [
        "",
        "---",
        "",
        f"## Merged — {now_str}  (+{len(new_articles)} articles)",
        "",
    ]
    for source_name, items in _group_by_source(new_articles).items():
        category = items[0].get("category", "")
        append_lines += [f"## {source_name}  ·  {category}", ""]
        for a in items:
            append_lines += _article_block(a, use_zh=use_zh)

    existing = out_path.read_text(encoding="utf-8")
    out_path.write_text(existing + "\n" + "\n".join(append_lines), encoding="utf-8")
    return out_path, len(new_articles)


def write_digest(articles: list[dict], output_dir: str) -> tuple[pathlib.Path, int]:
    """Write/merge English daily digest to output_dir/YYYY-WXX/YYYY-MM-DD.md.

    @return: (path, new_article_count)
    """
    today = date.today()
    today_str = today.isoformat()
    out_dir = pathlib.Path(output_dir) / _week_dir(today)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _write_or_merge(out_dir / f"{today_str}.md", articles, use_zh=False, today_str=today_str)


def write_chinese(articles: list[dict], output_dir: str) -> tuple[pathlib.Path, int]:
    """Write/merge Chinese daily digest to output_dir/YYYY-WXX/YYYY-MM-DD-zh.md.

    Articles must already have 'title_zh' and 'summary_zh' fields populated by translator.

    @return: (path, new_article_count)
    """
    today = date.today()
    today_str = today.isoformat()
    out_dir = pathlib.Path(output_dir) / _week_dir(today)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _write_or_merge(out_dir / f"{today_str}-zh.md", articles, use_zh=True, today_str=today_str)
