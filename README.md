# fairing

> 中文文档：[README_zh.md](README_zh.md)

**ruoyi_talk** · v1.0.0 · MIT

fairing is a personal RSS digest tool with an active-learning relevance classifier. It pulls articles from configured feeds, scores them against your taste, writes an Obsidian vault note and an optional NotebookLM source file, and sends an email summary. You label a small daily sample; once you accumulate enough feedback the classifier trains itself and starts ranking articles by predicted personal relevance. *Wraps the noise, delivers the signal.*

---

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/TRAINING.md](docs/TRAINING.md) | ML pipeline: embeddings, logistic regression, decay weights |
| [docs/LABELING.md](docs/LABELING.md) | Three-tier labeling system: `\rate`, `\rate --ext`, `\lb` |
| [docs/BACKUP.md](docs/BACKUP.md) | Backup and restore reference |
| [docs/PAYLOAD.md](docs/PAYLOAD.md) | Payload queue integration: `\ps`, `\sd`, `\pd` |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Full operations manual: all commands, troubleshooting |
| [docs/FREE_READER_PILOT.md](docs/FREE_READER_PILOT.md) | Local Miniflux/FreshRSS comparison with public AI feeds |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/JiekerTime/fairing.git
cd fairing
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
# Local output; the free reader pilot needs no SMTP or model API key.
NEWS_DIR=~/Documents/fairing-news
DATA_DIR=~/Documents/fairing-data

# Run behavior defaults
# RUN_CHINESE=false
# RUN_NO_MAIL=true
# RUN_MD=true
# RUN_NOTEBOOK=true
# MAIL_SPLIT_N=2
```

### 3. Configure sources

```bash
cp config/sources.local.yaml.example config/sources.local.yaml
# Add private feeds to sources.local.yaml
```

### 4. Launch

```bash
python main.py
```

Inside the shell: `\r` to run the first digest. `\rate` is optional feedback
and never blocks the next digest.

For cron / non-interactive use:

```bash
python main.py run [--chinese] [--no-mail] [--no-md] [--no-notebook]
```

For a free local reader trial, export the reviewed 15 public AI feeds and
import the OPML into self-hosted Miniflux or FreshRSS. This command never reads
`sources.local.yaml` or exports private feeds:

```bash
python -m fairing.opml --output /tmp/fairing-ai-pilot.opml
```

---

## Shell Commands

| Shortcut | Command | Params | Description |
|----------|---------|--------|-------------|
| `\r` | `run` | `[--no-mail] [--chinese]` | Full pipeline: RSS → embed → score → write digest → email → backup |
| `\rate` | `rate` | `[--ext]` | Optional labeling; `--ext` extends to all unlabeled |
| `\lb` | `label_browser` | `[keywords]` | Browse and edit labeled articles by keyword search |
| `\ms` | `model_status` | | Classifier status, training history, signal words |
| `\slr` | `suspect` | | Review labels where model disagrees (>60% divergence) |
| `\re` | `resend` | | Rebuild today's article list and force-send email |
| `\dl` | `remd` | | Rebuild digest file without email |
| `\t` | `toggle` | `<N>` | Enable or disable RSS source by index |
| `\c` | `config` | | Sources with 7-day counts, label quality, last-seen time |
| `\e` | `env` | `[set KEY VALUE]` | View or update `.env` variables |
| `\l` | `log` | | Run history with per-source breakdown |
| `\bk` | `backup` | | Manual backup trigger |
| `\rs` | `restore` | | Restore from backup with diff + confirmation |
| `\pd` | `payload_queue` | `[clear]` | View or clear payload queue |
| `\ps` | `payload_search` | `[keywords]` | Browse all articles or filter by keyword; add to payload queue |
| `\sd` | `send_by_id` | `<article_id>` | Add specific article to payload queue by ID |
| `\fb` | `fb` | `<article_id>` | Label a specific article (+/-) |
| `\li` | `list_index` | | List recent entries in title_index.jsonl |
| `\?` `\h` | `shortcuts` | | Show command reference |
| `\q` | `quit` | | Exit |

---

## Labeling Keys

| Key | Action |
|-----|--------|
| `+` | Mark as relevant |
| `-` | Mark as not interested |
| `n` | Skip (no label recorded) |
| `o` | Open URL in browser |
| `r` | Deep-read (fetch full text, open in `$EDITOR`) |
| `d` | Add to payload queue |
| `p` | Go back to previous article |
| `s` | Save progress and exit |

---

## Multi-Device Sync

Set `DATA_DIR` to a cloud-synced folder (OneDrive, iCloud Drive, Dropbox):

```bash
DATA_DIR=~/OneDrive/fairing
```

All runtime data files — including `feedback.jsonl`, `scoring_store.jsonl`, and the trained model — are written to `DATA_DIR`. Two-device workflow:

1. **Device A** runs `\r` and `\rate`. Data syncs to cloud.
2. **Device B** runs `\dl` to rebuild local files from synced data. No RSS fetch, no email.
3. Either device can run `\rate` — the feedback file syncs and both share the same model.

`BACKUP_DIR` is a separate local path; set it to a cloud path for cross-device backup coverage.

---

## Configuration

### Essential `.env` variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SMTP_USER` | Email only | — | 163 SMTP sender address |
| `SMTP_PASSWORD` | Email only | — | 163 app password |
| `MAIL_TO` | Email only | — | Recipient email |
| `LLM_GATEWAY_URL` | No | `http://127.0.0.1:8525` | Local LLM Gateway address for Chinese translation |
| `LLM_GATEWAY_API_KEY` | Chinese translation | — | Dedicated `fairing` client key; never a model-provider key |
| `DATA_DIR` | No | project root | All runtime data files |
| `BACKUP_DIR` | No | `~/Documents/fairing/data_bak` | Backup destination |
| `NEWS_DIR` | No | `~/Documents/fairing-news` | Root output directory; daily digests → `NEWS_DIR/YYYY-WXX/`; NotebookLM → `NEWS_DIR/notebooklm/` |
| `SEARCH_GATEWAY_URL` / `SG_URL` | No | — | Preferred full-text fetch gateway for configured sources |
| `SEARCH_GATEWAY_API_KEY` | No | — | API key for Search Gateway when required |
| `TRANSLATOR` | No | `gateway` | Chinese translation through LLM Gateway |
| `MAIL_SPLIT_N` | No | _(off)_ | Split digest email into N parts |
| `TOP_N` | No | `20` | Articles shown in full detail in email |
| `RUN_MD` | No | `true` | Permanent disable Obsidian: set `false` |
| `RUN_NOTEBOOK` | No | `true` | Permanent disable NotebookLM: set `false` |
| `RUN_CHINESE` | No | `false` | Always send Chinese email: set `true` |
| `RUN_NO_MAIL` | No | `false` | Never send email: set `true` |

翻译只处理 `config/sources.yaml` 中的公开来源；`sources.local.yaml` 的本地来源及无法确认来源归属的旧缓存保留英文，不会以 `public` 身份送入 Gateway。Gateway 密钥只写入运行时私有配置。

### sources.yaml fields

| Field | Default | Description |
|-------|---------|-------------|
| `name` | — | Display name in output and labeling UI |
| `url` | — | RSS/Atom feed URL |
| `category` | `General` | Grouping in Obsidian notes and email |
| `tags` | `[]` | Semantic labels used by subscriber fan-out |
| `firecrawl_fulltext` | `false` | Historical field name: enrich before embedding through Search Gateway, then local HTTP; hosted scraping is disabled |

Lookback hours are now dynamic (see [OPERATIONS.md](docs/OPERATIONS.md) — Dynamic Lookback). The `lookback_hours` field is no longer used per-source.

Private feeds go in `config/sources.local.yaml` (gitignored). Use `config/sources.local.yaml.example` as a template.

---

## License

MIT © [JiekerTime (若呓)](mailto:zhangjunjie@apache.org)

GitHub: https://github.com/JiekerTime/fairing
