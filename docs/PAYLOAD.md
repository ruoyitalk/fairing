> 中文版：[PAYLOAD_zh.md](PAYLOAD_zh.md)

# fairing — Payload Integration Reference

**Version**: v1.1.0

---

## Architectural Boundary

fairing and payload are two distinct services with a clear division of responsibility:

```
┌─────────────────────────────────────────┐
│  fairing                                │
│                                         │
│  RSS discovery → excerpt enrichment     │
│  → embedding → scoring → labeling       │
│  → email digest → payload_queue.json    │
└────────────────────┬────────────────────┘
                     │  article stubs
                     ▼
┌─────────────────────────────────────────┐
│  payload consumer                       │
│                                         │
│  full-text fetch → reading / archiving  │
│  → post-read judgement                  │
│  → feedback.jsonl (append)              │
└─────────────────────────────────────────┘
```

**fairing is responsible for**: discovering articles, filtering noise, producing embeddings, training the relevance classifier, and delivering a daily digest. It curates *what is worth reading*.

**The payload consumer is responsible for**: fetching full article text, presenting it to the user for deep reading, and deciding what to do with it (archive, annotate, feed into an LLM, etc.).

fairing does not read articles for the user. The `o` key in `\rate` opens the browser — that is the extent of fairing's reading support.

---

## Article ID

```python
article_id = sha256(normalize_url(url))[:16]
```

- 16 hex characters = 64 bits of entropy.
- `normalize_url()` strips tracking parameters and normalises scheme/host, so the same article from different sources maps to the same ID.
- The payload consumer must use `article_id` as the primary deduplication key, not the raw URL.

---

## payload_queue.json Schema

Each entry is a JSON object:

```json
{
  "article_id": "a1b2c3d4e5f6a7b8",
  "url":        "https://...",
  "title":      "...",
  "source":     "HN",
  "queued_date": "2026-03-22"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `article_id` | string | `sha256(normalize_url(url))[:16]` |
| `url` | string | Article URL (original, not normalised) |
| `title` | string | Title at time of queuing |
| `source` | string | RSS source name |
| `queued_date` | string | Beijing date queued (YYYY-MM-DD) |

The file is a JSON array. fairing deduplicates by `article_id` before writing.

---

## Four Ways to Add Articles

### 1. `d` key during `\rate` / `\rate --ext`

During any labeling session, press `d` to send the current article to the queue. Optionally prompts to also label it positive. The card stays on screen; labeling continues uninterrupted.

### 2. `\sd <id>` — Enqueue by ID

```
\sd a1b2c3d4e5f6a7b8
```

(`\sd` → `enqueue`) Looks up the article by `article_id` in the search pool and queues it after confirmation.

### 3. `\ps [keywords]` — Queue Search

```
\ps                   # browse all articles (paginated)
\ps query optimizer   # filter by title keywords
```

(`\ps` → `queue_search`) Presents paginated results. Select entries by number across pages, then confirm batch addition.

### 4. `\im <file.csv>` — Batch Import

```
\im ~/Downloads/articles.csv
```

(`\im` → `import_csv`) Reads a CSV file and processes each row. Supports labeling, queuing, or both in a single operation.

CSV format — two columns, no header required:

```csv
article_id,action
5e07b775ab1f3af6,+q
a1b2c3d4e5f6a7b8,-
deadbeef00000001,q
cafebabe12345678,s
```

| action | meaning |
|--------|---------|
| `+` | label as valuable |
| `-` | label as not interested |
| `q` | add to queue (no label) |
| `+q` | label as valuable AND add to queue |
| `-q` | label as not interested AND add to queue |
| `s` | skip (no operation) |

Lines starting with `#` are treated as comments and ignored.

---

## Queue Management

```
\pd          # view current queue contents
\pd clear    # clear the entire queue
```

(`\pd` → `queue`) `\pd clear` is the only supported way to reset the queue from within fairing. The payload consumer should not modify `payload_queue.json` directly.

---

## What the Payload Consumer Should Do

1. **Poll** `payload_queue.json` on a schedule or on demand.
2. **Deduplicate** by `article_id` against its own processed history.
3. **Fetch** full article text (Firecrawl, Jina, requests, or any other method).
4. **Present** the content to the user for reading.
5. **Clear** the queue via `\pd clear` after consuming, or manage its own consumed-ID list.

---

## Search Pool Construction

`\sd` and `\ps` build their search pool from three sources, merged by `article_id`:

| Priority | Source | Notes |
|----------|--------|-------|
| 1 | `title_index.jsonl` | All articles ever seen |
| 2 | `scoring_store.jsonl` | Articles with cached embeddings |
| 3 | `last_run_articles.json` | Latest `\r` output |

---

## Dynamic Lookback

```python
effective_window = max(25, hours_since_last_run)
```

On first run, fairing uses `2026-03-20` as the epoch. Skipped days are caught up automatically. Payload consumers should expect articles from variable time ranges.