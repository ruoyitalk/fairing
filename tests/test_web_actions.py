from pathlib import Path

import pytest


def test_active_feed_errors_ignores_recovered_sources():
    from fairing.web_actions import active_feed_errors

    assert active_feed_errors({
        "healthy": {"consecutive": 0},
        "broken": {"consecutive": 3},
        "legacy": "invalid",
    }) == [("broken", 3)]


def test_record_feedback_uses_cli_training_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.trainer import load_feedback
    from fairing.web_actions import record_feedback

    saved = record_feedback(
        {"url": "https://example.com/a", "title": "A", "source": "Source"},
        1,
    )
    assert saved["label"] == 1
    assert saved["label_index"] == 0
    assert load_feedback()[0] == saved


def test_record_feedback_rejects_web_only_string_labels(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fairing.web_actions import record_feedback

    with pytest.raises(ValueError, match="label must be 1 or -1"):
        record_feedback({"url": "https://example.com/a"}, "+")


def test_streamlit_uses_shared_feedback_and_queue_contracts():
    source = (Path(__file__).parents[1] / "streamlit_app.py").read_text(encoding="utf-8")
    assert "record_feedback(article, label)" in source
    assert "add_to_payload_queue(article)" in source
    assert "remove_from_payload_queue" in source
    assert "payload_queue_file().write_text" not in source
    assert "_append_jsonl" not in source
