"""Tests for fairing/backup.py — backup, diff, restore, and hash helpers."""
import json
import pytest
from pathlib import Path


@pytest.fixture()
def patch_backup(tmp_path, monkeypatch):
    """Redirect DATA_FILES and BACKUP_DIR to temp directories."""
    import fairing.backup as b

    # Route all data files into tmp_path via DATA_DIR
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    bak_root = tmp_path / "backups"
    monkeypatch.setattr(b, "backup_dir", lambda: bak_root)

    from fairing.paths import (
        feedback_file, seen_urls_file, scoring_store_file, rate_pending_file,
        title_index_file, payload_queue_file,
    )
    src_feedback     = feedback_file()
    src_seen         = seen_urls_file()
    src_store        = scoring_store_file()
    src_title_index  = title_index_file()
    src_pending      = rate_pending_file()
    src_queue        = payload_queue_file()

    src_feedback.write_text('{"url":"https://a.com","label":1}\n', encoding="utf-8")
    src_seen.write_text('{"2026-03-20":{"urls":[]}}', encoding="utf-8")
    src_store.write_text('{"url":"https://a.com"}\n', encoding="utf-8")
    src_title_index.write_text('{"article_id":"abc","url":"https://a.com"}\n', encoding="utf-8")
    src_pending.write_text('{}', encoding="utf-8")
    src_queue.write_text('[]', encoding="utf-8")

    return {
        "src": [src_feedback, src_seen, src_store, src_title_index, src_pending, src_queue],
        "bak_root": bak_root,
    }


# ── _file_md5 ──────────────────────────────────────────────────────────────────

def test_file_md5_is_deterministic(tmp_path):
    from fairing.backup import _file_md5
    f = tmp_path / "x.txt"
    f.write_bytes(b"hello world")
    assert _file_md5(f) == _file_md5(f)


def test_file_md5_differs_for_different_content(tmp_path):
    from fairing.backup import _file_md5
    a = tmp_path / "a.txt"; a.write_bytes(b"hello")
    b = tmp_path / "b.txt"; b.write_bytes(b"world")
    assert _file_md5(a) != _file_md5(b)


def test_file_md5_returns_none_for_missing(tmp_path):
    from fairing.backup import _file_md5
    assert _file_md5(tmp_path / "nonexistent.txt") is None


# ── run_backup ─────────────────────────────────────────────────────────────────

def test_run_backup_creates_dated_directory(patch_backup):
    from fairing.backup import run_backup
    dest, backed_up = run_backup()
    assert dest.exists()
    assert len(dest.name) == 10        # YYYY-MM-DD
    assert len(backed_up) == 6


def test_run_backup_copies_file_content(patch_backup):
    from fairing.backup import run_backup
    dest, _ = run_backup()
    bak_feedback = dest / "feedback.jsonl"
    assert bak_feedback.exists()
    assert "https://a.com" in bak_feedback.read_text(encoding="utf-8")


def test_run_backup_skips_missing_files(tmp_path, monkeypatch):
    """Files that don't exist should be silently skipped."""
    import fairing.backup as b
    # Point DATA_DIR to an empty dir so all data_files() return non-existent paths
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty_data"))
    monkeypatch.setattr(b, "backup_dir", lambda: tmp_path / "bak")
    _, backed_up = b.run_backup()
    assert backed_up == []


# ── list_backups ───────────────────────────────────────────────────────────────

def test_list_backups_newest_first(patch_backup):
    from fairing.backup import run_backup, list_backups, backup_dir
    bak_root = backup_dir()
    # Manually create two dated directories
    (bak_root / "2026-03-18").mkdir(parents=True)
    (bak_root / "2026-03-20").mkdir(parents=True)
    (bak_root / "2026-03-19").mkdir(parents=True)
    result = list_backups()
    assert result == ["2026-03-20", "2026-03-19", "2026-03-18"]


def test_list_backups_empty_when_no_dir(tmp_path, monkeypatch):
    import fairing.backup as b
    monkeypatch.setattr(b, "backup_dir", lambda: tmp_path / "nope")
    assert b.list_backups() == []


# ── diff_summary ───────────────────────────────────────────────────────────────

def test_diff_summary_identical_files(patch_backup):
    from fairing.backup import run_backup, diff_summary
    dest, _ = run_backup()
    diffs = diff_summary(dest.name)
    for d in diffs:
        if d["current_exists"] and d["backup_exists"]:
            assert d["identical"] is True


def test_diff_summary_detects_change(patch_backup):
    from fairing.backup import run_backup, diff_summary
    dest, _ = run_backup()
    # Modify source feedback after backup
    patch_backup["src"][0].write_text(
        '{"url":"https://a.com","label":1}\n{"url":"https://b.com","label":-1}\n',
        encoding="utf-8",
    )
    diffs = diff_summary(dest.name)
    feedback_diff = next(d for d in diffs if "feedback" in d["name"])
    assert feedback_diff["identical"] is False
    assert feedback_diff["current_lines"] == 2
    assert feedback_diff["backup_lines"]  == 1


# ── restore_backup ─────────────────────────────────────────────────────────────

def test_restore_backup_overwrites_source(patch_backup):
    from fairing.backup import run_backup, restore_backup
    dest, _ = run_backup()
    # Corrupt the source feedback
    patch_backup["src"][0].write_text("corrupted\n", encoding="utf-8")
    restored = restore_backup(dest.name)
    assert "feedback.jsonl" in restored
    content = patch_backup["src"][0].read_text(encoding="utf-8")
    assert "corrupted" not in content
    assert "https://a.com" in content


def test_restore_backup_returns_only_existing_files(patch_backup):
    from fairing.backup import run_backup, restore_backup
    dest, _ = run_backup()
    # Remove one backup file to simulate partial backup (new name has no leading dot)
    bak_pending = dest / "rate_pending.json"
    if bak_pending.exists():
        bak_pending.unlink()
    else:
        (dest / ".rate_pending.json").unlink(missing_ok=True)
    restored = restore_backup(dest.name)
    assert ".rate_pending.json" not in restored


# ── all_identical ──────────────────────────────────────────────────────────────

def test_all_identical_true_after_backup(patch_backup):
    from fairing.backup import run_backup, all_identical
    dest, _ = run_backup()
    assert all_identical(dest.name) is True


def test_all_identical_false_after_modification(patch_backup):
    from fairing.backup import run_backup, all_identical
    dest, _ = run_backup()
    patch_backup["src"][0].write_text("changed\n", encoding="utf-8")
    assert all_identical(dest.name) is False
