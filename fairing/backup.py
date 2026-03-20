"""Backup critical data files to a configurable directory.

Backup structure:
  <BACKUP_DIR>/
    2026-03-20/
      feedback.jsonl
      seen_urls.json
      scoring_store.jsonl
      title_index.jsonl
      rate_pending.json
      payload_queue.json

One directory per day (Beijing time). Same-day runs overwrite the previous
backup for that day. Directories older than RETAIN_DAYS are pruned automatically.

Public API:
  run_backup()      — copy data files into today's timestamped subdirectory
  list_backups()    — return available backup dates sorted newest first
  diff_summary()    — compare current files against a backup snapshot
  restore_backup()  — overwrite current data files from a backup snapshot
  all_identical()   — check whether current files are byte-for-byte equal to a backup

Configuration:
  BACKUP_DIR  env var — default: ~/Documents/fairing/data_bak
"""
import hashlib
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _file_md5(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()

logger = logging.getLogger(__name__)

_TZ_BEIJING = timezone(timedelta(hours=8))
RETAIN_DAYS = 7

from .paths import (
    feedback_file as _ff, seen_urls_file as _su,
    scoring_store_file as _ss, rate_pending_file as _rp,
    payload_queue_file as _pq, title_index_file as _ti,
)

def _data_files() -> list[Path]:
    return [_ff(), _su(), _ss(), _ti(), _rp(), _pq()]


def backup_dir() -> Path:
    raw = os.environ.get("BACKUP_DIR", "~/Documents/fairing/data_bak")
    return Path(raw).expanduser()


def run_backup() -> tuple[Path, list[str]]:
    """Copy data files into a timestamped subdirectory.

    @return: (dest_path, list_of_backed_up_filenames)
    """
    today = datetime.now(_TZ_BEIJING).strftime("%Y-%m-%d")
    dest  = backup_dir() / today
    dest.mkdir(parents=True, exist_ok=True)

    backed_up: list[str] = []
    for src in _data_files():
        if src.exists():
            shutil.copy2(src, dest / src.name)
            backed_up.append(src.name)

    if backed_up:
        logger.info("Backup → %s  (%d files: %s)", dest, len(backed_up), ", ".join(backed_up))
    else:
        logger.warning("Backup: no data files found to back up")

    _prune(backup_dir())
    return dest, backed_up


def list_backups() -> list[str]:
    """Return available backup dates sorted newest first."""
    base = backup_dir()
    if not base.exists():
        return []
    return sorted(
        [d.name for d in base.iterdir() if d.is_dir() and len(d.name) == 10],
        reverse=True,
    )


def diff_summary(date_str: str) -> list[dict]:
    """Compare current files against a backup snapshot.

    @return: list of dicts with keys:
      name, current_exists, backup_exists,
      current_lines, backup_lines  (for .jsonl),
      current_size,  backup_size   (bytes),
      identical                    (True when both files exist and have the same MD5)
    """
    bak_dir = backup_dir() / date_str
    result  = []
    for src in _data_files():
        bak   = bak_dir / src.name
        entry = {
            "name":            src.name,
            "current_exists":  src.exists(),
            "backup_exists":   bak.exists(),
            "current_lines":   None,
            "backup_lines":    None,
            "current_size":    src.stat().st_size  if src.exists() else 0,
            "backup_size":     bak.stat().st_size  if bak.exists() else 0,
        }
        entry["identical"] = (_file_md5(src) == _file_md5(bak) and
                              _file_md5(src) is not None)
        if src.suffix == ".jsonl":
            if src.exists():
                entry["current_lines"] = sum(
                    1 for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()
                )
            if bak.exists():
                entry["backup_lines"] = sum(
                    1 for ln in bak.read_text(encoding="utf-8").splitlines() if ln.strip()
                )
        result.append(entry)
    return result


def all_identical(date_str: str) -> bool:
    """Return True if every existing current file is byte-for-byte identical to its backup."""
    bak_dir = backup_dir() / date_str
    for src in _data_files():
        bak = bak_dir / src.name
        if src.exists() and bak.exists():
            if _file_md5(src) != _file_md5(bak):
                return False
        elif src.exists() != bak.exists():
            return False   # one exists, the other doesn't
    return True


def restore_backup(date_str: str) -> list[str]:
    """Overwrite current data files with the specified backup snapshot.

    @return: list of restored filenames
    """
    bak_dir  = backup_dir() / date_str
    restored = []
    for src in _data_files():
        bak = bak_dir / src.name
        if bak.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak, src)
            restored.append(src.name)
            logger.info("Restored %s from %s", src.name, date_str)
    return restored


def _prune(base: Path) -> None:
    if not base.exists():
        return
    cutoff = (datetime.now(_TZ_BEIJING) - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%d")
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name <= cutoff:
            shutil.rmtree(d)
            logger.info("Pruned old backup: %s", d.name)
