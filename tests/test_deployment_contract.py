from pathlib import Path


def test_fairing_runtime_has_no_direct_qdrant_connection():
    deploy = (Path(__file__).parents[1] / "deploy.sh").read_text(encoding="utf-8")

    assert "QDRANT_URL" not in deploy
    assert "http://qdrant:6333" not in deploy
