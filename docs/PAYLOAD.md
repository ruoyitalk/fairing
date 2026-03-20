> 中文版：[PAYLOAD_zh.md](PAYLOAD_zh.md)

# fairing — Payload Integration Reference

**Version**: v1.0.0

---

## Pipeline Position

```
fairing (run_digest / \rate / \rate --ext / \sd / \ps)
  └─ payload_queue.json          queued article stubs

payload (external consumer)
  └─ reads payload_queue.json
  └─ deduplicates by article_id
  └─ fetches full content
  └─ manages its own state
```

---

## Article ID

```python
article_id = sha256(normalize_url(url))[:16]
```

- 16 hex characters = 64 bits of entropy.
- Collision probability: approximately 9×10⁻⁸ over 100 years of daily operation.
- `normalize_url()` strips tracking parameters and normalizes scheme/host, so the same article URL from different sources maps to the same ID.

---

## payload_queue.json Schema

Each entry in the queue is a JSON object:

```json
{"article_id": "a1b2c3d4e5f6a7b8", "url": "https://...", "title": "...", "source": "HN", "queued_date": "2026-03-21"}
```

| Field | Type | Description |
|-------|------|-------------|
| `article_id` | string | `sha256(normalize_url(url))[:16]` — 16 hex chars |
| `url` | string | Article URL (original, not normalized) |
| `title` | string | Article title at time of queuing |
| `source` | string | RSS source name |
| `queued_date` | string | Beijing date queued (YYYY-MM-DD) |

The file is a JSON array. fairing appends entries and deduplicates by `article_id` before writing.

---

## Three Ways to Add Articles

### 1. `d` key during `\rate` / `\rate --ext`

During any labeling card session, press `d` to send the current article to `payload_queue.json`. The article is added immediately without leaving the card. The card remains visible; the user continues labeling.

### 2. `\sd <id>` — Send by ID

```
\sd a1b2c3d4e5f6a7b8
```

Looks up the article by `article_id` in the search pool (see below). Displays article metadata and prompts for confirmation:

```
Title:  Distributed Query Optimizer in CockroachDB
Source: HN
Date:   2026-03-21

Send to payload queue? [y/n]:
```

After confirmation, optionally prompts to label the article:

```
Label this article? [+/-/n]:
```

### 3. `\ps <keywords>` — Payload Search (batch)

```
\ps query optimizer
```

Searches the search pool for articles matching all keywords (case-insensitive AND logic). Presents paginated results. The user selects entries, then confirms the batch:

```
Send 3 articles to payload queue? [y/n]:
```

After confirmation, optionally prompts to label each selected article.

---

## Search Pool Construction

`\sd` and `\ps` build the search pool from three sources, merged in priority order:

| Priority | Source | Notes |
|----------|--------|-------|
| 1 (primary) | `title_index.jsonl` | All articles ever seen; most comprehensive |
| 2 (fallback) | `scoring_store.jsonl` | Articles with cached embeddings; subset of title_index |
| 3 (supplement) | `last_run_articles.json` | Latest `\r` output; covers very recent articles |

Entries from all three sources are merged by `article_id`. `title_index.jsonl` takes precedence for title and metadata. The combined pool is deduplicated.

---

## Queue Management (`\pd`)

```
\pd          # view current queue contents
\pd clear    # clear the entire queue
```

`\pd` displays the queue as a numbered list with `article_id`, title, source, and queued date.

`\pd clear` prompts for confirmation before emptying `payload_queue.json`.

---

## Data File Write Summary

| Action | Files Written |
|--------|--------------|
| `d` key during labeling | `payload_queue.json` (append + dedup) |
| `\sd <id>` confirmed | `payload_queue.json` (append + dedup) |
| `\ps` batch confirmed | `payload_queue.json` (append + dedup) |
| Optional label after `\sd` / `\ps` | `feedback.jsonl` (append) |
| `\pd clear` confirmed | `payload_queue.json` (cleared) |

---

## What Payload Should Do

fairing only writes stubs to `payload_queue.json`. The payload consumer is responsible for:

1. **Reading** `payload_queue.json` and extracting `article_id` + `url`.
2. **Deduplicating** by `article_id` against its own processed history.
3. **Fetching** full article content (via Firecrawl, requests, or its own method).
4. **Managing its own state** — fairing does not track which queue entries have been consumed.
5. **Not modifying** `payload_queue.json` — use `\pd clear` from within fairing to reset the queue.

---

## Dynamic Lookback

fairing uses dynamic lookback to avoid missing articles when `\r` runs are delayed.

```python
effective_window = max(LOOKBACK_MIN_HOURS, hours_since_last_run)
LOOKBACK_MIN_HOURS = 25   # minimum: always cover more than one calendar day
```

On first run (no `last_run_time` recorded), fairing uses `2026-03-20` as the epoch — articles published after this date are eligible.

This means:
- Normal daily run: window = 25 hours (covers any scheduling jitter).
- Skipped a day: window = 49+ hours (catches up automatically).
- Payload consumers should expect articles from variable time ranges.
