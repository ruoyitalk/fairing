"""Repair ownership of legacy Fairing snapshots without touching shared backups."""

import argparse
import os
import stat
from datetime import datetime
from pathlib import Path


ALLOWED_FILES = {
    "feedback.jsonl",
    "seen_urls.json",
    "scoring_store.jsonl",
    "title_index.jsonl",
    "rate_pending.json",
    "payload_queue.json",
}


def _is_backup_date(name: str) -> bool:
    try:
        parsed = datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return False
    return parsed.isoformat() == name


def validated_snapshots(root: Path) -> list[tuple[Path, list[Path]]]:
    root_mode = root.lstat().st_mode
    if root.is_symlink() or not stat.S_ISDIR(root_mode):
        raise RuntimeError(f"backup root is not a regular directory: {root}")

    snapshots: list[tuple[Path, list[Path]]] = []
    for candidate in sorted(root.iterdir()):
        if not _is_backup_date(candidate.name):
            continue
        candidate_mode = candidate.lstat().st_mode
        if candidate.is_symlink() or not stat.S_ISDIR(candidate_mode):
            raise RuntimeError(f"dated Fairing backup is not a regular directory: {candidate}")

        files: list[Path] = []
        for child in candidate.iterdir():
            child_mode = child.lstat().st_mode
            if (
                child.name not in ALLOWED_FILES
                or child.is_symlink()
                or not stat.S_ISREG(child_mode)
            ):
                raise RuntimeError(f"unexpected entry in dated Fairing backup: {child}")
            files.append(child)
        snapshots.append((candidate, files))
    return snapshots


def repair(root: Path, uid: int, gid: int) -> tuple[int, int]:
    snapshots = validated_snapshots(root)
    file_count = 0
    for directory, files in snapshots:
        os.chown(directory, uid, gid, follow_symlinks=False)
        for path in files:
            os.chown(path, uid, gid, follow_symlinks=False)
            file_count += 1
    return len(snapshots), file_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    args = parser.parse_args()
    directories, files = repair(args.root, args.uid, args.gid)
    print(f"validated_and_repaired_snapshots={directories} files={files}")


if __name__ == "__main__":
    main()
