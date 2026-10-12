"""Tests for fairing/trainer.py — evaluation gate and feedback I/O."""
import json
import pytest
import numpy as np


# ── feedback I/O ──────────────────────────────────────────────────────────────

@pytest.fixture()
def patch_feedback(tmp_path, monkeypatch):
    """Redirect all data files to a temp directory via DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def test_save_and_load_feedback(patch_feedback):
    from fairing.trainer import save_feedback, load_feedback
    entry = {"url": "https://a.com", "label": 1, "label_index": 0, "date": "2026-03-20"}
    save_feedback(entry)
    loaded = load_feedback()
    assert len(loaded) == 1
    assert loaded[0]["label"] == 1


def test_load_empty_feedback(patch_feedback):
    from fairing.trainer import load_feedback
    assert load_feedback() == []


def test_load_feedback_deduplicates_by_url_keeps_latest(patch_feedback):
    """When the same URL is labeled twice, load_feedback returns only the latest entry."""
    from fairing.trainer import save_feedback, load_feedback
    url = "https://example.com/article"
    save_feedback({"url": url, "label":  1, "label_index": 0, "date": "2026-03-19"})
    save_feedback({"url": url, "label": -1, "label_index": 1, "date": "2026-03-20"})
    loaded = load_feedback()
    assert len(loaded) == 1
    assert loaded[0]["label"] == -1   # latest wins


def test_load_feedback_dedup_preserves_distinct_urls(patch_feedback):
    """Entries with different URLs are all retained."""
    from fairing.trainer import save_feedback, load_feedback
    save_feedback({"url": "https://a.com", "label":  1, "label_index": 0, "date": "2026-03-20"})
    save_feedback({"url": "https://b.com", "label": -1, "label_index": 1, "date": "2026-03-20"})
    loaded = load_feedback()
    assert len(loaded) == 2


# ── training ──────────────────────────────────────────────────────────────────

def _synthetic_store(n_pos: int, n_neg: int) -> tuple[list, dict]:
    """Generate synthetic embeddings + feedback for testing."""
    rng     = np.random.default_rng(42)
    store   = {}
    feedback = []
    for i in range(n_pos):
        url = f"https://pos.com/{i}"
        # Positive class: embeddings shifted +0.5
        emb = (rng.standard_normal(384) + 0.5).tolist()
        store[url]    = {"url": url, "embedding": emb, "text_for_scoring": "distributed query optimizer"}
        feedback.append({"url": url, "label": 1, "label_index": i, "date": "2026-03-20"})
    for i in range(n_neg):
        url = f"https://neg.com/{i}"
        emb = (rng.standard_normal(384) - 0.5).tolist()
        store[url]    = {"url": url, "embedding": emb, "text_for_scoring": "beginner tutorial introduction"}
        feedback.append({"url": url, "label": -1, "label_index": n_pos + i, "date": "2026-03-20"})
    return feedback, store


def test_maybe_auto_train_insufficient_data(patch_feedback):
    from fairing.trainer import maybe_auto_train, save_feedback
    _, store = _synthetic_store(3, 3)   # below MIN_POS / MIN_NEG / MIN_TOTAL
    feedback, _ = _synthetic_store(3, 3)
    for f in feedback:
        save_feedback(f)
    result = maybe_auto_train(store)
    assert result is None


def test_assistant_labels_cannot_satisfy_human_validation_gate(patch_feedback):
    from fairing.trainer import maybe_auto_train, save_feedback, MIN_TOTAL

    feedback, store = _synthetic_store(MIN_TOTAL // 2, MIN_TOTAL // 2)
    for row in feedback:
        save_feedback({**row, "annotator": "assistant"})

    assert maybe_auto_train(store) is None


def test_training_due_after_new_feedback(patch_feedback):
    from fairing.paths import training_log_file
    from fairing.trainer import save_feedback, training_due

    assert not training_due()
    save_feedback({"url": "https://example.com", "label": 1})
    assert training_due()
    training_log_file().write_text('{}\n', encoding='utf-8')
    assert not training_due()


def test_train_with_sufficient_data(patch_feedback):
    from fairing.trainer import maybe_auto_train, save_feedback, MIN_POS, MIN_NEG, MIN_TOTAL
    from fairing.paths import model_file, scaler_file, training_log_file
    n = max(MIN_POS, MIN_NEG, MIN_TOTAL // 2) + 2
    feedback, store = _synthetic_store(n, n)
    for f in feedback:
        save_feedback(f)
    result = maybe_auto_train(store)
    # Should attempt training and return a result (deployed or not)
    assert result is not None
    assert 0.0 <= result.cv_accuracy <= 1.0
    assert result.n_samples == n * 2
    assert result.n_pos == n
    assert result.n_neg == n
    assert result.n_human == n * 2
    assert result.n_assistant == 0
    assert len(result.cv_seed_scores) == 3
    rows = training_log_file().read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    for field in ("date", "n_samples", "n_pos", "n_neg", "n_human",
                  "n_assistant", "cv_accuracy", "cv_std", "C", "deployed"):
        assert field in row
    assert row["evaluation"] == "human_holdout_nested_cv"
    assert isinstance(row["deployed"], bool)
    assert 0.0 <= row["cv_accuracy"] <= 1.0
    if result.deployed:
        assert model_file().exists() and scaler_file().exists()
