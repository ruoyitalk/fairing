"""Train personal relevance classifier from feedback data.

Scientific design:
  - Embeddings: sentence-transformers all-MiniLM-L6-v2 (384 dims)
    Pre-trained semantic vectors are a strong prior; logistic regression
    on top is well-studied and interpretable.

  - Classifier: LogisticRegressionCV with StandardScaler
    Why LR: low parameter count relative to features, strong regularization,
    well-calibrated probabilities, fast training on small datasets.
    Why CV: automatically selects regularization strength C from a grid,
    avoiding the underdetermination problem (384 features, ~30 samples).
    Why scale: logistic regression with L2 penalty is sensitive to feature
    scale; StandardScaler normalizes embedding dimensions.

  - Class balance: class_weight='balanced'
    Labels will be skewed (+>> -) for most users. Balanced weighting
    ensures the minority class (usually negative) is not ignored.

  - Decay: article-count-based, not time-based
    Unit: every DECAY_UNIT newly-labeled articles = one forgetting generation.
    Rationale: preference drift tracks reading pace, not calendar time.
    A user who labels 3 articles/day drifts faster than one who labels 3/week.
    Old labels are never deleted; weight approaches zero asymptotically.

  - Evaluation: StratifiedKFold + balanced_accuracy
    Balanced accuracy = mean(recall_per_class), robust to class imbalance.
    Stratified folds preserve class ratio in each split.

  - Deployment threshold: balanced_accuracy >= 0.75
    Chosen conservatively; with <50 samples CV variance is high.
    Report 95% CI to give calibrated confidence.

Storage:
  data/feedback.jsonl     — committed to git (training labels, no secrets)
  .personal_model.pkl     — gitignored (regenerable)
  .personal_scaler.pkl    — gitignored (regenerable)
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

from .paths import (feedback_file as _feedback_file, model_file as _model_file,
                    scaler_file as _scaler_file, training_log_file as _log_file)

def _ff() -> Path: return _feedback_file()
def _mf() -> Path: return _model_file()
def _sf() -> Path: return _scaler_file()

DECAY_BASE         = 0.5
DECAY_UNIT         = 3
ACCURACY_THRESHOLD = 0.75
MIN_POS            = 5
MIN_NEG            = 5
MIN_TOTAL          = 80   # absolute minimum before attempting any training


@dataclass
class TrainResult:
    cv_accuracy: float
    cv_std:      float
    n_samples:   int
    n_pos:       int
    n_neg:       int
    n_folds:     int
    deployed:    bool
    C_selected:  float


# ── feedback I/O ──────────────────────────────────────────────────────────────

def load_feedback() -> list[dict]:
    """Load feedback, deduplicating by URL and keeping the last entry per URL.

    Append-override semantics: when a label is edited, a new entry is appended
    with the same URL. This function returns only the latest entry per URL so
    the trainer always sees the current label.
    """
    _ff().parent.mkdir(parents=True, exist_ok=True)
    if not _ff().exists():
        return []
    raw = [json.loads(l) for l in _ff().read_text(encoding="utf-8").splitlines() if l.strip()]
    deduped: dict[str, dict] = {}
    for entry in raw:
        deduped[entry["url"]] = entry
    return list(deduped.values())


def save_feedback(entry: dict) -> None:
    _ff().parent.mkdir(parents=True, exist_ok=True)
    with _ff().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── decay ─────────────────────────────────────────────────────────────────────

def _label_weight(label_index: int, total: int) -> float:
    """Article-count-based decay weight.

    labels_since = how many labels were added AFTER this one.
    generations  = floor(labels_since / DECAY_UNIT)
    weight       = DECAY_BASE ** generations
    """
    labels_since = total - 1 - label_index
    generations  = max(0, labels_since) // DECAY_UNIT
    return DECAY_BASE ** generations


# ── training ─────────────────────────────────────────────────────────────────

def maybe_auto_train(store: dict) -> Optional[TrainResult]:
    """Attempt training after a feedback session.

    Returns TrainResult (deployed or not) if training was attempted,
    None if there is insufficient data.

    @param store: {url: entry} from embedder.load_store()
    """
    feedback = load_feedback()
    valid    = [(f, store[f["url"]]) for f in feedback if f["url"] in store]

    pos = [v for v in valid if v[0]["label"] ==  1]
    neg = [v for v in valid if v[0]["label"] == -1]

    if len(valid) < MIN_TOTAL or len(pos) < MIN_POS or len(neg) < MIN_NEG:
        logger.info(
            "Feedback: +%d / -%d / total %d  (need +%d / -%d / total %d)",
            len(pos), len(neg), len(valid), MIN_POS, MIN_NEG, MIN_TOTAL,
        )
        return None

    return _train(valid)


def _train(valid: list[tuple]) -> TrainResult:
    import joblib
    from sklearn.linear_model import LogisticRegressionCV
    from sklearn.model_selection import StratifiedKFold, cross_validate
    from sklearn.preprocessing import StandardScaler

    n     = len(valid)
    X_raw = np.array([v[1]["embedding"] for v in valid])
    y     = np.array([v[0]["label"]     for v in valid])
    w     = np.array([_label_weight(i, n) for i in range(n)])

    n_pos = int((y ==  1).sum())
    n_neg = int((y == -1).sum())

    # Scale features: critical for L2-regularized logistic regression
    scaler  = StandardScaler()
    X       = scaler.fit_transform(X_raw)

    # Number of CV folds: limited by minority class size
    n_folds = min(5, min(n_pos, n_neg))

    # Auto-select regularization C via nested CV
    Cs    = [0.001, 0.01, 0.1, 1.0, 10.0]
    model = LogisticRegressionCV(
        Cs=Cs,
        cv=StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42),
        class_weight="balanced",
        max_iter=2000,
        scoring="balanced_accuracy",
        refit=True,
    )
    model.fit(X, y, sample_weight=w)
    C_selected = float(model.C_[0])

    # Evaluate: manual stratified CV to correctly pass sample_weight per fold
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.linear_model import LogisticRegression as _LR

    skf       = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    cv_scores_list = []
    for tr_idx, te_idx in skf.split(X, y):
        fold_model = _LR(C=C_selected, class_weight="balanced", max_iter=2000)
        fold_model.fit(X[tr_idx], y[tr_idx], sample_weight=w[tr_idx])
        y_pred = fold_model.predict(X[te_idx])
        cv_scores_list.append(balanced_accuracy_score(y[te_idx], y_pred))
    cv_scores = np.array(cv_scores_list)
    cv_mean   = float(cv_scores.mean())
    cv_std    = float(cv_scores.std())

    deployed = cv_mean >= ACCURACY_THRESHOLD

    if deployed:
        joblib.dump(model,  _mf())
        joblib.dump(scaler, _sf())
        logger.info(
            "Model deployed — balanced_acc: %.2f ± %.2f  C=%.3f  n=%d",
            cv_mean, cv_std, C_selected, n,
        )
    else:
        logger.info(
            "Model not deployed — balanced_acc: %.2f ± %.2f < threshold %.2f",
            cv_mean, cv_std, ACCURACY_THRESHOLD,
        )

    result = TrainResult(
        cv_accuracy=cv_mean, cv_std=cv_std,
        n_samples=n, n_pos=n_pos, n_neg=n_neg,
        n_folds=n_folds, deployed=deployed,
        C_selected=C_selected,
    )

    # Append one record to the training log for history tracking.
    from fairing.state import today_beijing
    log_entry = {
        "date":        today_beijing(),
        "n_samples":   n,
        "n_pos":       n_pos,
        "n_neg":       n_neg,
        "cv_accuracy": round(cv_mean, 4),
        "cv_std":      round(cv_std, 4),
        "C":           C_selected,
        "deployed":    deployed,
    }
    with _log_file().open("a", encoding="utf-8") as _lf:
        _lf.write(json.dumps(log_entry) + "\n")

    return result


# ── inference ─────────────────────────────────────────────────────────────────

def load_model_and_scaler():
    """Load deployed model + scaler pair. Returns (None, None) if not deployed."""
    if not _mf().exists() or not _sf().exists():
        return None, None
    import joblib
    return joblib.load(_mf()), joblib.load(_sf())


# ── status ────────────────────────────────────────────────────────────────────

def model_status() -> dict:
    """Return all model parameters and feedback statistics."""
    feedback = load_feedback()
    pos = [f for f in feedback if f["label"] ==  1]
    neg = [f for f in feedback if f["label"] == -1]

    status = {
        "deployed":   _mf().exists() and _sf().exists(),
        "n_labels":   len(feedback),
        "n_pos":      len(pos),
        "n_neg":      len(neg),
        "decay_base": DECAY_BASE,
        "decay_unit": DECAY_UNIT,
        "threshold":  ACCURACY_THRESHOLD,
        "min_total":  MIN_TOTAL,
        "min_pos":    MIN_POS,
        "min_neg":    MIN_NEG,
    }

    if status["deployed"]:
        import joblib
        from datetime import datetime
        model   = joblib.load(_mf())
        mtime   = _mf().stat().st_mtime
        status.update({
            "train_date":  datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            "model_type":  type(model).__name__,
            "model_C":     float(model.C_[0]) if hasattr(model, "C_") else getattr(model, "C", "?"),
            "n_features":  model.n_features_in_,
        })

    return status


# ── TF-IDF signals ────────────────────────────────────────────────────────────

def tfidf_top_terms(pos_texts: list[str], neg_texts: list[str],
                    n: int = 15) -> list[str]:
    """Extract most discriminative terms from positive vs negative samples."""
    if not pos_texts or not neg_texts:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        all_texts = pos_texts + neg_texts
        labels    = [1] * len(pos_texts) + [-1] * len(neg_texts)

        vec   = TfidfVectorizer(max_features=2000, stop_words="english", ngram_range=(1, 2))
        X     = vec.fit_transform(all_texts).toarray()
        names = vec.get_feature_names_out()

        pos_mean = X[[i for i, l in enumerate(labels) if l ==  1]].mean(axis=0)
        neg_mean = X[[i for i, l in enumerate(labels) if l == -1]].mean(axis=0)
        diff     = pos_mean - neg_mean

        top_idx = np.argsort(diff)[::-1][:n]
        return [names[i] for i in top_idx]
    except Exception as e:
        logger.warning("TF-IDF extraction failed: %s", e)
        return []
