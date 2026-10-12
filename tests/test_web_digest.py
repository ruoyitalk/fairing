import tempfile
import unittest
from pathlib import Path

from fairing.web_digest import latest_digest, parse_digest, plain_markdown


class WebDigestTests(unittest.TestCase):
    def test_chinese_note_hides_old_translator_metadata_prefix(self):
        note = """# 每日资讯 — 2026-10-12
### OpenAI新闻|AI/官方新闻|新模型发布
| Source | OpenAI News |
| URL | [https://example.org/a](https://example.org/a) |
"""
        self.assertEqual(parse_digest(note, chinese=True)[0]["title"], "新模型发布")
        self.assertEqual(parse_digest(note)[0]["title"], "OpenAI新闻|AI/官方新闻|新模型发布")

    def test_feed_text_cannot_embed_an_image(self):
        self.assertEqual(plain_markdown("![pixel](https://example.org/p)"),
                         r"\!\[pixel\]\(https://example\.org/p\)")

    def test_parse_uses_plain_fields_and_rejects_unsafe_links(self):
        note = """# 每日资讯 — 2026-10-12
## OpenAI · AI
### 新发布
| Field | Value |
| Source | OpenAI |
| Published | 2026-10-12 |
| URL | [https://example.org/a](https://example.org/a) |
![cover](https://tracker.example/pixel)
公开摘要。
### 无效链接
| URL | [bad](javascript:alert(1)) |
不可展示。
"""
        self.assertEqual(parse_digest(note), [{
            "title": "新发布", "source": "OpenAI", "category": "AI", "published": "2026-10-12",
            "url": "https://example.org/a", "summary": "公开摘要。",
        }])

    def test_latest_date_prefers_chinese_note(self):
        with tempfile.TemporaryDirectory() as root:
            week = Path(root) / "2026-W42"
            week.mkdir()
            block = "### Title\n| URL | [https://example.org/a](https://example.org/a) |\n"
            (week / "2026-10-11-zh.md").write_text(block)
            (week / "2026-10-12.md").write_text(block)
            (week / "2026-10-12-zh.md").write_text(block)
            result = latest_digest(Path(root))
            self.assertIsNotNone(result)
            self.assertEqual((result[1], result[2]), ("2026-10-12", True))


if __name__ == "__main__":
    unittest.main()
