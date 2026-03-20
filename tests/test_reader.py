"""Tests for fairing/reader.py — URL type detection, slug, readnote saving."""
import pytest
from pathlib import Path


# ── _url_type ──────────────────────────────────────────────────────────────────

def test_url_type_article():
    from fairing.reader import _url_type
    assert _url_type("https://arxiv.org/abs/2501.12345") == "article"
    assert _url_type("https://example.com/blog/post-1") == "article"


def test_url_type_image():
    from fairing.reader import _url_type
    assert _url_type("https://example.com/figure.png")  == "image"
    assert _url_type("https://cdn.example.com/photo.jpg") == "image"
    assert _url_type("https://img.example.com/x.webp")  == "image"


def test_url_type_video_extension():
    from fairing.reader import _url_type
    assert _url_type("https://example.com/demo.mp4")  == "video"
    assert _url_type("https://example.com/talk.webm") == "video"


def test_url_type_video_domain():
    from fairing.reader import _url_type
    assert _url_type("https://youtube.com/watch?v=abc") == "video"
    assert _url_type("https://youtu.be/abc123")         == "video"
    assert _url_type("https://vimeo.com/123456")        == "video"
    assert _url_type("https://bilibili.com/video/BV1") == "video"


def test_url_type_case_insensitive():
    from fairing.reader import _url_type
    assert _url_type("HTTPS://EXAMPLE.COM/PHOTO.JPG") == "image"


# ── _slug ──────────────────────────────────────────────────────────────────────

def test_slug_basic():
    from fairing.reader import _slug
    assert _slug("Hello World") == "hello-world"


def test_slug_strips_special_chars():
    from fairing.reader import _slug
    result = _slug("A New Lower Bounding Paradigm: Tighter Bounds!")
    assert ":" not in result
    assert "!" not in result


def test_slug_truncates_to_max_len():
    from fairing.reader import _slug
    long_title = "word " * 20
    assert len(_slug(long_title)) <= 40


def test_slug_empty_title_fallback():
    from fairing.reader import _slug
    assert _slug("") == "article"
    assert _slug("!!! ???") == "article"


def test_slug_no_leading_trailing_hyphens():
    from fairing.reader import _slug
    result = _slug("  Hello World  ")
    assert not result.startswith("-")
    assert not result.endswith("-")


# ── save_readnote ──────────────────────────────────────────────────────────────

def test_save_readnote_creates_file(tmp_path):
    from fairing.reader import save_readnote
    path = save_readnote(
        url="https://example.com/article",
        title="Test Article",
        content="This is the body.",
        source="Example Blog",
        readnotes_dir=tmp_path,
        translated=False,
    )
    assert path.exists()
    assert path.suffix == ".md"


def test_save_readnote_frontmatter(tmp_path):
    from fairing.reader import save_readnote
    path = save_readnote(
        url="https://example.com/article",
        title="Frontmatter Test",
        content="Body text here.",
        source="My Source",
        readnotes_dir=tmp_path,
        translated=False,
    )
    text = path.read_text(encoding="utf-8")
    assert "---" in text
    assert "url: https://example.com/article" in text
    assert "source: My Source" in text
    assert "translated: false" in text
    assert "tags: [readnote]" in text


def test_save_readnote_translated_flag(tmp_path):
    from fairing.reader import save_readnote
    path = save_readnote(
        url="https://example.com/a",
        title="Test",
        content="Content",
        source="Src",
        readnotes_dir=tmp_path,
        translated=True,
    )
    assert "translated: true" in path.read_text(encoding="utf-8")


def test_save_readnote_creates_dir_if_missing(tmp_path):
    from fairing.reader import save_readnote
    nested = tmp_path / "deep" / "readnotes"
    save_readnote(
        url="https://x.com/a",
        title="X",
        content="C",
        source="S",
        readnotes_dir=nested,
    )
    assert nested.exists()


def test_save_readnote_filename_contains_date(tmp_path):
    from fairing.reader import save_readnote
    from fairing.state import today_beijing
    path = save_readnote(
        url="https://x.com/a",
        title="Date Test",
        content="C",
        source="S",
        readnotes_dir=tmp_path,
    )
    assert today_beijing() in path.name
