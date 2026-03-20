> 中文版：[TRAINING_zh.md](TRAINING_zh.md)

# fairing — Training Principles

**Version**: v1.0.0
**Archive**: [docs/archive/v0.2.0/](archive/v0.2.0/) *(original Chinese-only version)*

---

## Architecture Overview

```
Daily Articles / 每日文章
┌─────────────────────────────────────┐
│  title + excerpt + full_text (opt)  │
└──────────────┬──────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  sentence-transformers/all-MiniLM-L6-v2  │
│  "Distributed query optimizer"           │
│   → [0.12, -0.34, 0.87, ...]  (384-dim) │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────┐
│  StandardScaler          │  zero-mean, unit-variance per dimension
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  LogisticRegressionCV                │
│  class_weight = balanced             │
│  scoring = balanced_accuracy         │
│  MIN_TOTAL = 80 labels required      │
└──────────────┬───────────────────────┘
               │
        P(relevant) ∈ [0, 1]
               │
               ▼
┌──────────────────────────────────────┐
│  Top 20  → full detail display       │
│  Rest    → title-only list           │
└──────────────────────────────────────┘
```

---

## Why Sentence Transformers?

Traditional TF-IDF treats words as independent tokens.
"Query optimization" and "query planner" share zero TF-IDF overlap despite being semantically close.

Sentence Transformers encode text into dense vector space where **semantically similar text is geometrically close**:

```
Embedding space (2D projection / 二维投影示意)

 ┌─────────────────────────────────────────┐
 │  ● distributed systems                  │
 │   ● consensus algorithm                 │  ← positive cluster / 正样本聚类
 │    ● query optimizer                    │
 │                                         │
 │         · · · · · ·                     │  ← neutral zone / 中性区
 │                                         │
 │             ○ Python beginner           │
 │              ○ tutorial intro           │  ← negative cluster / 负样本聚类
 │               ○ Hello World            │
 └─────────────────────────────────────────┘
```

`all-MiniLM-L6-v2` is a distilled BERT variant trained on 1B+ sentence pairs.
Its 384-dim embeddings are a strong prior — **no fine-tuning needed**.

| Property | Value |
|----------|-------|
| Inference speed | ~30 ms/article on CPU |
| Language | Primarily English |
| Model size | 22 MB, auto-cached |
| Semantic property | cosine similarity ≈ semantic similarity |

---

## Why Logistic Regression?

With pre-trained embeddings, positive and negative classes are often **linearly separable** in 384-dim space. A linear classifier is sufficient with clear advantages:

| Property | Logistic Regression | Neural Network |
|----------|--------------------|----|
| Parameters | 384 weights + bias | millions |
| Overfit risk (30 samples) | Low (regularized) | Very high |
| Training time | < 1 second | minutes |
| Interpretability | High (per-dim weights) | Low |
| Probability calibration | Built-in (Platt scaling) | Requires extra steps |

---

## Regularization: LogisticRegressionCV

**Problem**: 384 features, ~30 samples → severe over-parameterization.

L2 regularization adds a penalty to the loss:

```
loss = cross_entropy(y, ŷ) + (1/C) × ||w||²

C small → strong regularization → weights pushed toward zero
C large → weak regularization  → may overfit
```

`LogisticRegressionCV` auto-selects C via cross-validation:

```
C candidates:  0.001  0.01  0.1  1.0  10.0

CV accuracy (example):
  0.001  ████████░░░░  0.62  ← underfit
  0.01   ████████████  0.78
  0.1    ██████████░░  0.76  ← selected
  1.0    ████████░░░░  0.71
  10.0   █████░░░░░░░  0.58  ← overfit
```

---

## Class Balance

Users tend to label articles they like (positive) and skip boring ones (negative) → **class imbalance**.

Without correction, the model learns "always predict relevant" — high raw accuracy, useless in practice.

`class_weight='balanced'` applies inverse-frequency weights:

```
Example: 70 positive, 30 negative

w_pos = 100 / (2 × 70) = 0.71
w_neg = 100 / (2 × 30) = 1.67
      ↑ negatives get ~2× gradient signal
```

---

## Evaluation: balanced_accuracy

Raw accuracy is misleading with class imbalance. We use **balanced accuracy**:

```
balanced_accuracy = 0.5 × (sensitivity + specificity)
                  = 0.5 × (TP/(TP+FN) + TN/(TN+FP))

Model that always predicts "relevant":
  sensitivity = 1.0  (never misses a positive)
  specificity = 0.0  (misclassifies all negatives)
  balanced_accuracy = 0.5  ← same as random guessing

A well-trained model:
  balanced_accuracy = 0.80  ← actually useful
```

**Deploy threshold: balanced_accuracy ≥ 0.75**

Roughly: 75% of recommended articles are actually relevant; 75% of filtered articles are genuinely uninteresting.

### Stratified K-Fold

```
n_folds = min(5, min(n_positive, n_negative))

Each fold preserves the positive/negative ratio.
每折保留相同的正负比例。
```

---

## Decay Weights

### Motivation

Interests drift over time. An article topic labeled "highly relevant" three weeks ago may be background knowledge now.

### Count-based decay (not calendar time)

Forgetting speed is tied to **reading pace**, not the calendar:
- Label 3 articles/day → faster decay
- Label 3 articles/week → slower decay

```python
DECAY_BASE = 0.5   # weight halves each generation / 每档权重减半
DECAY_UNIT = 3     # every 3 new labels = 1 forgetting generation / 每 3 条新标注 = 1 衰减档

weight = DECAY_BASE ** floor(labels_since / DECAY_UNIT)
```

```
Decay curve / 衰减曲线:

weight │
1.0    │████ ████ ████
       │         ←3→
0.5    │              ████ ████ ████
       │                        ←3→
0.25   │                             ████ ████ ████
0.12   │                                          ████ ███
0.06   │                                               ████
0.0    └──────────────────────────────────────────────────→
       labels added since this label / 此标注之后新增标注数
        0    3    6    9   12   15   18   21   24   27   30
```

Old labels are **never deleted** — their weight approaches zero asymptotically.
Returning to an old topic needs only a few new positive samples to override decayed evidence.

---

## Full Training Loop

```
\rate labeling session / 打标会话
     │
     ├─ show article (text_for_scoring: title + excerpt + full_text snippet)
     │
     ├─ user input: + (relevant) / - (irrelevant) / n (skip) / p (prev) / s (save-quit)
     │
     └─ save_feedback(url, label, label_index, date)
              │
              ▼
       data/feedback.jsonl  ← git-tracked; survives machine changes
       (URL-deduplicated on read: keep latest entry per URL)
              │
              ▼
       maybe_auto_train()
              │
              ├─ pos < 5 OR neg < 5 OR total < MIN_TOTAL (80)?
              │         └─ "keep collecting" → exit
              │
              ▼
       build X (embeddings), y (labels), w (decay weights)
              │
              ▼
       StandardScaler.fit_transform(X)
              │
              ▼
       LogisticRegressionCV
         - inner CV auto-selects C
         - class_weight=balanced
              │
              ▼
       outer StratifiedKFold validation
         → balanced_accuracy per fold → mean ± std
              │
              ├─ mean < 0.75? → "not deployed, keep labeling"
              │
              └─ mean ≥ 0.75? → save model + scaler
                                  → next run uses personal scoring
```

---

## Sampling Strategy for \rate

The mandatory daily batch uses **simple random sampling** from today's new unlabeled articles:

```python
n    = min(8, max(3, len(articles) // 4))   # 3–8 articles, proportional to daily count
pool = [a for a in articles if a["url"] not in already_labeled]
sample = random.sample(pool, min(n, len(pool)))
```

`\rate --ext` (extended mode) presents **all** unlabeled articles from `title_index.jsonl`
in chronological order (newest first) with no time-window limit — the user labels as many
as they wish and exits with `s`.

---

## Storage Design

| File | Location | Git-tracked | Notes |
|------|----------|------------|-------|
| Feedback labels | `DATA_DIR/data/feedback.jsonl` | **No** (since v1.0.0) | Only file needed for retraining |
| Article index | `DATA_DIR/title_index.jsonl` | No | All articles seen; pool for `\rate --ext` and `\lb` |
| Embedding cache | `DATA_DIR/scoring_store.jsonl` | No | Rebuildable from articles |
| Classifier | `DATA_DIR/personal_model.pkl` | No | Rebuildable from feedback |
| Scaler | `DATA_DIR/personal_scaler.pkl` | No | Rebuildable from feedback |

All data files live under `DATA_DIR`. If model files are lost, run `\rate` — auto-retrain triggers after enough labels.

---

## Known Limitations

**Small-sample variance**
With 20–50 labels, CV accuracy variance is ±10–15%.
A reported `0.78 ± 0.12` means true generalization accuracy may be 0.66–0.90.

**English-only pipeline**
`all-MiniLM-L6-v2` is English-biased. Articles with >25% CJK characters are excluded at ingestion.
`--chinese` only translates English articles for email output; it does not accept Chinese content as input.

**Sudden interest shifts**
Abrupt topic changes need 15–20 new labels for the model to adapt.
Decay handles gradual drift well; sudden shifts need focused labeling sessions.

**Excerpt quality**
Many RSS feeds provide only 2–3 sentence excerpts.
Enable Firecrawl full-text per source for significantly better signal quality.
