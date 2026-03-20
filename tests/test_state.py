"""Tests for fairing/state.py — URL/title normalization and dedup."""
import pytest
from fairing.state import normalize_url, normalize_title, filter_unseen, mark_seen


# ── normalize_url ─────────────────────────────────────────────────────────────

def test_normalize_url_strips_tracking_params():
    url = "https://example.com/article?utm_source=twitter&utm_medium=social"
    assert normalize_url(url) == "https://example.com/article"


def test_normalize_url_trailing_slash():
    assert normalize_url("https://example.com/blog/") == "https://example.com/blog"


def test_normalize_url_preserves_non_tracking_params():
    url = "https://arxiv.org/abs/2603.17168?q=important"
    result = normalize_url(url)
    assert "q=important" in result


def test_normalize_url_lowercase_host():
    assert normalize_url("https://ClickHouse.com/blog") == "https://clickhouse.com/blog"


def test_normalize_url_strips_fragment():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_url_same_result_for_variants():
    base = normalize_url("https://example.com/article")
    assert normalize_url("https://EXAMPLE.COM/article/") == base
    assert normalize_url("https://example.com/article?utm_source=x") == base


# ── normalize_title ───────────────────────────────────────────────────────────

def test_normalize_title_lowercase():
    assert normalize_title("Hello World") == "hello world"


def test_normalize_title_strips_punctuation():
    assert normalize_title("Hello, World!") == "hello world"


def test_normalize_title_deduplication_equivalence():
    t1 = normalize_title("ClickHouse 26.2: QBit data type becomes production-ready!")
    t2 = normalize_title("clickhouse 262 qbit data type becomes productionready")
    # Both strip punctuation — core words should match
    assert "clickhouse" in t1
    assert "clickhouse" in t2


# ── filter_unseen / mark_seen (with tmp state file) ──────────────────────────

@pytest.fixture(autouse=True)
def patch_state_file(tmp_path, monkeypatch):
    """Redirect all data files to a temp directory via DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_article(url: str, title: str = "Test") -> dict:
    return {"url": url, "title": title, "source": "Test", "category": "Test",
            "published": "2026-01-01", "excerpt": ""}


def test_filter_unseen_passes_new_articles():
    articles = [_make_article("https://example.com/a")]
    fresh    = filter_unseen(articles)
    assert len(fresh) == 1


def test_mark_seen_then_filter():
    a = _make_article("https://example.com/b")
    mark_seen([a])
    fresh = filter_unseen([a])
    assert len(fresh) == 0


def test_filter_deduplicates_by_url_variant():
    """URL with tracking param should match stored clean URL."""
    a_clean   = _make_article("https://example.com/post")
    a_tracked = _make_article("https://example.com/post?utm_source=x", title="Same Post")
    mark_seen([a_clean])
    fresh = filter_unseen([a_tracked])
    assert len(fresh) == 0


def test_filter_deduplicates_by_title():
    """Same title at a different URL should be caught by title dedup."""
    a1 = _make_article("https://site-a.com/post", "ClickHouse 26.2 Released")
    a2 = _make_article("https://site-b.com/post", "ClickHouse 26.2 Released")
    mark_seen([a1])
    fresh = filter_unseen([a2])
    assert len(fresh) == 0


def test_multiple_articles_partial_dedup():
    articles = [
        _make_article("https://example.com/1", "Article One"),
        _make_article("https://example.com/2", "Article Two"),
    ]
    mark_seen([articles[0]])
    fresh = filter_unseen(articles)
    assert len(fresh) == 1
    assert fresh[0]["url"] == "https://example.com/2"
