"""Centralised data-file path routing for fairing.

All mutable data files are stored under DATA_DIR, which defaults to the
project root. Setting DATA_DIR in .env (e.g. to an OneDrive / Dropbox /
iCloud Drive folder) enables seamless multi-device sync without any
platform-specific code.

.env example:
    DATA_DIR=~/OneDrive/fairing

File layout under DATA_DIR:
    data/feedback.jsonl          — training labels (git-tracked in project)
    seen_urls.json               — dedup state (30-day rolling window)
    scoring_store.jsonl          — embedding cache
    title_index.jsonl            — lightweight title/url index (no embeddings)
    rate_pending.json            — labeling progress
    payload_queue.json           — handoff queue to downstream payload stage
    feed_errors.json             — consecutive feed failure tracking
    last_run_articles.json       — score-sorted list from last \\r run
    last_run_time                — timestamp of last successful run
    digest_hash                  — MD5 of last email send
    personal_model.pkl           — deployed LogisticRegressionCV
    personal_scaler.pkl          — paired StandardScaler
"""
import os
from pathlib import Path

# Load .env early so DATA_DIR is available before any path is resolved.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

_PROJECT_ROOT = Path(__file__).parent.parent


def data_dir() -> Path:
    """Return the configured data directory.

    Uses DATA_DIR env var when set; otherwise falls back to the project root.
    The directory is created on first access if it does not exist.
    """
    raw = os.environ.get("DATA_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return _PROJECT_ROOT


def data_path(*parts: str) -> Path:
    """Return an absolute path for a data file under data_dir().

    Intermediate directories are created automatically.

    @param parts: relative path components (e.g. "data", "feedback.jsonl")
    @return: absolute Path
    """
    p = data_dir().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Named paths (import these instead of building paths manually) ──────────────

def feedback_file() -> Path:
    return data_path("data", "feedback.jsonl")

def seen_urls_file() -> Path:
    return data_path("seen_urls.json")

def scoring_store_file() -> Path:
    return data_path("scoring_store.jsonl")

def rate_pending_file() -> Path:
    return data_path("rate_pending.json")

def last_run_file() -> Path:
    return data_path("last_run_articles.json")

def digest_hash_file() -> Path:
    return data_path("digest_hash")

def model_file() -> Path:
    return data_path("personal_model.pkl")

def scaler_file() -> Path:
    return data_path("personal_scaler.pkl")

def title_index_file() -> Path:
    return data_path("title_index.jsonl")

def last_run_time_file() -> Path:
    return data_path("last_run_time")

def payload_queue_file() -> Path:
    return data_path("payload_queue.json")

def feed_errors_file() -> Path:
    return data_path("feed_errors.json")

def training_log_file() -> Path:
    return data_path("training_log.jsonl")
