"""Train personal relevance classifier from feedback data.

Scientific design:
  - Embeddings: sentence-transformers all-MiniLM-L6-v2 (384 dims)
    Pre-trained semantic vectors are a strong prior; logistic regression
    on top is well-studied and interpretable.

  - Classifier: LogisticRegression with StandardScaler
    Why LR: low parameter count relative to features, strong regularization,
    well-calibrated probabilities, fast training on small datasets.
    Why CV: automatically selects regularization strength C from a grid,
    avoiding the underdetermination problem (384 features, ~30 samples).
    Why scale: logistic regression with L2 penalty is sensitive to feature
    scale; StandardScaler normalizes embedding dimensions.

  - Class balance: class_weight='balanced'
    Labels will be skewed (+>> -) for most users. Balanced weighting
    ensures the minority class (usually negative) is not ignored.

  - All human labels retain their weight while the sample is small.
    The old half-life of three labels made most of the evidence effectively
    disappear after a single feedback session.

  - Evaluation: repeated nested cross-validation on human labels
    Assistant-inferred labels may help fit a model, but never count as
    independent evidence that it works. Scaling and C selection stay inside
    each training fold.

  - Deployment threshold: balanced_accuracy >= 0.75
    Chosen conservatively; with <50 samples CV variance is high.
    Report 95% CI to give calibrated confidence.

Storage:
  data/feedback.jsonl     — committed to git (training labels, no secrets)
  .personal_model.pkl     — gitignored (regenerable)
  .personal_scaler.pkl    — gitignored (regenerable)
"""
import fcntl
import hashlib
import json
import logging
import os
from contextlib import contextmanager
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

ACCURACY_THRESHOLD = 0.75
MIN_POS            = 5
MIN_NEG            = 5
MIN_TOTAL          = 80   # absolute minimum before attempting any training
MIN_HUMAN_TOTAL    = 30
MIN_HUMAN_POS      = 10
MIN_HUMAN_NEG      = 10
EVALUATION_VERSION = 2
CV_SEEDS           = (42, 43, 44)


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
    n_human:     int = 0
    n_assistant: int = 0
    cv_seed_scores: tuple[float, ...] = ()


# ── feedback I/O ──────────────────────────────────────────────────────────────

@contextmanager
def _feedback_lock(*, exclusive: bool):
    feedback = _ff()
    lock_file = feedback.with_name(feedback.name + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_feedback() -> list[dict]:
    """Load feedback, deduplicating by URL and keeping the last entry per URL.

    Append-override semantics: when a label is edited, a new entry is appended
    with the same URL. This function returns only the latest entry per URL so
    the trainer always sees the current label.
    """
    with _feedback_lock(exclusive=False):
        if not _ff().exists():
            return []
        raw = [json.loads(l) for l in _ff().read_text(encoding="utf-8").splitlines() if l.strip()]
    deduped: dict[str, dict] = {}
    for entry in raw:
        deduped[entry["url"]] = entry
    return list(deduped.values())


def save_feedback(entry: dict) -> None:
    with _feedback_lock(exclusive=True):
        with _ff().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


# ── training ─────────────────────────────────────────────────────────────────

def training_due() -> bool:
    """A new feedback write needs one evaluation at the next scheduled run."""
    # Reading status must not create /app/data in a read-only image.
    from .paths import data_dir
    root = data_dir()
    feedback = root / "data" / "feedback.jsonl"
    log = root / "training_log.jsonl"
    return feedback.exists() and (not log.exists()
                                  or feedback.stat().st_mtime_ns > log.stat().st_mtime_ns)

def maybe_auto_train(store: dict | None = None) -> Optional[TrainResult]:
    """Attempt training after a feedback session.

    Returns TrainResult (deployed or not) if training was attempted,
    None if there is insufficient data.

    @param store: optional {url: entry}; omitted to stream only labeled URLs
    """
    feedback = load_feedback()
    if (len(feedback) < MIN_TOTAL
            or sum(f["label"] == 1 for f in feedback) < MIN_POS
            or sum(f["label"] == -1 for f in feedback) < MIN_NEG):
        return None
    if store is None:
        from .embedder import _load_store
        store = _load_store({f["url"] for f in feedback})
    valid = []
    for item in feedback:
        entry = store.get(item["url"])
        embedding = entry.get("embedding") if isinstance(entry, dict) else None
        if isinstance(embedding, list) and len(embedding) == 384:
            try:
                if np.isfinite(np.asarray(embedding, dtype=float)).all():
                    valid.append((item, entry))
            except (TypeError, ValueError):
                continue

    pos = [v for v in valid if v[0]["label"] == 1]
    neg = [v for v in valid if v[0]["label"] == -1]
    human = [v for v in valid if v[0].get("annotator") != "assistant"]
    human_pos = sum(v[0]["label"] == 1 for v in human)
    human_neg = sum(v[0]["label"] == -1 for v in human)

    if (len(valid) < MIN_TOTAL or len(pos) < MIN_POS or len(neg) < MIN_NEG
            or len(human) < MIN_HUMAN_TOTAL
            or human_pos < MIN_HUMAN_POS or human_neg < MIN_HUMAN_NEG):
        logger.info(
            "Training labels: valid=%d (+%d/-%d), human=%d (+%d/-%d); insufficient",
            len(valid), len(pos), len(neg), len(human), human_pos, human_neg,
        )
        return None

    signature = hashlib.sha256(json.dumps(
        [(f["url"], f["label"], f.get("annotator", "")) for f, _ in valid],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return _train(valid, signature)


def _train(valid: list[tuple], signature: str) -> TrainResult:
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import GridSearchCV, StratifiedKFold
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    n     = len(valid)
    X_raw = np.array([v[1]["embedding"] for v in valid])
    y     = np.array([v[0]["label"]     for v in valid])
    n_pos = int((y ==  1).sum())
    n_neg = int((y == -1).sum())
    human_idx = np.array([i for i, (f, _) in enumerate(valid)
                          if f.get("annotator") != "assistant"], dtype=int)
    assistant_idx = np.array([i for i, (f, _) in enumerate(valid)
                              if f.get("annotator") == "assistant"], dtype=int)
    n_folds = min(5, int((y[human_idx] == 1).sum()),
                  int((y[human_idx] == -1).sum()))
    grid = {"clf__C": [0.001, 0.01, 0.1, 1.0, 10.0]}

    def search(seed: int) -> GridSearchCV:
        return GridSearchCV(
            Pipeline([("scale", StandardScaler()),
                      ("clf", LogisticRegression(class_weight="balanced",
                                                  max_iter=2000))]),
            grid, scoring="balanced_accuracy",
            cv=StratifiedKFold(n_splits=3, shuffle=True,
                               random_state=seed + 100),
            n_jobs=1,
        )

    # Every held-out row is a real user label. Proxy labels can only appear
    # in the training fold, and both scaling and C selection are refit there.
    seed_scores: list[float] = []
    selected_cs: list[float] = []
    for seed in CV_SEEDS:
        held_truth: list[int] = []
        held_pred: list[int] = []
        outer = StratifiedKFold(n_splits=n_folds, shuffle=True,
                                random_state=seed)
        for train_rel, test_rel in outer.split(X_raw[human_idx], y[human_idx]):
            train_idx = np.concatenate((human_idx[train_rel], assistant_idx))
            test_idx = human_idx[test_rel]
            candidate = search(seed)
            candidate.fit(X_raw[train_idx], y[train_idx])
            held_truth.extend(y[test_idx].tolist())
            held_pred.extend(candidate.predict(X_raw[test_idx]).tolist())
            selected_cs.append(float(candidate.best_params_["clf__C"]))
        seed_scores.append(float(balanced_accuracy_score(held_truth, held_pred)))

    cv_mean = float(np.mean(seed_scores))
    cv_std = float(np.std(seed_scores))
    deployed = cv_mean >= ACCURACY_THRESHOLD
    C_selected = float(np.median(selected_cs))

    if deployed:
        final = search(CV_SEEDS[0])
        final.fit(X_raw, y)
        C_selected = float(final.best_params_["clf__C"])
        model = final.best_estimator_.named_steps["clf"]
        scaler = final.best_estimator_.named_steps["scale"]
        joblib.dump(model, _mf())
        joblib.dump(scaler, _sf())
        logger.info(
            "Model deployed — balanced_acc: %.2f ± %.2f  C=%.3f  n=%d",
            cv_mean, cv_std, C_selected, n,
        )
    else:
        logger.info(
            "Model not deployed — human holdout balanced_acc: %.2f ± %.2f < threshold %.2f",
            cv_mean, cv_std, ACCURACY_THRESHOLD,
        )

    result = TrainResult(
        cv_accuracy=cv_mean, cv_std=cv_std,
        n_samples=n, n_pos=n_pos, n_neg=n_neg,
        n_folds=n_folds, deployed=deployed,
        C_selected=C_selected,
        n_human=len(human_idx), n_assistant=len(assistant_idx),
        cv_seed_scores=tuple(seed_scores),
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
        "n_human":     len(human_idx),
        "n_assistant": len(assistant_idx),
        "cv_seed_scores": [round(score, 4) for score in seed_scores],
        "evaluation":  "human_holdout_nested_cv",
        "evaluation_version": EVALUATION_VERSION,
        "dataset_sha256": signature,
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
        "threshold":  ACCURACY_THRESHOLD,
        "min_total":  MIN_TOTAL,
        "min_pos":    MIN_POS,
        "min_neg":    MIN_NEG,
        "min_human_total": MIN_HUMAN_TOTAL,
        "min_human_pos": MIN_HUMAN_POS,
        "min_human_neg": MIN_HUMAN_NEG,
    }

    log_path = _log_file()
    if log_path.exists():
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        if rows:
            last = rows[-1]
            status["last_evaluation"] = {
                key: last.get(key) for key in
                ("date", "n_samples", "n_human", "n_assistant",
                 "cv_accuracy", "cv_std", "deployed", "evaluation")
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
