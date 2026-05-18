"""Tests for main.py helper functions (non-command utilities)."""
import numpy as np
import sys
import os
from types import SimpleNamespace
from unittest.mock import patch

# Ensure project root is importable without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_store(entries: list[tuple[str, list[float]]]) -> dict:
    """Build a minimal scoring_store dict from (url, embedding) pairs."""
    return {url: {"embedding": emb, "text_for_scoring": f"text {url}"}
            for url, emb in entries}


def _make_feedback(entries: list[tuple[str, int]]) -> list[dict]:
    """Build minimal feedback list from (url, label) pairs."""
    return [{"url": url, "label": label, "title": f"Title {url}",
             "source": "S", "date": "2026-03-22"}
            for url, label in entries]


# ── _nearest_labels ────────────────────────────────────────────────────────────

def test_nearest_labels_returns_pos_and_neg():
    from main import _nearest_labels
    # Article at [1,0,0] is closest to pos [0.9,0,0] and far from neg [0,1,0]
    store = _make_store([
        ("url_target", [1.0, 0.0, 0.0]),
        ("url_pos1",   [0.9, 0.1, 0.0]),
        ("url_pos2",   [0.8, 0.2, 0.0]),
        ("url_neg1",   [0.0, 1.0, 0.0]),
    ])
    feedback = _make_feedback([
        ("url_pos1", 1),
        ("url_pos2", 1),
        ("url_neg1", -1),
    ])
    pos, neg = _nearest_labels("url_target", store, feedback, n=2)
    assert len(pos) <= 2
    assert len(neg) <= 1
    assert all(isinstance(t, str) for t in pos + neg)


def test_nearest_labels_url_not_in_store():
    from main import _nearest_labels
    store    = _make_store([("url_a", [1.0, 0.0])])
    feedback = _make_feedback([("url_a", 1)])
    pos, neg = _nearest_labels("url_unknown", store, feedback)
    assert pos == []
    assert neg == []


def test_nearest_labels_no_feedback():
    from main import _nearest_labels
    store    = _make_store([("url_a", [1.0, 0.0])])
    pos, neg = _nearest_labels("url_a", store, [])
    assert pos == []
    assert neg == []


def test_nearest_labels_skips_self():
    from main import _nearest_labels
    # url_a is labeled; asking about url_a should not include itself
    store = _make_store([
        ("url_a", [1.0, 0.0]),
        ("url_b", [0.9, 0.1]),
    ])
    feedback = _make_feedback([("url_a", 1), ("url_b", 1)])
    pos, neg = _nearest_labels("url_a", store, feedback, n=5)
    titles = pos + neg
    # url_a's own title should not appear (self is skipped)
    assert all("url_a" not in t for t in titles)


def test_nearest_labels_respects_n():
    from main import _nearest_labels
    store = _make_store([(f"url_{i}", [float(i), 0.0]) for i in range(10)])
    feedback = _make_feedback([(f"url_{i}", 1 if i % 2 == 0 else -1) for i in range(1, 10)])
    pos, neg = _nearest_labels("url_0", store, feedback, n=2)
    assert len(pos) <= 2
    assert len(neg) <= 2


# ── _enrich_full_text_for_scoring ─────────────────────────────────────────────

def test_enrich_full_text_for_scoring_uses_source_gate():
    from main import _enrich_full_text_for_scoring

    cfg = SimpleNamespace(rss_sources=[
        SimpleNamespace(name="Full", firecrawl_fulltext=True),
        SimpleNamespace(name="MetaOnly", firecrawl_fulltext=False),
    ])
    articles = [
        {"source": "Full", "url": "https://example.com/full"},
        {"source": "MetaOnly", "url": "https://example.com/meta"},
    ]

    with patch("fairing.reader.fetch_full_result", return_value={
        "content": "full body",
        "engine": "scrapling",
        "blocked": False,
        "error_type": None,
        "block_reason": None,
        "upstream_status": 200,
    }) as mock_fetch:
        _enrich_full_text_for_scoring(articles, cfg)

    mock_fetch.assert_called_once_with("https://example.com/full")
    assert articles[0]["full_text"] == "full body"
    assert articles[0]["fetch_engine"] == "scrapling"
    assert "full_text" not in articles[1]


def test_enrich_full_text_for_scoring_preserves_blocked_diagnostics():
    from main import _enrich_full_text_for_scoring

    cfg = SimpleNamespace(rss_sources=[
        SimpleNamespace(name="Blocked", firecrawl_fulltext=True),
    ])
    articles = [{"source": "Blocked", "url": "https://example.com/blocked"}]

    with patch("fairing.reader.fetch_full_result", return_value={
        "content": "Access Denied",
        "engine": "scrapling",
        "blocked": True,
        "error_type": "blocked_by_waf",
        "block_reason": "akamai_edgesuite",
        "upstream_status": 403,
    }):
        _enrich_full_text_for_scoring(articles, cfg)

    assert articles[0]["fetch_blocked"] is True
    assert articles[0]["fetch_error_type"] == "blocked_by_waf"
    assert articles[0]["fetch_block_reason"] == "akamai_edgesuite"
    assert articles[0]["upstream_status"] == 403
    assert "full_text" not in articles[0]
