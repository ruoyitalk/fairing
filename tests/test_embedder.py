"""Tests for fairing/embedder.py fan-out behavior."""
import json
from types import SimpleNamespace


def test_fan_out_deduplicates_normalized_urls(tmp_path):
    from fairing.embedder import fan_out

    out = tmp_path / "feeds" / "gp" / "store.jsonl"
    out.parent.mkdir(parents=True)
    out.write_text(
        json.dumps({"url": "https://example.com/post"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    articles = [{
        "url": "https://example.com/post?utm_source=rss#comments",
        "title": "Duplicate",
        "source": "Src",
        "tags": ["ai"],
        "text_for_scoring": "duplicate",
    }]
    subscriptions = [SimpleNamespace(name="gp", tags=["ai"], output=str(out))]

    stats = fan_out(articles, subscriptions)

    assert stats["gp"] == {"matched": 1, "written": 0}
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1


def test_fan_out_writes_fetch_diagnostics(tmp_path):
    from fairing.embedder import fan_out

    out = tmp_path / "feeds" / "gp" / "store.jsonl"
    articles = [{
        "url": "https://example.com/new",
        "title": "New",
        "source": "Src",
        "category": "Research",
        "tags": ["ai"],
        "text_for_scoring": "body",
        "fetch_engine": "scrapling",
        "fetch_blocked": True,
        "fetch_error_type": "blocked_by_waf",
        "fetch_block_reason": "akamai_edgesuite",
        "upstream_status": 403,
    }]
    subscriptions = [SimpleNamespace(name="gp", tags=["ai"], output=str(out))]

    stats = fan_out(articles, subscriptions)
    entry = json.loads(out.read_text(encoding="utf-8").strip())

    assert stats["gp"] == {"matched": 1, "written": 1}
    assert entry["category"] == "Research"
    assert entry["fetch_engine"] == "scrapling"
    assert entry["fetch_blocked"] is True
    assert entry["fetch_error_type"] == "blocked_by_waf"
    assert entry["fetch_block_reason"] == "akamai_edgesuite"
    assert entry["upstream_status"] == 403


def test_fan_out_can_backfill_tags_from_source_map(tmp_path):
    from fairing.embedder import fan_out

    out = tmp_path / "feeds" / "gp" / "store.jsonl"
    articles = [{
        "url": "https://example.com/historical",
        "title": "Historical",
        "source": "Old Source",
        "text_for_scoring": "body",
    }]
    subscriptions = [SimpleNamespace(name="gp", tags=["database"], output=str(out))]

    stats = fan_out(
        articles,
        subscriptions,
        source_tags_by_name={"Old Source": ["database", "systems"]},
    )
    entry = json.loads(out.read_text(encoding="utf-8").strip())

    assert stats["gp"] == {"matched": 1, "written": 1}
    assert entry["tags"] == ["database", "systems"]


def test_load_store_skips_malformed_rows(tmp_path, monkeypatch):
    from fairing import embedder

    store_path = tmp_path / "scoring_store.jsonl"
    good = {
        "url": "https://example.com/good",
        "title": "Good",
        "text_for_scoring": "body",
        "embedding": [0.1, 0.2],
    }
    store_path.write_text(
        json.dumps(good, ensure_ascii=False) + "\n"
        + '{"url": "https://example.com/partial", "title": "Partial"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(embedder, "_scoring_store_file", lambda: store_path)

    store = embedder.load_store()

    assert list(store) == ["https://example.com/good"]
    assert store["https://example.com/good"]["title"] == "Good"
