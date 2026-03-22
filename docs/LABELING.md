> 中文版：[LABELING_zh.md](LABELING_zh.md)

# fairing — Labeling System Reference

**Version**: v1.0.0

---

## Overview

fairing uses a three-tier labeling system. All tiers write to the same `feedback.jsonl` and trigger `maybe_auto_train()` after each label.

```
\r (run_digest)
  └─ _save_pending()          sample articles → rate_pending.json
        │
        ▼
\rate  (Tier 1 — Mandatory Daily Batch)
  └─ _run_rate()   label today's sample (random order); rate-gate blocks next \r
        │
        ▼
\rate --ext  (Tier 2 — Extended Labeling)
  └─ _run_ext_rate()    label all unlabeled from title_index; random order; no time limit
        │
        ▼
\lb  (Tier 3 — Label Browser)
  └─ _edit_label_entry()    search + edit any labeled entry

All three tiers → feedback.jsonl → maybe_auto_train()
```

---

## Data Files

### rate_pending.json

Tracks the daily labeling target. Written by `\r`, consumed by `\rate`.

```json
{
  "run_date":  "2026-03-21",
  "n":         5,
  "completed": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `run_date` | string | Beijing-date of the `\r` run |
| `n` | int | Number of labels required today (3–8, proportional to article count) |
| `completed` | bool | `true` once today's label count meets `n` |

`completed` is set to `true` by any path that produces enough labels: `\rate`, `\im`, or `\lb`. Progress is computed live from `feedback.jsonl` (count of today's labels) rather than tracking specific article URLs.

### feedback.jsonl

Append-only training labels. On load, deduplicated by URL (latest entry wins).

```json
{"url": "https://...", "title": "...", "source": "HN", "label": 1, "label_index": 42, "date": "2026-03-21"}
```

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Article URL (original, not normalized) |
| `title` | string | Article title at time of labeling |
| `source` | string | RSS source name |
| `label` | int | `1` = relevant, `-1` = not interested |
| `label_index` | int | Monotonically increasing counter (used for decay weight) |
| `date` | string | Beijing date of label (YYYY-MM-DD) |

### title_index.jsonl

Index of all articles ever seen by fairing. Used as the candidate pool for `\rate --ext` and `\lb`.

```json
{"article_id": "a1b2c3d4e5f6a7b8", "url": "https://...", "title": "...", "source": "HN", "date": "2026-03-21"}
```

| Field | Type | Description |
|-------|------|-------------|
| `article_id` | string | `sha256(normalize_url(url))[:16]` — 16 hex chars |
| `url` | string | Article URL |
| `title` | string | Article title |
| `source` | string | RSS source name |
| `date` | string | Beijing date first seen |

---

## Tier 1 — Mandatory Daily Batch (`\rate`)

### Target count: `_calc_sample_n()`

Called at the end of each `\r` run to determine today's labeling goal.

```python
n = min(8, max(3, len(articles) // 4))   # 3–8, proportional to today's article count
```

Written to `rate_pending.json` as `{"run_date": ..., "n": n, "completed": false}`.
If today's label count in `feedback.jsonl` already meets `n`, `completed` is set to `true` immediately.

### Card Loop: `_run_rate()`

Draws articles from the **full unlabeled pool** (`title_index.jsonl`, same as `\rate --ext`) in random order and presents them as cards until `n` labels have been saved today. Progress is computed live from `feedback.jsonl` so labels from any path (`\im`, `\lb`, etc.) count toward the daily target.

On each card, the following keys are accepted:

| Key | Action |
|-----|--------|
| `+` | Label as relevant (label=1), advance |
| `-` | Label as not interested (label=-1), advance |
| `n` | Skip — no label recorded, advance |
| `o` | Open URL in system browser |
| `d` | Send to payload queue (`payload_queue.json`) |
| `p` | Go back to previous card |
| `s` | Save progress and exit |

After each `+` or `-`, `save_feedback()` appends to `feedback.jsonl` and calls `maybe_auto_train()`.

### Rate-Gate

After `\r`, `rate_pending.json` is written. The next `\r` checks:

```
if rate_pending exists AND completed=false:
    block run with warning — "complete \rate first, or use --force"
```

Use `\r --force` to bypass. The gate exists to ensure at least one daily labeling session. `completed` is automatically set when today's label count (from any source) reaches `n`.

---

## Tier 2 — Extended Labeling (`\rate --ext`)

### Prerequisites

`\rate --ext` requires both:
1. `rate_pending.json` exists.
2. `rate_pending.completed == true` (today's mandatory batch is done).

If either condition fails, the command is blocked with an explanation.

### Pool Construction

All articles from `title_index.jsonl` that are not in `already_labeled`, presented in random order.

No time-window limit — articles from any date are eligible.

### Behavior

- Presents the same card interface as Tier 1.
- User labels as many as they wish; exits with `s`.
- No completion state is tracked — repeated invocations continue from the current unlabeled pool.

---

## Tier 3 — Label Browser (`\lb`)

### Invocation

```
\lb [english keywords]
```

Without keywords, shows the 20 most recently labeled articles. With keywords, filters by title.

### Search Logic

- Case-insensitive, AND logic across all keywords.
- Search pool: all entries in `feedback.jsonl` (deduplicated by URL).
- Example: `\lb query optimizer` matches titles containing both "query" and "optimizer".

### Pagination

`PAGE_SIZE = 20`. Results are shown 20 at a time with `[n]ext / [p]rev / [q]uit` navigation.

### `_edit_label_entry()` Flow

From the list, enter an article number to edit its label:

```
Select entry number (or q to quit): 3
Current label: relevant (+1)
New label [+/-/n to skip]: -
```

Edit appends a new entry to `feedback.jsonl` (dedup-on-load means the new entry wins). `maybe_auto_train()` is called after each edit.

---

## Auto-Train Trigger

`maybe_auto_train()` is called after every label saved by any tier.

```python
MIN_TOTAL = 80       # minimum total labels
MIN_POS   = 5        # minimum positive labels
MIN_NEG   = 5        # minimum negative labels
ACCURACY_THRESHOLD = 0.75   # minimum balanced_accuracy to deploy
```

Flow:
1. Load `feedback.jsonl`, deduplicate by URL (keep latest).
2. If `total < 80` or `pos < 5` or `neg < 5`: print progress, return.
3. Build embeddings (from `scoring_store.jsonl` cache or fresh encode).
4. Apply decay weights: `weight = 0.5 ** floor(labels_since / 3)`.
5. Fit `LogisticRegressionCV` with `StandardScaler`, `class_weight='balanced'`.
6. Validate with `StratifiedKFold`, compute `balanced_accuracy`.
7. If `mean >= 0.75`: save `personal_model.pkl` + `personal_scaler.pkl`. Model is deployed.
8. If `mean < 0.75`: print score, keep collecting.

---

## Data File Write Summary

| Action | Files Written |
|--------|--------------|
| `\r` completes | `rate_pending.json` (new sample) |
| Label via `+` or `-` | `feedback.jsonl` (append) |
| Extended label | `feedback.jsonl` (append) |
| Edit via `\lb` | `feedback.jsonl` (append, dedup-on-load) |
| Auto-train succeeds | `personal_model.pkl`, `personal_scaler.pkl` |
| `d` key during labeling | `payload_queue.json` (append) |

---

## Card Interface Keys

| Key | Available in | Action |
|-----|-------------|--------|
| `+` | `\rate`, `\rate --ext` | Label relevant (label=1) |
| `-` | `\rate`, `\rate --ext` | Label not interested (label=-1) |
| `n` | `\rate`, `\rate --ext` | Skip (no label) |
| `o` | `\rate`, `\rate --ext` | Open in browser |
| `d` | `\rate`, `\rate --ext` | Add to payload queue |
| `p` | `\rate`, `\rate --ext` | Previous article |
| `s` | `\rate`, `\rate --ext` | Save and exit |
| number | `\lb` | Select entry to edit |
| `+`/`-` | `\lb` edit prompt | New label |
| `n` | `\lb` edit prompt | Cancel edit |
| `q` | `\lb` | Quit browser |
