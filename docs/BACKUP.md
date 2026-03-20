> 中文版：[BACKUP_zh.md](BACKUP_zh.md)

# fairing — Backup & Restore Reference

**Version**: v1.0.0

---

## Overview

```
\r (run_digest)
  └─ run_backup()             automatic backup after every successful run

\bk (manual)
  └─ run_backup()             same function, user-triggered

\rs (restore)
  └─ list_backups()           show available snapshots
  └─ all_identical()          MD5 check — skip if backup equals live files
  └─ diff_summary()           per-file comparison
  └─ "yes" confirmation
  └─ restore_backup()         copy files back to DATA_DIR
```

---

## Backed-Up Files

The following 6 files are copied on every backup:

| File | Location in backup | Description |
|------|--------------------|-------------|
| `feedback.jsonl` | `BACKUP_DIR/YYYY-MM-DD/data/feedback.jsonl` | Training labels — most critical |
| `seen_urls.json` | `BACKUP_DIR/YYYY-MM-DD/seen_urls.json` | URL dedup state |
| `scoring_store.jsonl` | `BACKUP_DIR/YYYY-MM-DD/scoring_store.jsonl` | Embedding cache |
| `title_index.jsonl` | `BACKUP_DIR/YYYY-MM-DD/title_index.jsonl` | Article index |
| `rate_pending.json` | `BACKUP_DIR/YYYY-MM-DD/rate_pending.json` | Labeling progress |
| `payload_queue.json` | `BACKUP_DIR/YYYY-MM-DD/payload_queue.json` | Pending payload articles |

### Not Backed Up

These files are excluded intentionally:

| File | Reason |
|------|--------|
| `last_run_articles.json` | Ephemeral — regenerated on next `\r` |
| `digest_hash` | Ephemeral — email guard, not state |
| `last_run_time` | Ephemeral — runtime timestamp |
| `personal_model.pkl` | Regenerable from `feedback.jsonl` |
| `personal_scaler.pkl` | Regenerable from `feedback.jsonl` |

---

## Backup Directory Layout

```
BACKUP_DIR/
  2026-03-19/
    data/
      feedback.jsonl
    seen_urls.json
    scoring_store.jsonl
    title_index.jsonl
    rate_pending.json
    payload_queue.json
  2026-03-20/
    data/
      feedback.jsonl
    ...
  2026-03-21/
    ...
```

- One directory per calendar day (Beijing time).
- Same-day backups overwrite the previous snapshot for that day.
- `_prune()` removes directories older than `RETAIN_DAYS` (default 7).

---

## Backup Write Flow: `run_backup()`

```python
dest = BACKUP_DIR / today_beijing()   # e.g. "2026-03-21"
dest.mkdir(parents=True, exist_ok=True)

for src_path, rel_path in BACKUP_FILES:
    if src_path.exists():
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, target)

_prune(BACKUP_DIR, RETAIN_DAYS)
```

`shutil.copy2` preserves file metadata. Same-day re-runs overwrite without error. `_prune()` iterates `BACKUP_DIR`, parses directory names as dates, and deletes directories older than `RETAIN_DAYS` days.

---

## Restore Flow

Five steps, executed by `\rs`:

### Step 1: `list_backups()`

Lists all date directories in `BACKUP_DIR`, sorted newest-first, with file counts.

```
Available backups:
  [1] 2026-03-21  (6 files)
  [2] 2026-03-20  (6 files)
  [3] 2026-03-19  (5 files)
Select backup to restore (or q to quit):
```

### Step 2: `all_identical()` — MD5 check

Computes MD5 of each file in the selected backup and the corresponding live file. If all files are byte-identical, restore is skipped with a message: "Backup is identical to current data — nothing to restore."

### Step 3: `diff_summary()` — Per-file comparison

For each backed-up file, reports:

| Status | Meaning |
|--------|---------|
| `identical` | File matches live version |
| `modified` | File differs (shows line count delta) |
| `missing in backup` | File does not exist in snapshot |
| `missing locally` | Live file was deleted |

Example output:
```
feedback.jsonl     modified  (+12 lines in backup vs live)
seen_urls.json     identical
scoring_store.jsonl modified (+3 entries)
title_index.jsonl  identical
rate_pending.json  modified
payload_queue.json identical
```

### Step 4: Confirmation

```
Restore from 2026-03-20? This will overwrite live files. Type "yes" to confirm:
```

Any input other than `yes` cancels the operation.

### Step 5: `restore_backup()`

Copies each file from the selected backup directory to `DATA_DIR`, preserving subdirectory structure.

---

## Restore Consequences

| File Restored | Effect |
|---------------|--------|
| `feedback.jsonl` | Training labels roll back; next `\rate` may retrain with older data |
| `seen_urls.json` | Articles from between backup and now may reappear in next `\r` |
| `scoring_store.jsonl` | Embedding cache rolls back; new embeddings will be recomputed |
| `title_index.jsonl` | Article index rolls back; `\rate --ext` and `\lb` pools shrink |
| `rate_pending.json` | Labeling progress rolls back to backup state |
| `payload_queue.json` | Payload queue rolls back; articles added since backup are lost |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_DIR` | `~/Documents/fairing/data_bak` | Root directory for all backup snapshots |
| `RETAIN_DAYS` | `7` | Number of daily snapshots to keep before pruning |

Both can be overridden in `.env`.

---

## Multi-Device Notes

When using `DATA_DIR` with cloud sync:

- Backup runs automatically after `\r` on whichever device executes the run.
- `BACKUP_DIR` is a separate local path — it is **not** synced across devices by default.
- For cross-device backup coverage, set `BACKUP_DIR` to a cloud-synced location (e.g., a subfolder of OneDrive).
- `\rs` always restores to `DATA_DIR`, so both devices benefit from the same restore operation if `DATA_DIR` is shared via cloud.
