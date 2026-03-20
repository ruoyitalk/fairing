"""Tests for fairing/scorer.py — score_articles with and without model."""
import pytest
import numpy as np


def _article(url: str, embedding=None) -> dict:
    emb = embedding if embedding is not None else np.zeros(384).tolist()
    return {"url": url, "title": "T", "source": "S",
            "category": "C", "excerpt": "", "embedding": emb}


@pytest.fixture()
def no_model(tmp_path, monkeypatch):
    """Ensure no model files exist by routing DATA_DIR to an empty temp dir."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))


@pytest.fixture()
def mock_model(tmp_path, monkeypatch):
    """Inject a dummy trained model and scaler."""
    import fairing.scorer as sc
    import fairing.trainer as t
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(0)
    X   = rng.standard_normal((20, 384))
    y   = np.array([1] * 10 + [-1] * 10)

    scaler = StandardScaler().fit(X)
    model  = LogisticRegression(class_weight="balanced", max_iter=200)
    model.fit(scaler.transform(X), y)

    model_file  = tmp_path / "model.pkl"
    scaler_file = tmp_path / "scaler.pkl"
    joblib.dump(model,  model_file)
    joblib.dump(scaler, scaler_file)

    # Route DATA_DIR to tmp_path so model_file() / scaler_file() resolve here
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    joblib.dump(model,  tmp_path / "personal_model.pkl")
    joblib.dump(scaler, tmp_path / "personal_scaler.pkl")


# ── without model ──────────────────────────────────────────────────────────────

def test_score_articles_returns_unscored_without_model(no_model):
    from fairing.scorer import score_articles
    articles = [_article("https://a.com"), _article("https://b.com")]
    result   = score_articles(articles)
    assert len(result) == 2
    assert "score" not in result[0]


def test_score_articles_preserves_order_without_model(no_model):
    from fairing.scorer import score_articles
    urls     = ["https://a.com", "https://b.com", "https://c.com"]
    articles = [_article(u) for u in urls]
    result   = score_articles(articles)
    assert [r["url"] for r in result] == urls


def test_score_articles_handles_empty_list(no_model):
    from fairing.scorer import score_articles
    assert score_articles([]) == []


# ── with model ────────────────────────────────────────────────────────────────

def test_score_articles_adds_score_with_model(mock_model):
    from fairing.scorer import score_articles
    articles = [_article("https://a.com"), _article("https://b.com")]
    result   = score_articles(articles)
    assert all("score" in a for a in result)
    assert all(0.0 <= a["score"] <= 1.0 for a in result)


def test_score_articles_sorts_descending_with_model(mock_model):
    from fairing.scorer import score_articles
    rng = np.random.default_rng(1)
    articles = [_article(f"https://x.com/{i}", rng.standard_normal(384).tolist())
                for i in range(5)]
    result   = score_articles(articles)
    scores   = [a["score"] for a in result]
    assert scores == sorted(scores, reverse=True)


def test_score_articles_handles_missing_embedding(mock_model):
    """Articles without embedding should get score=0.5 (default fallback)."""
    from fairing.scorer import score_articles
    a_with    = _article("https://a.com")
    a_without = {"url": "https://b.com", "title": "T", "source": "S",
                 "category": "C", "excerpt": ""}   # no embedding key
    result = score_articles([a_with, a_without])
    no_emb = next(r for r in result if r["url"] == "https://b.com")
    assert no_emb["score"] == 0.5
