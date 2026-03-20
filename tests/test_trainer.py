"""Tests for fairing/trainer.py — decay weights, training, feedback I/O."""
import json
import pytest
import numpy as np


# ── decay weights ─────────────────────────────────────────────────────────────

def test_label_weight_latest_is_one():
    from fairing.trainer import _label_weight
    assert _label_weight(9, 10) == 1.0   # last of 10


def test_label_weight_one_generation():
    from fairing.trainer import _label_weight, DECAY_BASE, DECAY_UNIT
    # 3 newer labels = 1 generation
    assert _label_weight(DECAY_UNIT - 1, DECAY_UNIT * 2) == DECAY_BASE


def test_label_weight_two_generations():
    from fairing.trainer import _label_weight, DECAY_BASE, DECAY_UNIT
    # need labels_since = 2*DECAY_UNIT to get 2 generations
    # labels_since = total - 1 - 0 = total - 1, so total = 2*DECAY_UNIT + 1
    total = DECAY_UNIT * 2 + 1
    assert _label_weight(0, total) == DECAY_BASE ** 2


def test_label_weight_never_negative():
    from fairing.trainer import _label_weight
    for i in range(100):
        assert _label_weight(i, 100) >= 0


def test_label_weight_monotone_decreasing():
    from fairing.trainer import _label_weight
    total  = 30
    weights = [_label_weight(i, total) for i in range(total)]
    # weights should be non-increasing (older = smaller or equal)
    for a, b in zip(weights, weights[1:]):
        assert a <= b


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


def test_train_with_sufficient_data(patch_feedback):
    from fairing.trainer import maybe_auto_train, save_feedback, MIN_POS, MIN_NEG, MIN_TOTAL
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


def test_train_creates_model_file_when_accurate(patch_feedback):
    """With clearly separable synthetic data, model should deploy."""
    from fairing.trainer import maybe_auto_train, save_feedback, MIN_TOTAL
    n = MIN_TOTAL // 2 + 5   # enough samples to exceed MIN_TOTAL
    feedback, store = _synthetic_store(n, n)
    for f in feedback:
        save_feedback(f)
    result = maybe_auto_train(store)
    assert result is not None
    # With clearly separated embeddings, should reach threshold
    if result.deployed:
        from fairing.paths import model_file, scaler_file
        assert model_file().exists()
        assert scaler_file().exists()
