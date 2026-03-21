"""Tests for fairing/writer.py — plain Markdown digest output."""
from pathlib import Path


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
