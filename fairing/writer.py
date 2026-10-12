"""Output writers for fairing digests.

Writes plain Markdown daily digest files to NEWS_DIR/YYYY-WXX/YYYY-MM-DD.md.
Same-day re-runs auto-merge new articles (dedup by URL).
"""
import pathlib
import re
from datetime import date, datetime
from typing import Mapping


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
    ]
    if isinstance(a.get("score"), (int, float)):
        lines.append(f"| Score | {max(0.0, min(1.0, a['score'])):.4f} |")
    lines.append("")
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


def select_digest_articles(
    articles: list[dict], limit: int = 20, per_source: int = 2,
    *, source_tiers: Mapping[str, int] | None = None,
    labels: Mapping[str, dict] | None = None,
    as_of: date | None = None, max_age_days: int = 7,
) -> list[dict]:
    """Select a small, recent, source-diverse digest from the article archive.

    Source tiers express editorial scope, while the model score and observed
    source feedback are bounded secondary signals. A negative label or an
    obvious promotion cannot be rescued by a high model score. A positive
    label can restore an automatically filtered article. No quota is filled
    with extra articles from an already saturated source.
    """
    from .editorial import is_default_filtered
    from .state import normalize_title, normalize_url

    labels = labels or {}
    source_labels: dict[str, list[int]] = {}
    for row in labels.values():
        source = str(row.get("source") or "")
        if source and row.get("label") in (-1, 1):
            source_labels.setdefault(source, []).append(row["label"])

    candidates: list[tuple[float, str, dict]] = []
    for article in articles:
        url = str(article.get("url") or "")
        source = str(article.get("source") or "")
        if not url or not source:
            continue
        label = labels.get(url, {}).get("label")
        if label == -1 or (label != 1 and is_default_filtered(article)):
            continue
        if source_tiers is None:
            tags = article.get("tags") or []
            tier = 2 if ("ai" in tags or "ai" in str(article.get("category") or "").casefold()) else 1
        else:
            tier = source_tiers.get(source, 0)
        if tier <= 0 and label != 1:
            continue

        published = str(article.get("published") or article.get("date") or "")
        age = max_age_days - 1
        if as_of is not None:
            try:
                age = (as_of - date.fromisoformat(published[:10])).days
            except ValueError:
                continue
            if age < -1 or age >= max_age_days:
                continue

        opinions = source_labels.get(source, [])
        source_signal = 2 * sum(opinions) / (len(opinions) + 8)
        model_signal = 0.0
        if isinstance(article.get("score"), (int, float)):
            model_signal = 4 * (max(0.0, min(1.0, article["score"])) - 0.5)
        rank = (4 * max(tier, 0) + max(0, max_age_days - max(age, 0))
                + source_signal + model_signal + (20 if label == 1 else 0))
        candidates.append((rank, published, article))

    candidates.sort(key=lambda item: (item[0], item[1],
                                      str(item[2].get("source") or ""),
                                      str(item[2].get("url") or "")), reverse=True)
    selected: list[dict] = []
    source_counts: dict[str, int] = {}
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for _, _, article in candidates:
        source = str(article["source"])
        url = normalize_url(article["url"])
        title = normalize_title(str(article.get("title") or ""))
        if url in seen_urls or (title and title in seen_titles):
            continue
        if source_counts.get(source, 0) >= per_source:
            continue
        selected.append(article)
        source_counts[source] = source_counts.get(source, 0) + 1
        seen_urls.add(url)
        if title:
            seen_titles.add(title)
        if len(selected) >= limit:
            break
    return selected


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
    if use_zh:
        summary = (f"精选 {len(featured)} 篇，另有 {len(rest)} 篇标题"
                   if scored else f"共 {len(articles)} 篇")
        heading = "每日资讯"
        source_summary = f"{summary}，来自 {len(sources)} 个来源"
    else:
        summary = (f"{len(featured)} full + {len(rest)} titles"
                   if scored else f"{len(articles)} articles")
        heading = "Daily Digest"
        source_summary = f"{summary} from {len(sources)} sources"

    lines: list[str] = [
        f"# {heading} — {today_str}",
        "",
        f"> {source_summary}",
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
        section = f"其他 {len(rest)} 篇" if use_zh else f"Remaining {len(rest)} articles"
        lines += ["", "---", "", f"## {section}", ""]
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
        (f"## 补充 — {now_str}（新增 {len(new_articles)} 篇）" if use_zh
         else f"## Merged — {now_str}  (+{len(new_articles)} articles)"),
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
    from .state import today_beijing
    today = date.fromisoformat(today_beijing())
    today_str = today.isoformat()
    out_dir = pathlib.Path(output_dir) / _week_dir(today)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _write_or_merge(out_dir / f"{today_str}.md", articles, use_zh=False, today_str=today_str)


def write_chinese(articles: list[dict], output_dir: str) -> tuple[pathlib.Path, int]:
    """Write/merge Chinese daily digest to output_dir/YYYY-WXX/YYYY-MM-DD-zh.md.

    Articles must already have 'title_zh' and 'summary_zh' fields populated by translator.

    @return: (path, new_article_count)
    """
    from .state import today_beijing
    today = date.fromisoformat(today_beijing())
    today_str = today.isoformat()
    out_dir = pathlib.Path(output_dir) / _week_dir(today)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _write_or_merge(out_dir / f"{today_str}-zh.md", articles, use_zh=True, today_str=today_str)
