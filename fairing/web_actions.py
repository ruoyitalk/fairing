"""Shared write contracts for Fairing's authenticated web UI."""

from __future__ import annotations

from .state import today_beijing
from .trainer import load_feedback, save_feedback


def record_feedback(article: dict, label: int) -> dict:
    """Persist one web label using the same schema as the CLI trainer."""
    if label not in (1, -1):
        raise ValueError("label must be 1 or -1")
    record = {
        "url": article.get("url", ""),
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "label": label,
        "label_index": len(load_feedback()),
        "date": today_beijing(),
    }
    save_feedback(record)
    return record
