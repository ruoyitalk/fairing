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
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _file_md5(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

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


def _allowed_backup_names() -> set[str]:
    return {path.name for path in _data_files()}


def backup_dir() -> Path:
    raw = os.environ.get("BACKUP_DIR", "~/Documents/fairing/data_bak")
    return Path(raw).expanduser()


def _ensure_backup_root() -> Path:
    root = backup_dir()
    if root.exists() or root.is_symlink():
        mode = root.lstat().st_mode
        if root.is_symlink() or not stat.S_ISDIR(mode):
            raise RuntimeError(f"Backup root is not a regular directory: {root}")
    else:
        root.mkdir(parents=True)
    return root


def _parse_backup_date(name: str):
    """Return a date only for canonical YYYY-MM-DD snapshot names."""
    try:
        parsed = datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if parsed.isoformat() == name else None


def _validate_snapshot_dir(path: Path) -> None:
    """Reject links, special files, and foreign content in a Fairing snapshot."""
    mode = path.lstat().st_mode
    if path.is_symlink() or not stat.S_ISDIR(mode):
        raise RuntimeError(f"Backup snapshot is not a regular directory: {path}")

    allowed = _allowed_backup_names()
    for child in path.iterdir():
        child_mode = child.lstat().st_mode
        if child.name not in allowed or child.is_symlink() or not stat.S_ISREG(child_mode):
            raise RuntimeError(f"Backup snapshot contains an unsafe entry: {child}")


def _snapshot_path(date_str: str) -> Path:
    if _parse_backup_date(date_str) is None:
        raise ValueError(f"Invalid backup date: {date_str!r}")
    path = backup_dir() / date_str
    if path.exists() or path.is_symlink():
        _validate_snapshot_dir(path)
    return path


def _snapshot_dirs(base: Path, *, strict: bool = False) -> list[tuple[date, Path]]:
    snapshots: list[tuple[date, Path]] = []
    for candidate in base.iterdir():
        parsed = _parse_backup_date(candidate.name)
        if parsed is None:
            continue
        try:
            _validate_snapshot_dir(candidate)
        except (OSError, RuntimeError) as exc:
            if strict:
                raise RuntimeError(f"Unsafe dated backup snapshot: {candidate}") from exc
            logger.warning("Ignoring unsafe backup snapshot %s: %s", candidate, exc)
            continue
        snapshots.append((parsed, candidate))
    return snapshots


def _count_nonempty_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def run_backup() -> tuple[Path, list[str]]:
    """Copy data files into a timestamped subdirectory.

    @return: (dest_path, list_of_backed_up_filenames)
    """
    today = datetime.now(_TZ_BEIJING).strftime("%Y-%m-%d")
    dest = _ensure_backup_root() / today
    if dest.exists() or dest.is_symlink():
        _validate_snapshot_dir(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _validate_snapshot_dir(dest)

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
        [path.name for _, path in _snapshot_dirs(base)],
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
    bak_dir = _snapshot_path(date_str)
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
        current_md5 = _file_md5(src)
        backup_md5 = _file_md5(bak)
        entry["identical"] = current_md5 is not None and current_md5 == backup_md5
        if src.suffix == ".jsonl":
            if src.exists():
                entry["current_lines"] = _count_nonempty_lines(src)
            if bak.exists():
                entry["backup_lines"] = _count_nonempty_lines(bak)
        result.append(entry)
    return result


def all_identical(date_str: str) -> bool:
    """Return True if every existing current file is byte-for-byte identical to its backup."""
    bak_dir = _snapshot_path(date_str)
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
    bak_dir = _snapshot_path(date_str)
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
    cutoff = (datetime.now(_TZ_BEIJING) - timedelta(days=RETAIN_DAYS)).date()
    for snapshot_date, path in sorted(_snapshot_dirs(base, strict=True)):
        if snapshot_date <= cutoff:
            shutil.rmtree(path)
            logger.info("Pruned old backup: %s", path.name)
