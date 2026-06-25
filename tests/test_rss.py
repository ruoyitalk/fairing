"""Tests for fairing/rss.py — disabled source filtering, CJK exclusion, date parsing."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from fairing.config import RssSource


def _source(name: str = "Test", url: str = "https://example.com/rss",
            category: str = "Tech", enabled: bool = True) -> RssSource:
    return RssSource(name=name, url=url, category=category, enabled=enabled)


def _fake_entry(title: str = "Test Article", link: str = "https://example.com/1",
                summary: str = "A summary.", pub_dt: datetime | None = None) -> MagicMock:
    """Build a fake feedparser entry with a publication date inside lookback window.

    rss.py accesses title/link via entry.get(), and summary via getattr(),
    so both paths must be mocked correctly.
    """
    entry = MagicMock()
    entry.title   = title
    entry.link    = link
    entry.summary = summary
    # rss.py calls entry.get("title", "") and entry.get("link", "")
    _lookup = {"title": title, "link": link}
    entry.get.side_effect = lambda key, default="": _lookup.get(key, default)
    # Image extraction attributes
    entry.media_content   = []
    entry.media_thumbnail = []
    entry.enclosures      = []
    if pub_dt is None:
        pub_dt = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    entry.published_parsed = pub_dt.timetuple()
    entry.updated_parsed   = None
    return entry


def _fake_feed(entries: list) -> MagicMock:
    feed = MagicMock()
    feed.entries = entries
    feed.bozo    = False
    return feed


# ── disabled source filtering ──────────────────────────────────────────────────

def test_fetch_rss_skips_disabled_source():
    from fairing.rss import fetch_rss
    disabled_src = _source(name="Off", enabled=False)
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([_fake_entry()])
        articles = fetch_rss([disabled_src])
    assert articles == []
    mock_fetch.assert_not_called()


def test_fetch_rss_processes_enabled_source():
    from fairing.rss import fetch_rss
    enabled_src = _source(name="On", enabled=True)
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([_fake_entry(title="Live Article")])
        articles = fetch_rss([enabled_src])
    assert len(articles) == 1
    assert articles[0]["title"] == "Live Article"


def test_fetch_rss_mixed_enabled_disabled():
    from fairing.rss import fetch_rss
    src_on  = _source(name="On",  url="https://on.com/rss",  enabled=True)
    src_off = _source(name="Off", url="https://off.com/rss", enabled=False)
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([_fake_entry()])
        articles = fetch_rss([src_on, src_off])
    assert mock_fetch.call_count == 1
    assert mock_fetch.call_args[0][0] == "https://on.com/rss"


# ── lookback window ────────────────────────────────────────────────────────────

def test_fetch_rss_excludes_old_articles():
    from fairing.rss import fetch_rss, LOOKBACK_MIN_HOURS
    old_dt = datetime.now(tz=timezone.utc) - timedelta(hours=LOOKBACK_MIN_HOURS + 10)
    entry  = _fake_entry(pub_dt=old_dt)
    src    = _source()
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([entry])
        articles = fetch_rss([src])
    assert articles == []


def test_fetch_rss_includes_recent_articles():
    from fairing.rss import fetch_rss, LOOKBACK_MIN_HOURS
    recent = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    entry  = _fake_entry(title="Fresh", pub_dt=recent)
    src    = _source()
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([entry])
        articles = fetch_rss([src])
    assert len(articles) == 1


# ── CJK filtering ─────────────────────────────────────────────────────────────

def test_fetch_rss_excludes_cjk_dominant_articles():
    """Articles where >25% of chars are CJK should be skipped."""
    from fairing.rss import fetch_rss
    cjk_title   = "这是一篇中文文章" * 5       # >25% CJK
    entry = _fake_entry(title=cjk_title, summary=cjk_title)
    src   = _source()
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([entry])
        articles = fetch_rss([src])
    assert articles == []


def test_fetch_rss_allows_mostly_english():
    from fairing.rss import fetch_rss
    entry = _fake_entry(title="English Article with one 字 token", summary="English body.")
    src   = _source()
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([entry])
        articles = fetch_rss([src])
    assert len(articles) == 1


# ── failed fetches ────────────────────────────────────────────────────────────

def test_fetch_rss_skips_failed_source():
    from fairing.rss import fetch_rss
    src = _source()
    with patch("fairing.rss._fetch_with_retry") as mock_fetch, \
         patch("fairing.rss._record_feed_error"):
        mock_fetch.return_value = None   # simulates all retries failed
        articles = fetch_rss([src])
    assert articles == []


def test_fetch_rss_continues_after_failed_source():
    """One failed source should not prevent other sources from being fetched."""
    from fairing.rss import fetch_rss
    src_fail = _source(name="Fail", url="https://fail.com/rss")
    src_ok   = _source(name="OK",   url="https://ok.com/rss")

    def side_effect(url, *args, **kwargs):
        if "fail" in url:
            return None
        return _fake_feed([_fake_entry(title="Survived")])

    with patch("fairing.rss._fetch_with_retry", side_effect=side_effect), \
         patch("fairing.rss._record_feed_error"):
        articles = fetch_rss([src_fail, src_ok])
    assert len(articles) == 1
    assert articles[0]["title"] == "Survived"


def test_prune_feed_errors_removes_disabled_or_removed_sources(tmp_path):
    from fairing import rss

    error_file = tmp_path / "feed_errors.json"
    error_file.write_text(
        """
{
  "Active": {"consecutive_failures": 2, "last_error": "timeout"},
  "Removed": {"consecutive_failures": 10, "last_error": "404"}
}
""".strip(),
        encoding="utf-8",
    )

    with patch("fairing.rss._feed_errors_file", return_value=error_file):
        result = rss.prune_feed_errors({"Active"})

    assert set(result) == {"Active"}
    assert "Removed" not in error_file.read_text(encoding="utf-8")


# ── article structure ─────────────────────────────────────────────────────────

def test_fetch_rss_article_has_required_fields():
    from fairing.rss import fetch_rss
    src = _source(name="MyFeed", category="Engineering")
    with patch("fairing.rss._fetch_with_retry") as mock_fetch:
        mock_fetch.return_value = _fake_feed([_fake_entry(link="https://example.com/post")])
        articles = fetch_rss([src])
    assert len(articles) == 1
    a = articles[0]
    assert "url"       in a
    assert "title"     in a
    assert "source"    in a
    assert "category"  in a
    assert "published" in a
    assert a["source"]   == "MyFeed"
    assert a["category"] == "Engineering"


def test_fetch_feed_uses_explicit_http_headers():
    from fairing.rss import _fetch_feed

    class Response:
        content = b"<rss><channel></channel></rss>"
        headers = {"content-type": "application/rss+xml"}

        def raise_for_status(self):
            return None

    with patch("fairing.rss.requests.get", return_value=Response()) as mock_get, \
         patch("fairing.rss.feedparser.parse") as mock_parse:
        _fetch_feed("https://example.com/feed", timeout=7)

    assert mock_get.call_args.kwargs["timeout"] == 7
    assert "User-Agent" in mock_get.call_args.kwargs["headers"]
    assert mock_parse.call_args.kwargs["response_headers"]["content-type"] == "application/rss+xml"
