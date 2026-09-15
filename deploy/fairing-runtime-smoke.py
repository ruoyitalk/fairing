#!/usr/bin/env python3
"""Fail-fast production probe for Fairing's mounted data and model runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("FAIRING_APP_ROOT", "/app"))

from fairing.config import Config
from fairing.embedder import _get_model
from fairing.export import load_payload_queue
from fairing.paths import feedback_file, title_index_file
from fairing.trainer import load_feedback
from fairing.web_auth import read_secret


def _write_probe(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / name
    probe.write_text("fairing-runtime-smoke\n", encoding="utf-8")
    probe.unlink()


def main() -> None:
    secret_dir = Path(os.environ["FAIRING_SECRET_DIR"])
    for name in (
        "google_client_id",
        "google_client_secret",
        "context_session_secret",
        "context_password",
    ):
        read_secret(secret_dir / name)

    feedback = load_feedback()
    queue = load_payload_queue()
    # Opening the canonical append-only file checks the actual write permission
    # without altering its content.
    with feedback_file().open("a", encoding="utf-8"):
        pass

    _write_probe(feedback_file().parent, ".fairing-data-write-probe")
    _write_probe(Path(os.environ["NEWS_DIR"]), ".fairing-news-write-probe")

    enabled_subscriptions = [item.name for item in Config().subscriptions]
    if enabled_subscriptions:
        raise RuntimeError(
            f"legacy fan-out subscriptions are still enabled: {enabled_subscriptions}"
        )

    model = _get_model()
    dimension = model.get_sentence_embedding_dimension()
    if dimension != 384:
        raise RuntimeError(f"unexpected embedding dimension: {dimension}")

    print(json.dumps({
        "feedback_unique": len(feedback),
        "payload_queue": len(queue),
        "title_index_lines": sum(
            1 for _ in title_index_file().open(encoding="utf-8")
        ),
        "enabled_subscriptions": enabled_subscriptions,
        "embedding_dimension": dimension,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
