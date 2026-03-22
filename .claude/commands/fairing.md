# fairing v1.0.0 — personal RSS digest with active-learning relevance classifier

fairing fetches tech articles, scores them by personal preference, delivers a daily email digest,
and continuously improves its model through interactive labeling sessions.

## Architecture

```
main.py              — interactive CLI shell (cmd.Cmd) + run_digest() entry point
fairing/
  config.py          — RssSource dataclass, Config (loads sources.yaml + sources.local.yaml)
  paths.py           — all DATA_DIR-relative path helpers (centralized path routing)
  rss.py             — fetch_rss(): feedparser, dynamic lookback, CJK filter, retry logic
  state.py           — seen_urls dedup (normalize URL + title), today_beijing() UTC+8
  embedder.py        — sentence-transformers all-MiniLM-L6-v2, scoring_store.jsonl cache
  scorer.py          — LogisticRegressionCV on 384-dim embeddings → article score
  trainer.py         — feedback I/O, decay weights, StratifiedKFold CV, auto-train trigger
  mailer.py          — SMTP via 163, HTML email, send_digest(), MAIL_SPLIT_N support
  writer.py          — write_obsidian(), write_notebooklm(), archive_vault()
  translator.py      — Gemini-based EN→ZH translation (email only, training data stays EN)
  backup.py          — timestamped daily backups, diff_summary(), restore_backup()
  reader.py          — URL type detection, browser open, fetch_full() for excerpt enrichment only
  export.py          — payload queue management: add, search, send_by_id, list
data/
  feedback.jsonl     — append-only training labels (url, title, source, label, date)
config/
  sources.yaml       — public RSS sources
  sources.local.yaml — private sources + `disabled: [name, ...]` list
```

## Data File Routing (paths.py)

All data file paths are resolved through `paths.py` helpers. Key functions:

| Helper | File |
|--------|------|
| `feedback_file()` | `DATA_DIR/data/feedback.jsonl` |
| `seen_urls_file()` | `DATA_DIR/seen_urls.json` |
| `scoring_store_file()` | `DATA_DIR/scoring_store.jsonl` |
| `rate_pending_file()` | `DATA_DIR/rate_pending.json` |
| `title_index_file()` | `DATA_DIR/title_index.jsonl` |
| `last_run_time_file()` | `DATA_DIR/last_run_time` |
| `payload_queue_file()` | `DATA_DIR/payload_queue.json` |
| `feed_errors_file()` | `DATA_DIR/feed_errors.json` |
| `model_file()` | `DATA_DIR/personal_model.pkl` |
| `scaler_file()` | `DATA_DIR/personal_scaler.pkl` |
| `digest_hash_file()` | `DATA_DIR/digest_hash` |

## Key Data Files (gitignored unless noted)

| File | Purpose |
|------|---------|
| `DATA_DIR/data/feedback.jsonl` | Training labels — most critical; not git-tracked (v1.0.0+) |
| `DATA_DIR/seen_urls.json` | Dedup state, 30-day rolling window |
| `DATA_DIR/scoring_store.jsonl` | Embedding cache (url→384-dim vector) |
| `DATA_DIR/rate_pending.json` | Today's labeling target `{run_date, n, completed}` |
| `DATA_DIR/title_index.jsonl` | All articles ever seen; unlabeled pool for `\rate` and `\rate --ext` |
| `DATA_DIR/last_run_time` | Timestamp of last `\r` run (for dynamic lookback) |
| `DATA_DIR/payload_queue.json` | Queued article stubs for external payload consumer |
| `DATA_DIR/feed_errors.json` | Feed fetch errors from last `\r` |
| `DATA_DIR/digest_hash` | MD5 of last email content (prevents duplicate send) |
| `DATA_DIR/personal_model.pkl` | Trained LogisticRegressionCV model |
| `DATA_DIR/personal_scaler.pkl` | Paired StandardScaler |

## Shell Commands (`\shortcut` → `command`)

| Shortcut | Command | Params | Description |
|----------|---------|--------|-------------|
| `\r` | `run` | `[--no-mail] [--chinese] [--force]` | Full pipeline: RSS → embed → score → write digest → email → backup |
| `\rate` | `rate` | `[--ext]` | Mandatory daily labeling; `--ext` = all unlabeled from title_index |
| `\lb` | `labels` | `[keywords]` | Browse and edit labeled articles; keyword search (AND, case-insensitive) |
| `\ms` | `model_status` | | Classifier status, training history, signal words |
| `\slr` | `label_review` | | Review labels where model disagrees >60% |
| `\re` | `resend` | | Rebuild today's full article list and force-send email |
| `\dl` | `rebuild` | | Rebuild digest file without email (secondary device sync) |
| `\t` | `toggle` | `<N>` | Enable/disable an RSS source by index |
| `\c` | `config` | | Sources with 7-day counts, label quality, last-seen time |
| `\e` | `env` | `[set KEY VALUE]` | View/edit .env |
| `\l` | `log` | | Run history with today's per-source breakdown |
| `\bk` | `backup` | | Manual backup trigger |
| `\rs` | `restore` | | Restore from backup with diff + confirmation |
| `\pd` | `queue` | `[clear]` | View or clear payload_queue.json |
| `\ps` | `queue_search` | `[keywords]` | Browse all articles or filter by title; paginated; batch-add to payload queue |
| `\sd` | `enqueue` | `<article_id>` | Add specific article to payload queue by 16-hex ID |
| `\fb` | `label` | `<article_id>` | Label a specific article (+/-) |
| `\im` | `import_csv` | `<file.csv>` | Batch label/enqueue from CSV (actions: +/-/q/+q/-q/s) |
| `\li` | `license` | | Show MIT license |
| `\?` `\h` | `help` | | Show command reference |
| `\q` | `quit` | | Exit |

## Labeling Flow

```
\r (run_digest)
  → fetch_rss (skip disabled sources; dynamic lookback window)
  → filter_unseen (normalize URL + title)
  → enrich (embed → scoring_store.jsonl; update title_index.jsonl)
  → score_articles (LogisticRegressionCV or heuristic)
  → write_digest (NEWS_DIR/YYYY-WXX/YYYY-MM-DD.md)
  → mark_seen (seen_urls.json)
  → send_digest (MAIL_SPLIT_N support)
  → _save_pending ({run_date, n, completed} → rate_pending.json)
  → run_backup

Three-tier labeling system:

\rate  (Tier 1 — Mandatory Daily Batch)
  → _run_rate(): draw n articles from full unlabeled pool (title_index.jsonl)
  → progress = today's label count in feedback.jsonl (live, any path counts)
  → rate-gate: blocks next \r until completed=true
  keys: + / - / n / o / d / p / s   (d → dispatches to payload queue)

\rate --ext  (Tier 2 — Extended Labeling)
  → prerequisite: rate_pending.completed == true (auto-checked live)
  → pool: all unlabeled from title_index.jsonl, random order, no time limit
  → same card interface and pool logic as Tier 1 (_build_unlabeled_pool)

\lb [keywords]  (Tier 3 — Label Browser)
  → search: feedback.jsonl, case-insensitive AND, PAGE_SIZE=20
  → _edit_label_entry(): append new entry to feedback.jsonl (dedup-on-load)

All tiers → feedback.jsonl → maybe_auto_train()
```

## run Parameters

```
--no-md       skip Obsidian output this run
--no-notebook skip NotebookLM output this run
--no-mail     skip email
--chinese     translate email body to Chinese
--force       bypass rate-gate
```

Default behavior computed from `.env` RUN_* vars at runtime.

## Model Details

- Embeddings: `sentence-transformers/all-MiniLM-L6-v2` (384 dims)
- Classifier: `LogisticRegressionCV` + `StandardScaler`, `class_weight='balanced'`
- Evaluation: `StratifiedKFold`, `balanced_accuracy`, deploy threshold ≥ 0.75
- Decay: article-count-based (`DECAY_BASE=0.5`, `DECAY_UNIT=3`), not time-based
- `MIN_TOTAL=80` labels required before training is attempted

## Documentation Rules

When updating docs, maintain both English and Chinese versions:

| Doc | Content |
|-----|---------|
| `docs/TRAINING.md` / `docs/TRAINING_zh.md` | ML pipeline internals |
| `docs/LABELING.md` / `docs/LABELING_zh.md` | Three-tier labeling system |
| `docs/BACKUP.md` / `docs/BACKUP_zh.md` | Backup and restore |
| `docs/PAYLOAD.md` / `docs/PAYLOAD_zh.md` | Payload queue integration |
| `docs/OPERATIONS.md` / `docs/OPERATIONS_zh.md` | Full operations manual |

## Key Conventions

- All dates use Beijing time (UTC+8) via `today_beijing()` in `state.py`
- `feedback.jsonl` is append-only; edits are handled by appending + deduplicating on URL (keep last)
- `seen_urls.json` stores normalized URLs (tracking params stripped)
- `title_index.jsonl` stores all articles ever seen; never truncated
- `article_id = sha256(normalize_url(url))[:16]` — stable 16-hex identifier
- Dynamic lookback: `effective_window = max(25, hours_since_last_run)`; first run uses 2026-03-20 epoch
- Backups go to `BACKUP_DIR` (default `~/Documents/fairing/data_bak`), 7-day retention
n- Training data always reflects English content; `--chinese` translates only the email copy
- `payload_queue.json` stores article stubs; consumer is responsible for full fetch and state

## Development Workflow (mandatory for every change)

Every code change — no matter how small — must be accompanied by:

1. **Tests**: add or update tests in `tests/test_<module>.py` covering the changed behaviour.
   Run `python -m pytest tests/ -v` and ensure **all 130+ tests pass** before committing.
2. **Docstrings / comments**: update the affected function/module docstrings in English.
3. **Skills file**: update this file (`/.claude/commands/fairing.md`) if commands, flags,
   data files, or workflows change.
4. **Commit**: one focused commit per logical change with a conventional-commit message.
   No Co-Authored-By lines in commits.

```bash
# Validation command (safe — reads no user data, writes to tmp only)
.venv/bin/python -m pytest tests/ -v --tb=short
```

## Other Notes

- Do not introduce new dependencies without checking requirements.txt first
- `sources.local.yaml` is gitignored; `sources.yaml` is the public template
- `feedback.jsonl` is NOT git-tracked from v1.0.0; it lives in `DATA_DIR/data/feedback.jsonl`
- To re-embed articles: delete `scoring_store.jsonl` from `DATA_DIR` (will be rebuilt on next run)
- To force re-send today's email: `\re` (bypasses hash check)
- To retrain model: delete `personal_model.pkl` and `personal_scaler.pkl` from `DATA_DIR`, then `\rate`
- `\li` helps find article_id values for `\sd`
- `\pd clear` is the only supported way to reset the payload queue from within fairing
- Full-text reading is the payload consumer's responsibility; fairing only opens the browser (`o` key)
