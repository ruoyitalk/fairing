> 中文版：[OPERATIONS_zh.md](OPERATIONS_zh.md)

# fairing — Operations Reference

**Version**: v1.0.0

---

## Daily Workflow

```
1. \r                      fetch RSS, score, write files, email, backup, sample pending
2. \rate                   label today's mandatory sample (3–8 articles)
3. \rate --ext (optional)  label more from historical pool
4. \lb (optional)          search and correct past labels
```

The rate-gate ensures `\r` is not run again on the same day without completing `\rate` first, unless `--force` is passed.

---

## Per-Command Reference

### `\r` — Run Digest

```
\r [--no-md] [--no-notebook] [--no-mail] [--chinese] [--force]
```

Full pipeline run. Sequence:

1. `fetch_rss()` — pull articles from all enabled sources using dynamic lookback.
2. `filter_unseen()` — two-layer dedup: normalized URL + normalized title.
3. Optional Firecrawl full-text fetch for configured sources.
4. `enrich()` — compute `all-MiniLM-L6-v2` embeddings; cache in `scoring_store.jsonl`.
5. `score_articles()` — rank by personal model (if deployed) or fallback heuristic.
6. `write_obsidian()` — write Obsidian vault `.md` (unless `--no-md`).
7. `write_notebooklm()` — write NotebookLM source file (unless `--no-notebook`).
8. `mark_seen()` — record URLs in `seen_urls.json`.
9. `send_digest()` — send HTML email (unless `--no-mail`); `--chinese` translates.
10. `run_backup()` — copy data files to `BACKUP_DIR`.
11. `_save_pending()` — sample 3–8 articles; write `rate_pending.json`.

| Flag | Description |
|------|-------------|
| `--no-md` | Skip Obsidian output this run |
| `--no-notebook` | Skip NotebookLM output this run |
| `--no-mail` | Skip email send |
| `--chinese` | Translate email body to Chinese |
| `--force` | Bypass rate-gate even if `rate_pending` is incomplete |

Persistent defaults: set `RUN_MD`, `RUN_NOTEBOOK`, `RUN_CHINESE`, `RUN_NO_MAIL` in `.env`.

---

### `\rate` — Mandatory Daily Labeling

```
\rate [--ext]
```

Without `--ext`: presents today's mandatory sample from `rate_pending.json`. Blocks until all articles are processed or user exits with `s`. Sets `completed=true` when done.

With `--ext`: extended mode. Requires `rate_pending.completed == true`. Presents all unlabeled articles from `title_index.jsonl` (newest-first, no time limit).

---

### `\lb` — Label Browser

```
\lb [english keywords]
```

Browse and edit labeled articles. Without keywords, shows the 20 most recently labeled. With keywords, filters by title (case-insensitive AND logic). `PAGE_SIZE=20`; navigate with `[n]ext / [p]rev / [q]uit`.

Enter an article number to edit its label. Edit appends to `feedback.jsonl` and triggers `maybe_auto_train()`.

---

### `\ms` — Model Status

```
\ms
```

Displays:
- Classifier deployment status (deployed / not deployed).
- Label counts: total, positive, negative.
- Progress toward `MIN_TOTAL=80`.
- If deployed: balanced_accuracy from last training run, top signal words (TF-IDF proxy).
- Training history: last 10 training attempts (date / samples / accuracy±std / C / deployed).

---

### `\re` — Resend Email

```
\re
```

Rebuilds today's full article list from `last_run_articles.json` and force-sends the email (bypassing the MD5 duplicate guard). Useful when the email was not received or needs to be resent after configuration changes.

---

### `\dl` — Rebuild Local Files

```
\dl
```

Rebuilds Obsidian and NotebookLM output files from the last run's data without fetching RSS or sending email. Use on a secondary device to generate local files from synced data.

---

### `\t` — Toggle Source

```
\t <N>
```

Enable or disable RSS source by its index number (shown in `\c`). Writes the `disabled` list to `sources.local.yaml`.

---

### `\c` — Config / Sources

```
\c
```

List all configured RSS sources with:
- Index number.
- Name and URL.
- 7-day article count.
- Label quality: positive labels / total labeled for this source (color-coded).
- Time since last article.
- Enable/disable status.

---

### `\e` — Env

```
\e
\e set KEY VALUE
```

Without arguments: display all `.env` variables and current run-behavior defaults.

With `set KEY VALUE`: update or add a variable in `.env`. Always shows current `RUN_*` effective values.

---

### `\l` — Log

```
\l
```

Show run history: date, article count per source, email sent status, and any feed errors encountered.

---

### `\bk` — Manual Backup

```
\bk
```

Trigger `run_backup()` manually. Same as the automatic backup that runs after `\r`. Overwrites today's snapshot if one exists.

---

### `\rs` — Restore

```
\rs
```

Interactive restore flow:
1. `list_backups()` — show available snapshots (newest-first).
2. Select a date.
3. `all_identical()` — MD5 check; skip if backup matches live files.
4. `diff_summary()` — per-file comparison report.
5. Type `yes` to confirm.
6. `restore_backup()` — copy files from backup to `DATA_DIR`.

---

### `\pd` — Payload Queue

```
\pd
\pd clear
```

`\pd`: view current `payload_queue.json` contents.
`\pd clear`: clear the queue (with confirmation).

---

### `\ps` — Payload Search

```
\ps [english keywords]
```

Browse or search all known articles for payload queuing. Without keywords, shows all articles (paginated, newest first). With keywords, filters by title (AND-matched, case-insensitive). Navigate with `n`/`p`, select entries by number across pages, confirm to add to `payload_queue.json`. Optionally label selected articles.

---

### `\sd` — Send by ID

```
\sd <article_id>
```

Look up an article by its 16-hex `article_id` and add it to `payload_queue.json`. Displays metadata for confirmation. Optionally label after queuing.

---

### `\fb` — Label an Article by ID

```
\fb <article_id>
```

Label a specific article as relevant (`+`) or not interested (`-`). Use after reading a full article via the payload consumer to record a high-quality judgment. Triggers `maybe_auto_train()` after saving.

---

### `\slr` — Suspicious Label Review

```
\slr
```

Shows articles where the model's prediction strongly disagrees with the saved label (disagreement > 60%). Sorted by disagreement magnitude. Allows re-labeling in place. Triggers `maybe_auto_train()` after any changes.

Requires a deployed model (`\ms` to check status).

---

### `\li` — List Title Index

```
\li
```

Display the most recent entries in `title_index.jsonl`. Useful for finding `article_id` values for `\sd`.

---

### `\?` / `\h` — Help

```
\?
```

Display the shortcut reference table.

---

### `\q` — Quit

```
\q
```

Exit the interactive shell.

---

## Key Concepts

### article_id

```python
article_id = sha256(normalize_url(url))[:16]
```

16 hex characters (64-bit) derived from the normalized URL. Used as the stable identifier across all data files and the payload queue.

### Rate-Gate

After each `\r`, `rate_pending.json` is written with `completed=false`. The next `\r` on the same day checks this file and blocks if it is incomplete. Use `\r --force` to bypass.

Purpose: ensure at least one daily labeling session before re-running the digest.

### Dynamic Lookback

```python
effective_window = max(LOOKBACK_MIN_HOURS, hours_since_last_run)
LOOKBACK_MIN_HOURS = 25
```

fairing automatically extends the lookback window if runs are delayed, catching up missed articles. First run uses `2026-03-20` as the epoch.

### MAIL_SPLIT_N

When `MAIL_SPLIT_N` is set in `.env`, the digest email is split into N parts (one per email). Used to work around email client rendering limits on very long digests.

### top_n()

`top_n()` selects the top N articles by score for the email digest. Articles beyond `top_n` are listed as title-only. Default `N=20`; configurable via `TOP_N` in `.env`.

---

## Troubleshooting

### Rate-gate blocked

```
Warning: \rate incomplete — run \rate before next \r (or use --force)
```

Run `\rate` to complete today's labeling sample. Or `\r --force` to bypass.

### `\rate --ext` blocked

```
Error: mandatory \rate not completed — run \rate first
```

`\rate --ext` requires `rate_pending.completed == true`. Run `\rate` first.

### Email not sent

- Check `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` in `.env`.
- Use `\re` to retry sending without re-running the full pipeline.
- Check `\l` for recent error messages.

### Model not deploying

- Check `\ms` for label counts. Need `MIN_TOTAL=80`, `MIN_POS=5`, `MIN_NEG=5`.
- If counts are sufficient but model still not deploying, `balanced_accuracy < 0.75`. Continue labeling — more diverse samples improve accuracy.
- To force retrain: delete `personal_model.pkl` and `personal_scaler.pkl` from `DATA_DIR`, then run `\rate`.

### Feed errors in `\l`

- Verify feed URL is still valid.
- Use `\t <N>` to temporarily disable a broken source.
- Check if `lookback_hours` is appropriate — some sources (e.g., arXiv) need `48`.
