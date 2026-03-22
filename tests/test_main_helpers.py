"""Tests for main.py helper functions (non-command utilities)."""
import numpy as np
import sys
import os

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
