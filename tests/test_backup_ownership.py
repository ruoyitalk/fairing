"""Tests for the narrowly scoped legacy-backup ownership repair."""

from unittest.mock import patch

import pytest

import fairing.backup_ownership as MODULE


def test_repair_only_touches_canonical_fairing_snapshots(tmp_path):
    snapshot = tmp_path / "2026-09-15"
    snapshot.mkdir()
    (snapshot / "feedback.jsonl").write_text("{}\n", encoding="utf-8")
    unrelated = tmp_path / "payload"
    unrelated.mkdir()
    (unrelated / "keep").write_text("unchanged", encoding="utf-8")

    with patch.object(MODULE.os, "chown") as chown:
        directories, files = MODULE.repair(tmp_path, 1000, 1000)

    assert (directories, files) == (1, 1)
    assert (unrelated / "keep").read_text(encoding="utf-8") == "unchanged"
    touched = {call.args[0] for call in chown.call_args_list}
    assert touched == {snapshot, snapshot / "feedback.jsonl"}


@pytest.mark.parametrize("unsafe_name", ["foreign.txt", "scoring_store.jsonl.link"])
def test_repair_rejects_foreign_snapshot_content(tmp_path, unsafe_name):
    snapshot = tmp_path / "2026-09-15"
    snapshot.mkdir()
    (snapshot / unsafe_name).write_text("unsafe", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unexpected entry"):
        MODULE.repair(tmp_path, 1000, 1000)


def test_repair_rejects_dated_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "2026-09-15").symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match="not a regular directory"):
        MODULE.repair(tmp_path, 1000, 1000)
