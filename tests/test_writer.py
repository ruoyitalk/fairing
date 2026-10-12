"""Tests for fairing/writer.py — plain Markdown digest output."""
from pathlib import Path
from datetime import date


def _article(url: str = "https://a.com/1", title: str = "Article One",
             source: str = "Test Source", category: str = "DB",
             published: str = "2026-03-20", excerpt: str = "Body text.") -> dict:
    return {"url": url, "title": title, "source": source,
            "category": category, "published": published, "excerpt": excerpt}


# ── write_digest ───────────────────────────────────────────────────────────────

def test_write_digest_creates_file(tmp_path):
    from fairing.writer import write_digest
    path, count = write_digest([_article()], str(tmp_path))
    assert path.exists()
    assert count == 1


def test_write_digest_contains_title(tmp_path):
    from fairing.writer import write_digest
    path, _ = write_digest([_article(title="My Unique Title")], str(tmp_path))
    assert "My Unique Title" in path.read_text(encoding="utf-8")


def test_write_digest_contains_url(tmp_path):
    from fairing.writer import write_digest
    path, _ = write_digest([_article(url="https://unique.example.com/post")], str(tmp_path))
    assert "https://unique.example.com/post" in path.read_text(encoding="utf-8")


def test_write_digest_no_yaml_frontmatter(tmp_path):
    from fairing.writer import write_digest
    path, _ = write_digest([_article()], str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert not text.startswith("---"), "Digest should not have YAML frontmatter"
    assert text.startswith("# Daily Digest")


def test_write_digest_merge_new_articles(tmp_path):
    """Running write_digest twice on the same day merges new articles."""
    from fairing.writer import write_digest
    a1 = _article(url="https://a.com/1", title="First")
    a2 = _article(url="https://a.com/2", title="Second")
    write_digest([a1], str(tmp_path))
    path, count = write_digest([a1, a2], str(tmp_path))
    assert count == 1  # only a2 is new
    assert "Second" in path.read_text(encoding="utf-8")


def test_write_digest_no_duplicate_on_rerun(tmp_path):
    """Running with the same article twice should not duplicate content."""
    from fairing.writer import write_digest
    a = _article(url="https://a.com/1", title="Only Article")
    write_digest([a], str(tmp_path))
    path, count = write_digest([a], str(tmp_path))
    assert count == 0
    assert path.read_text(encoding="utf-8").count("Only Article") == 1


def test_write_digest_week_subdirectory(tmp_path):
    """Output should go into a YYYY-WXX subdirectory."""
    from fairing.writer import write_digest
    path, _ = write_digest([_article()], str(tmp_path))
    assert path.parent.parent == tmp_path
    assert path.parent.name.startswith("20")   # e.g. 2026-W12


def test_write_digest_tiered_display(tmp_path):
    """Articles with score get tiered display: featured + rest section."""
    from fairing.writer import write_digest, TOP_N
    articles = []
    for i in range(TOP_N + 2):
        a = _article(url=f"https://a.com/{i}", title=f"Article {i}")
        a["score"] = 1.0 - i * 0.01
        articles.append(a)
    path, _ = write_digest(articles, str(tmp_path))
    assert "Remaining" in path.read_text(encoding="utf-8")


def test_daily_selection_limits_backlog_and_spreads_sources():
    from fairing.writer import select_digest_articles
    articles = [
        _article(url=f"https://a.com/{i}", title=f"A article {i}", source="A",
                 published=f"2026-10-{30-i:02d}")
        for i in range(12)
    ] + [
        _article(url=f"https://b.com/{i}", title=f"B article {i}", source="B",
                 published=f"2026-09-{30-i:02d}")
        for i in range(12)
    ]
    selected = select_digest_articles(articles, limit=8, per_source=3)
    assert len(selected) == 6
    assert {a["source"] for a in selected} == {"A", "B"}
    assert sum(a["source"] == "B" for a in selected) >= 3
    assert selected[0]["url"] == "https://a.com/0"


def test_digest_respects_source_scope_and_recent_window():
    from fairing.writer import select_digest_articles
    rows = [
        _article(url="https://a.com/new", source="Official", category="AI", published="2026-10-12"),
        _article(url="https://b.com/old", source="Official", category="AI", published="2026-10-01"),
        _article(url="https://c.com/new", source="Library", category="AI", published="2026-10-12"),
    ]
    selected = select_digest_articles(
        rows, source_tiers={"Official": 3, "Library": 0}, as_of=date(2026, 10, 12),
    )
    assert [row["url"] for row in selected] == ["https://a.com/new"]


def test_digest_feedback_overrides_filter_but_not_source_cap():
    from fairing.writer import select_digest_articles
    rows = [
        _article(url="https://a.com/1", title="Developer meetup", source="A"),
        _article(url="https://a.com/2", title="Technical paper", source="A"),
        _article(url="https://a.com/3", title="Model release", source="A"),
        _article(url="https://b.com/1", title="Agent tutorial", source="B"),
    ]
    labels = {
        "https://a.com/1": {"label": 1, "source": "A"},
        "https://a.com/2": {"label": -1, "source": "A"},
    }
    selected = select_digest_articles(
        rows, source_tiers={"A": 2, "B": 2}, labels=labels, per_source=1,
    )
    assert [row["url"] for row in selected] == ["https://a.com/1", "https://b.com/1"]


def test_digest_model_score_is_secondary_to_source_quality_and_deduplicates():
    from fairing.writer import select_digest_articles
    good = _article(url="https://first.example/p?utm_source=rss", title="New model",
                    source="Official", category="AI")
    good["score"] = 0.1
    duplicate = _article(url="https://first.example/p", title="New model",
                         source="Other", category="AI")
    weak = _article(url="https://weak.example/p", title="Another model",
                    source="Other", category="AI")
    weak["score"] = 0.99
    selected = select_digest_articles(
        [weak, duplicate, good], source_tiers={"Official": 3, "Other": 1},
    )
    assert [row["url"] for row in selected] == [good["url"], weak["url"]]


def test_digest_model_score_ranks_equally_fresh_articles_from_same_tier():
    from fairing.writer import select_digest_articles
    low = _article(url="https://a.com/low", title="Low score", source="A")
    high = _article(url="https://b.com/high", title="High score", source="B")
    low["score"] = 0.1
    high["score"] = 0.9
    selected = select_digest_articles([low, high], source_tiers={"A": 2, "B": 2})
    assert [row["url"] for row in selected] == [high["url"], low["url"]]


def test_daily_selection_prioritizes_ai_sources_without_dropping_other_index_entries():
    from fairing.writer import select_digest_articles
    database = _article(url="https://db.example/1", category="Database", published="2026-10-12")
    ai = _article(url="https://ai.example/1", category="AI / Research", published="2026-10-11")
    articles = [database, ai]
    selected = select_digest_articles(articles, limit=1)
    assert selected == [ai]
    assert articles == [database, ai]


def test_write_chinese_note_uses_plain_chinese_headings(tmp_path):
    from fairing.writer import write_chinese
    article = _article(title="Original title")
    article["title_zh"] = "中文标题"
    article["summary_zh"] = "事实摘要。"
    path, count = write_chinese([article], str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert count == 1
    assert path.name.endswith("-zh.md")
    assert text.startswith("# 每日资讯")
    assert "中文标题" in text and "事实摘要。" in text
    assert "Daily Digest" not in text


# ── _existing_urls ─────────────────────────────────────────────────────────────

def test_existing_urls_parses_digest(tmp_path):
    from fairing.writer import _existing_urls
    note = tmp_path / "note.md"
    note.write_text(
        "| URL | [https://arxiv.org/abs/123](https://arxiv.org/abs/123) |\n"
        "| URL | [https://example.com/post](https://example.com/post) |\n",
        encoding="utf-8",
    )
    urls = _existing_urls(note)
    assert "https://arxiv.org/abs/123" in urls
    assert "https://example.com/post" in urls


def test_existing_urls_returns_empty_for_missing_file(tmp_path):
    from fairing.writer import _existing_urls
    assert _existing_urls(tmp_path / "nonexistent.md") == set()
