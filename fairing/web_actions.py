"""Shared write contracts for Fairing's authenticated web UI."""

from __future__ import annotations

from .state import today_beijing
from .trainer import load_feedback, save_feedback


def active_feed_errors(errors: dict) -> list[tuple[str, int]]:
    """Return only sources with a positive consecutive-failure count."""
    active = []
    for source, info in errors.items():
        consecutive = (
            info.get("consecutive_failures", info.get("consecutive", 0))
            if isinstance(info, dict)
            else 0
        )
        if isinstance(consecutive, int) and consecutive > 0:
            active.append((source, consecutive))
    return active


def record_feedback(article: dict, label: int, *, reason: str = "") -> dict:
    """Persist one web label using the same schema as the CLI trainer."""
    if label not in (1, -1):
        raise ValueError("label must be 1 or -1")
    if reason not in ("", "advertisement") or (reason and label != -1):
        raise ValueError("reason must be advertisement for a negative label")
    record = {
        "url": article.get("url", ""),
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "label": label,
        "label_index": len(load_feedback()),
        "date": today_beijing(),
    }
    if reason:
        record["reason"] = reason
    save_feedback(record)
    return record
