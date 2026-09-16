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


def test_load_store_materializes_only_requested_urls(tmp_path, monkeypatch):
    from fairing import embedder

    store_path = tmp_path / "scoring_store.jsonl"
    store_path.write_text(
        "\n".join(
            json.dumps({"url": f"https://example.com/{index}", "embedding": [index]})
            for index in range(100)
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(embedder, "_scoring_store_file", lambda: store_path)

    store = embedder._load_store({"https://example.com/7", "https://example.com/91"})

    assert list(store) == ["https://example.com/7", "https://example.com/91"]


def test_enrich_embeds_and_persists_in_bounded_batches(tmp_path, monkeypatch):
    # Import export before temporarily replacing paths.title_index_file.  The
    # export module binds path helpers at import time, so importing it while the
    # helper is patched would leak the test path into later test modules.
    from fairing import embedder
    from fairing import export as _export  # noqa: F401

    store_path = tmp_path / "scoring_store.jsonl"
    title_path = tmp_path / "title_index.jsonl"
    monkeypatch.setattr(embedder, "_scoring_store_file", lambda: store_path)
    monkeypatch.setenv("FAIRING_EMBED_BATCH_SIZE", "3")

    from fairing import paths

    monkeypatch.setattr(paths, "title_index_file", lambda: title_path)
    calls: list[int] = []

    class Vector(list):
        def tolist(self):
            return list(self)

    class Model:
        def encode(self, texts, **kwargs):
            calls.append(len(texts))
            assert kwargs["batch_size"] == 3
            assert kwargs["convert_to_numpy"] is True
            return [Vector([float(len(text))] * 4) for text in texts]

    monkeypatch.setattr(embedder, "_get_model", lambda: Model())
    articles = [
        {"url": f"https://example.com/{index}", "title": f"Article {index}"}
        for index in range(8)
    ]

    result = embedder.enrich(articles)

    assert result is articles
    assert calls == [3, 3, 2]
    assert all(len(article["embedding"]) == 4 for article in articles)
    assert len(store_path.read_text(encoding="utf-8").splitlines()) == 8
    assert len(title_path.read_text(encoding="utf-8").splitlines()) == 8
