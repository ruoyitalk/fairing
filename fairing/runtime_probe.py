"""Small runtime probes shared by health, deploy, and scheduled execution."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def verify_run_lock(data_dir: Path | None = None) -> Path:
    """Prove the non-root runtime can safely open the canonical run lock.

    A stale lock file created by an older root container must not leave the web
    UI healthy while every scheduled ingestion fails.  O_NOFOLLOW keeps this
    probe from following a substituted symlink when the platform supports it.
    """

    root = data_dir or Path(os.environ.get("DATA_DIR", "/data/fairing"))
    if not root.is_dir():
        raise RuntimeError(f"Fairing data directory is missing: {root}")

    lock = root / "fairing_run.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock, flags, 0o644)
    except OSError as exc:
        raise RuntimeError(f"Fairing run lock is not writable: {lock}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"Fairing run lock is not a regular file: {lock}")
    finally:
        os.close(descriptor)
    return lock


def main() -> None:
    lock = verify_run_lock()
    print(json.dumps({"status": "ok", "run_lock": str(lock)}, sort_keys=True))


if __name__ == "__main__":
    main()
