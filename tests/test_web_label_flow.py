"""The reader's ad action should write feedback and update the visible list."""

import json
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from fairing.export import article_id_for
from fairing.trainer import load_feedback


def test_reader_marks_ad_then_restores_it_from_article_library(tmp_path, monkeypatch):
    url = "https://example.org/technical-post"
    data_dir = tmp_path / "data"
    news_dir = tmp_path / "news"
    week = news_dir / "2026-W42"
    week.mkdir(parents=True)
    (week / "2026-10-12-zh.md").write_text(
        "# 每日资讯 — 2026-10-12\n\n"
        "## Test · AI\n\n"
        "### 新技术文章\n\n"
        "| Source | Test |\n"
        "| Published | 2026-10-12 |\n"
        f"| URL | [{url}]({url}) |\n",
        encoding="utf-8",
    )
    data_dir.mkdir()
    (data_dir / "title_index.jsonl").write_text(json.dumps({
        "url": url, "title": "New technical article", "source": "Test",
        "date": "2026-10-12",
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEWS_DIR", str(news_dir))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app.py"))
    app.session_state["fairing_homeserver_authenticated"] = True
    with patch("fairing.web_auth.read_secret", return_value="test-secret"):
        app.run()
        assert not app.exception
        ad_key = f"ad_digest_{article_id_for(url)}"
        next(button for button in app.button if button.key == ad_key).click().run()
        assert not app.exception
        assert load_feedback()[0]["label"] == -1
        assert load_feedback()[0]["reason"] == "advertisement"
        assert not any(button.key == ad_key for button in app.button)

        next(radio for radio in app.radio if radio.label == "阅读范围").set_value("文章库").run()
        next(checkbox for checkbox in app.checkbox if checkbox.label == "显示已过滤的内容").check().run()
        useful_key = f"useful_library_{article_id_for(url)}"
        next(button for button in app.button if button.key == useful_key).click().run()
        assert not app.exception
        assert load_feedback()[0]["label"] == 1
