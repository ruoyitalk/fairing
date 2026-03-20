"""Article relevance scorer.

Only one path: personal model (LogisticRegressionCV on scaled embeddings).
If model is not deployed, articles are returned unscored (full display).
The scaler must accompany the model — both are loaded together.
"""
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

from .paths import model_file as _model_file, scaler_file as _scaler_file


def is_model_ready() -> bool:
    return _model_file().exists() and _scaler_file().exists()


def score_articles(articles: list[dict]) -> list[dict]:
    """Score and sort articles by relevance.

    Returns articles in original order without scores if no model is deployed.
    Otherwise scales embeddings, predicts probabilities, adds 'score' field,
    and sorts descending.

    @param articles: enriched article dicts (must have 'embedding' field)
    @return: articles with optional 'score' field, sorted if model is ready
    """
    if not is_model_ready():
        logger.debug("No model deployed — returning articles unscored")
        return articles

    from fairing.trainer import load_model_and_scaler
    model, scaler = load_model_and_scaler()
    if model is None:
        return articles

    embeddings = [a.get("embedding") for a in articles]
    has_emb    = [e is not None for e in embeddings]

    if not any(has_emb):
        return articles

    # Scale and score articles that have embeddings
    X_raw = np.array([e for e in embeddings if e is not None])
    X     = scaler.transform(X_raw)
    probs = model.predict_proba(X)[:, 1]  # P(positive class)

    prob_iter = iter(probs)
    for a, has in zip(articles, has_emb):
        a["score"] = float(next(prob_iter)) if has else 0.5

    articles.sort(key=lambda a: a["score"], reverse=True)
    logger.info("Scored %d articles (model deployed)", len(articles))
    return articles
