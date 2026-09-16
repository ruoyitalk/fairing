from __future__ import annotations

import os
from pathlib import Path

import pytest

from fairing.runtime_probe import verify_run_lock


def test_verify_run_lock_creates_writable_regular_file(tmp_path: Path) -> None:
    lock = verify_run_lock(tmp_path)

    assert lock == tmp_path / "fairing_run.lock"
    assert lock.is_file()
    assert not lock.is_symlink()
    with lock.open("a", encoding="utf-8"):
        pass


def test_verify_run_lock_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do not follow\n", encoding="utf-8")
    (tmp_path / "fairing_run.lock").symlink_to(target)

    with pytest.raises(RuntimeError, match="run lock is not writable"):
        verify_run_lock(tmp_path)


def test_verify_run_lock_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "fairing_run.lock").mkdir()

    with pytest.raises(RuntimeError, match="run lock is not writable"):
        verify_run_lock(tmp_path)


def test_verify_run_lock_uses_configured_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", os.fspath(tmp_path))

    assert verify_run_lock() == tmp_path / "fairing_run.lock"
