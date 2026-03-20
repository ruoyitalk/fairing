"""Tests for fairing/writer.py — Obsidian notes, NotebookLM output, vault archiving."""
import pytest
from pathlib import Path


def _article(url: str = "https://a.com/1", title: str = "Article One",
             source: str = "Test Source", category: str = "DB",
             published: str = "2026-03-20", excerpt: str = "Body text.") -> dict:
    return {"url": url, "title": title, "source": source,
            "category": category, "published": published, "excerpt": excerpt}


# ── write_obsidian ─────────────────────────────────────────────────────────────

def test_write_obsidian_creates_file(tmp_path):
    from fairing.writer import write_obsidian
    path, count = write_obsidian([_article()], str(tmp_path))
    assert path.exists()
    assert count == 1


def test_write_obsidian_contains_title(tmp_path):
    from fairing.writer import write_obsidian
    path, _ = write_obsidian([_article(title="My Unique Title")], str(tmp_path))
    assert "My Unique Title" in path.read_text(encoding="utf-8")


def test_write_obsidian_contains_url(tmp_path):
    from fairing.writer import write_obsidian
    path, _ = write_obsidian([_article(url="https://unique.example.com/post")], str(tmp_path))
    assert "https://unique.example.com/post" in path.read_text(encoding="utf-8")


def test_write_obsidian_yaml_frontmatter(tmp_path):
    from fairing.writer import write_obsidian
    path, _ = write_obsidian([_article()], str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "date:" in text
    assert "tags:" in text


def test_write_obsidian_merge_new_articles(tmp_path):
    """Running write_obsidian twice on the same day merges new articles."""
    from fairing.writer import write_obsidian
    a1 = _article(url="https://a.com/1", title="First")
    a2 = _article(url="https://a.com/2", title="Second")
    write_obsidian([a1], str(tmp_path))
    path, count = write_obsidian([a1, a2], str(tmp_path))
    assert count == 1  # only a2 is new
    text = path.read_text(encoding="utf-8")
    assert "Second" in text


def test_write_obsidian_no_duplicate_on_rerun(tmp_path):
    """Running with the same article twice should not duplicate content."""
    from fairing.writer import write_obsidian
    a = _article(url="https://a.com/1", title="Only Article")
    write_obsidian([a], str(tmp_path))
    path, count = write_obsidian([a], str(tmp_path))
    assert count == 0
    text = path.read_text(encoding="utf-8")
    assert text.count("Only Article") == 1


def test_write_obsidian_week_subdirectory(tmp_path):
    """Output should go into a YYYY-WXX subdirectory."""
    from fairing.writer import write_obsidian
    path, _ = write_obsidian([_article()], str(tmp_path))
    # Path: tmp_path/<YYYY-WXX>/<YYYY-MM-DD>.md
    assert path.parent.parent == tmp_path
    assert path.parent.name.startswith("20")   # e.g. 2026-W12


def test_write_obsidian_tiered_display(tmp_path):
    """Articles with score get tiered display: featured + rest section."""
    from fairing.writer import write_obsidian, TOP_N
    articles = []
    for i in range(TOP_N + 2):
        a = _article(url=f"https://a.com/{i}", title=f"Article {i}")
        a["score"] = 1.0 - i * 0.01
        articles.append(a)
    path, _ = write_obsidian(articles, str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert "其余" in text


# ── write_notebooklm ───────────────────────────────────────────────────────────

def test_write_notebooklm_creates_file(tmp_path):
    from fairing.writer import write_notebooklm
    path = write_notebooklm([_article()], str(tmp_path))
    assert path.exists()


def test_write_notebooklm_contains_source_header(tmp_path):
    from fairing.writer import write_notebooklm
    path = write_notebooklm([_article(source="ArXiv CS")], str(tmp_path))
    assert "ArXiv CS" in path.read_text(encoding="utf-8")


def test_write_notebooklm_multiple_sources(tmp_path):
    from fairing.writer import write_notebooklm
    articles = [
        _article(url="https://a.com/1", source="SourceA"),
        _article(url="https://b.com/1", source="SourceB"),
    ]
    path = write_notebooklm(articles, str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert "SourceA" in text
    assert "SourceB" in text


# ── archive_vault ──────────────────────────────────────────────────────────────

def test_archive_vault_moves_date_files(tmp_path):
    from fairing.writer import archive_vault
    # Create a loose date file in vault root
    (tmp_path / "2026-03-15.md").write_text("# Digest", encoding="utf-8")
    moved = archive_vault(str(tmp_path))
    assert moved == 1
    # File should no longer be in root
    assert not (tmp_path / "2026-03-15.md").exists()


def test_archive_vault_preserves_non_date_files(tmp_path):
    from fairing.writer import archive_vault
    (tmp_path / "README.md").write_text("Welcome", encoding="utf-8")
    archive_vault(str(tmp_path))
    assert (tmp_path / "README.md").exists()


def test_archive_vault_handles_empty_vault(tmp_path):
    from fairing.writer import archive_vault
    assert archive_vault(str(tmp_path)) == 0


def test_archive_vault_handles_missing_vault():
    from fairing.writer import archive_vault
    assert archive_vault("/nonexistent/vault/path") == 0


# ── _existing_urls ─────────────────────────────────────────────────────────────

def test_existing_urls_parses_obsidian_note(tmp_path):
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
