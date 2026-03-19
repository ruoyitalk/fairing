# fairing

A daily feed aggregator for personal knowledge workflows. Pulls from RSS feeds,
newsletters, and arXiv papers, then writes a structured Markdown digest to an
Obsidian vault and sends an email summary.

Part of the [ruoyi_talk](https://github.com/ruoyitalk) project.

**Author:** ruoyi &lt;zhangjunjie@apache.org&gt;
**License:** [MIT](LICENSE)

---

## How it works

Each run goes through four stages:

1. **Collect** — fetch all configured RSS feeds
2. **Deduplicate** — filter articles already written in previous days
3. **Write** — generate an Obsidian note and a NotebookLM plain-text file
   (when `NOTEBOOKLM_DIR` is set); optionally a Chinese-translated note
4. **Notify** — send an HTML email digest via SMTP; warns and skips if content
   is identical to the previous send (MD5 check)

Output:
- `OBSIDIAN_DIR/YYYY-WXX/YYYY-MM-DD.md`
- `NOTEBOOKLM_DIR/YYYY-WXX/YYYY-MM-DD.md` (if configured)

Running multiple times on the same day appends new articles to the existing file
rather than overwriting it.

---

## Requirements

- Python 3.11 or later
- macOS, Linux, or Windows (PowerShell 5+)

---

## Setup

```bash
git clone git@github.com:ruoyitalk/fairing.git
cd fairing
cp .env.example .env
cp config/sources.local.yaml.example config/sources.local.yaml
```

Edit `.env` with your credentials. All fields are optional — omitting a key
disables the corresponding feature.

---

## Running

**macOS / Linux**

```bash
bash run.sh       # start interactive shell
```

**Windows**

```powershell
.\run.ps1         # PowerShell (recommended)
run.bat           # cmd wrapper
```

First-time PowerShell setup (once per machine):

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Non-interactive** (cron, scripts):

```bash
bash run.sh run
bash run.sh run --chinese
bash run.sh run --all
```

---

## Shell commands

```
run                    Obsidian note + NotebookLM (if configured) + email
run --md               Obsidian only, no NotebookLM
run --no-mail          skip email notification
run --chinese          also write Chinese-translated note (GEMINI_API_KEY required)
run --fulltext         fetch full article body via Firecrawl
run --all              --chinese + --fulltext

config                 show all feed sources in a table
env                    show .env values (sensitive fields masked)
env set KEY VALUE      update a single key in .env at runtime
log                    show per-day run history and email status
help                   list all commands
exit / \q              quit
```

## Slash shortcuts

```
\r     run                     \rm    run --md
\rq    run --no-mail           \rc    run --chinese
\rf    run --fulltext          \ra    run --all
\c     config                  \e     env
\l     log                     \h     shortcuts (this list)
\q     quit
```

---

## Configuration

### Environment (.env)

| Variable | Description |
|---|---|
| `SMTP_HOST` | SMTP server, e.g. `smtp.163.com` |
| `SMTP_PORT` | SMTP port, default `465` |
| `SMTP_USER` | Sender address |
| `SMTP_PASSWORD` | SMTP password or authorization code |
| `MAIL_TO` | Recipient address |
| `FIRECRAWL_API_KEY` | Enables full-text enrichment for blog sources. Free tier: 500 credits/month at [firecrawl.dev](https://firecrawl.dev) |
| `GEMINI_API_KEY` | Enables `--chinese` output. Free tier at [aistudio.google.com](https://aistudio.google.com) |
| `OBSIDIAN_DIR` | Vault directory. Default: `~/Documents/ruoyinote` |
| `NOTEBOOKLM_DIR` | Plain-text output directory. Leave empty to disable |

### Feed sources

**`config/sources.yaml`** — public feeds, safe to commit.

```yaml
rss:
  - name: ClickHouse Blog
    url: https://clickhouse.com/rss.xml
    category: Database
    lookback_hours: 24        # omit to use the default (24)
    firecrawl_fulltext: true  # fetch full article body; off by default
```

**`config/sources.local.yaml`** — private feeds, gitignored.

Add feeds you do not want in the public repository here. Entries are merged
with `sources.yaml` at runtime.

```yaml
rss:
  - name: McKinsey Newsletter
    url: https://kill-the-newsletter.com/feeds/<id>.xml
    category: Strategy / AI
```

For newsletters delivered only by email, use [Kill the Newsletter](https://kill-the-newsletter.com)
to get an RSS-compatible feed URL.

**Field reference:**

| Field | Default | Description |
|---|---|---|
| `name` | required | Display name |
| `url` | required | RSS or Atom feed URL |
| `category` | `General` | Used for grouping in output |
| `lookback_hours` | `24` | How far back to fetch. 24 suits daily runs; set to `48` only for sources that skip weekends (e.g. arXiv) |
| `firecrawl_fulltext` | `false` | Fetch full article body. Costs Firecrawl credits |

---

## Built-in sources

The following sources are included in `config/sources.yaml` by default.

| Source | Category | Lookback | Full-text |
|---|---|---|---|
| [ClickHouse Blog](https://clickhouse.com/blog) | Database | 24 h | yes |
| [Databricks Blog](https://www.databricks.com/blog) | Data Platform | 24 h | yes |
| [Qdrant Blog](https://qdrant.tech/articles/) | AI / Vector DB | 24 h | — |
| [NVIDIA Developer Blog](https://developer.nvidia.com/blog) | AI / Infrastructure | 24 h | — |
| [Lilian Weng](https://lilianweng.github.io) | AI / ML Research | 24 h | — |
| [Eugene Yan](https://eugeneyan.com) | AI / ML | 24 h | — |
| [Anthropic Engineering](https://www.anthropic.com/engineering) | AI / Engineering | 24 h | — |
| [ByteByteGo](https://blog.bytebytego.com) | Architecture | 24 h | — |
| [arXiv cs.DB/cs.AR/cs.OS](https://arxiv.org) | Research | 48 h | — |
| [Apache Calcite Releases](https://github.com/apache/calcite) | Release | 24 h | — |
| [Trino Releases](https://github.com/trinodb/trino) | Release | 24 h | — |
| [ClickHouse Releases](https://github.com/ClickHouse/ClickHouse) | Release | 24 h | — |
| [HackerNews: query optimizer](https://hnrss.org) | Community | 24 h | — |
| [HackerNews: distributed systems](https://hnrss.org) | Community | 24 h | — |
| [r/dataengineering](https://reddit.com/r/dataengineering) | Community | 24 h | — |
| [CMU Database Group](https://www.youtube.com/@CMUDatabaseGroup) | Research / Video | 24 h | — |
| [FinOps Foundation](https://www.finops.org) | Cloud / FinOps | 24 h | — |
| [benn.substack](https://benn.substack.com) | Data / Strategy | 24 h | — |
| [Software Architecture Weekly](https://softwarearchitectureweekly.substack.com) | Architecture | 24 h | — |
| [The Pragmatic Engineer](https://newsletter.pragmaticengineer.com) | Engineering | 24 h | — |
| [Curious Engineer](https://vivekbansal.substack.com) | Engineering | 24 h | — |

For sites without official RSS, community-maintained feeds are used where available
(e.g. [Olshansk/rss-feeds](https://github.com/Olshansk/rss-feeds) for Anthropic Engineering).

McKinsey: subscribe to their email newsletter and convert via Kill the Newsletter.

---

## State files

Fairing keeps two local files in the project root. Both are gitignored.

**`.seen_urls.json`**

Tracks normalized URLs and titles of all processed articles, keyed by date.

```json
{
  "2026-03-19": {
    "urls":   ["https://normalized-url", ...],
    "titles": ["normalized article title", ...]
  }
}
```

Two deduplication layers are applied on every run:

1. **URL match** — normalized URL (tracking params stripped, trailing slash
   removed, scheme/host lowercased). Catches the same article shared with UTM
   links or minor URL variants.
2. **Title match** — normalized title (lowercase, punctuation removed). Catches
   the same content cross-posted at a different URL across sources.

Any article matching either layer is excluded. Same-day second runs only
process articles that genuinely arrived after the previous run.
Entries older than 30 days are pruned automatically.

To re-process all articles from scratch, delete this file.

**`.digest_hash`**

Stores the MD5 of the last email's article content (url + title + excerpt),
scoped to the current date. Acts as a lightweight safety net: if a run somehow
reaches the mailer with an identical article set (edge case), the email is
skipped. To force a re-send, delete this file.

---

## Project layout

```
fairing/
├── config/
│   ├── sources.yaml                 public feed list
│   ├── sources.local.yaml           private feed list (gitignored)
│   └── sources.local.yaml.example
├── fairing/
│   ├── __init__.py                  version and author metadata
│   ├── config.py                    load and merge config files
│   ├── rss.py                       RSS fetcher with per-feed timeout and retry
│   ├── mckinsey.py                  Firecrawl-based McKinsey scraper
│   ├── translator.py                Gemini Chinese translation
│   ├── writer.py                    Obsidian / NotebookLM / Chinese output
│   ├── mailer.py                    SMTP email digest
│   └── state.py                     cross-day URL deduplication
├── main.py                          CLI entry point (interactive shell)
├── run.sh                           macOS / Linux launcher
├── run.ps1                          Windows PowerShell launcher
├── run.bat                          Windows cmd launcher
└── requirements.txt
```
