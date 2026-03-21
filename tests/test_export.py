"""Tests for fairing/export.py — article_id, payload queue, and title search."""
import json
import pytest
from pathlib import Path


# ── article_id_for ─────────────────────────────────────────────────────────────

def test_article_id_is_16_hex_chars(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import article_id_for
    aid = article_id_for("https://example.com/post")
    assert len(aid) == 16
    assert all(c in "0123456789abcdef" for c in aid)


def test_article_id_strips_tracking_params(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import article_id_for
    clean = article_id_for("https://example.com/post")
    with_tracking = article_id_for("https://example.com/post?utm_source=rss")
    assert clean == with_tracking


def test_article_id_stable_across_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import article_id_for
    url = "https://example.com/stable"
    assert article_id_for(url) == article_id_for(url)


def test_different_urls_different_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import article_id_for
    aid1 = article_id_for("https://example.com/post-one")
    aid2 = article_id_for("https://example.com/post-two")
    assert aid1 != aid2


# ── load_payload_queue ─────────────────────────────────────────────────────────

def test_load_returns_empty_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import load_payload_queue
    assert load_payload_queue() == []


# ── add_to_payload_queue ───────────────────────────────────────────────────────

def test_add_writes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue, load_payload_queue
    article = {"url": "https://example.com/a", "title": "Test", "source": "Src"}
    add_to_payload_queue(article)
    queue = load_payload_queue()
    assert len(queue) == 1


def test_add_sets_article_id(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue, load_payload_queue, article_id_for
    url = "https://example.com/b"
    add_to_payload_queue({"url": url, "title": "B", "source": "S"})
    queue = load_payload_queue()
    assert queue[0]["article_id"] == article_id_for(url)


def test_add_deduplicates_by_article_id(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue, load_payload_queue
    article = {"url": "https://example.com/c", "title": "C", "source": "S"}
    result1 = add_to_payload_queue(article)
    result2 = add_to_payload_queue(article)
    assert result1 is True
    assert result2 is False
    assert len(load_payload_queue()) == 1


def test_add_deduplicates_tracking_params(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue, load_payload_queue
    url_clean    = "https://example.com/d"
    url_tracking = "https://example.com/d?utm_source=newsletter"
    add_to_payload_queue({"url": url_clean,    "title": "D", "source": "S"})
    result = add_to_payload_queue({"url": url_tracking, "title": "D", "source": "S"})
    assert result is False
    assert len(load_payload_queue()) == 1


# ── remove_from_payload_queue ──────────────────────────────────────────────────

def test_remove_from_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue, remove_from_payload_queue, load_payload_queue, article_id_for
    url = "https://example.com/e"
    add_to_payload_queue({"url": url, "title": "E", "source": "S"})
    aid = article_id_for(url)
    result = remove_from_payload_queue(aid)
    assert result is True
    assert load_payload_queue() == []


def test_remove_nonexistent_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import remove_from_payload_queue
    assert remove_from_payload_queue("0000000000000000") is False


# ── search_by_title ────────────────────────────────────────────────────────────

def _seed_title_index(tmp_path: Path, entries: list[dict]) -> None:
    idx_file = tmp_path / "title_index.jsonl"
    with idx_file.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_search_finds_by_substring(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import search_by_title
    _seed_title_index(tmp_path, [
        {"article_id": "aaaa000000000001", "url": "https://a.com/1",
         "title": "ClickHouse Query Optimizer", "source": "CH", "date": "2026-03-20"},
    ])
    results = search_by_title("clickhouse")
    assert len(results) == 1


def test_search_all_words_must_match(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import search_by_title
    _seed_title_index(tmp_path, [
        {"article_id": "aaaa000000000001", "url": "https://a.com/1",
         "title": "ClickHouse Query Optimizer", "source": "CH", "date": "2026-03-20"},
        {"article_id": "aaaa000000000002", "url": "https://a.com/2",
         "title": "ClickHouse Storage Engine", "source": "CH", "date": "2026-03-20"},
    ])
    results = search_by_title("clickhouse optimizer")
    assert len(results) == 1
    assert "Optimizer" in results[0]["title"]


def test_search_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import search_by_title
    _seed_title_index(tmp_path, [
        {"article_id": "aaaa000000000001", "url": "https://a.com/1",
         "title": "Apache Calcite Planner", "source": "Blog", "date": "2026-03-20"},
    ])
    results = search_by_title("APACHE calcite")
    assert len(results) == 1


def test_search_sorted_newest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import search_by_title
    _seed_title_index(tmp_path, [
        {"article_id": "aaaa000000000001", "url": "https://a.com/1",
         "title": "Database Article Old", "source": "S", "date": "2026-03-18"},
        {"article_id": "aaaa000000000002", "url": "https://a.com/2",
         "title": "Database Article New", "source": "S", "date": "2026-03-21"},
        {"article_id": "aaaa000000000003", "url": "https://a.com/3",
         "title": "Database Article Mid", "source": "S", "date": "2026-03-20"},
    ])
    results = search_by_title("database article")
    assert results[0]["date"] == "2026-03-21"
    assert results[-1]["date"] == "2026-03-18"


# ── find_by_id ─────────────────────────────────────────────────────────────────

def test_find_by_id_returns_article(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import find_by_id
    _seed_title_index(tmp_path, [
        {"article_id": "deadbeef00000001", "url": "https://a.com/1",
         "title": "Target Article", "source": "S", "date": "2026-03-20"},
    ])
    result = find_by_id("deadbeef00000001")
    assert result is not None
    assert result["title"] == "Target Article"


def test_find_by_id_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import find_by_id
    _seed_title_index(tmp_path, [])
    assert find_by_id("0000000000000000") is None


# ── payload_queue_file path ────────────────────────────────────────────────────

def test_write_creates_json_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.export import add_to_payload_queue
    from fairing.paths import payload_queue_file
    add_to_payload_queue({"url": "https://example.com/f", "title": "F", "source": "S"})
    assert payload_queue_file().exists()
    data = json.loads(payload_queue_file().read_text(encoding="utf-8"))
    assert isinstance(data, list)


def test_write_returns_correct_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.paths import payload_queue_file
    p = payload_queue_file()
    assert p.name == "payload_queue.json"


# ── _dispatch_to_payload ────────────────────────────────────────────────────────

def _make_article(url: str = "https://example.com/art") -> dict:
    return {"url": url, "title": "Test Article", "source": "TestSrc"}


def test_dispatch_returns_false_when_ask_label_false(monkeypatch, tmp_path):
    """ask_label=False should add to queue and return False without prompting."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import main as m
    monkeypatch.setattr(m, "console", type("C", (), {"print": lambda self, *a, **k: None})())
    result = m._dispatch_to_payload(_make_article(), ask_label=False)
    assert result is False


def test_dispatch_returns_false_when_already_positive(monkeypatch, tmp_path):
    """Article already labeled positive: no prompt, return False."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import main as m
    monkeypatch.setattr(m, "console", type("C", (), {"print": lambda self, *a, **k: None})())
    url = "https://example.com/already-pos"
    # Pre-write a positive label
    from fairing.trainer import save_feedback
    save_feedback({"url": url, "title": "T", "source": "S", "label": 1,
                   "label_index": 0, "date": "2026-03-21"})
    prompted = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompted.append(prompt) or "")
    result = m._dispatch_to_payload({"url": url, "title": "T", "source": "S"}, ask_label=True)
    assert result is False
    assert not prompted, "Should not prompt when article is already labeled positive"


def test_dispatch_returns_true_when_user_confirms(monkeypatch, tmp_path):
    """User answers 'y' to the label prompt: saves feedback, returns True."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import main as m
    monkeypatch.setattr(m, "console", type("C", (), {"print": lambda self, *a, **k: None})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    result = m._dispatch_to_payload(_make_article(), ask_label=True)
    assert result is True
    from fairing.trainer import load_feedback
    saved = load_feedback()
    assert any(f["url"] == "https://example.com/art" and f["label"] == 1 for f in saved)


def test_dispatch_returns_false_when_user_declines(monkeypatch, tmp_path):
    """User answers 'n' to the label prompt: no feedback saved, returns False."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import main as m
    monkeypatch.setattr(m, "console", type("C", (), {"print": lambda self, *a, **k: None})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    result = m._dispatch_to_payload(_make_article(), ask_label=True)
    assert result is False
    from fairing.trainer import load_feedback
    assert load_feedback() == []
