"""Fairing translation uses its dedicated Gateway identity."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fairing import config, translator


class FreeTranslatorTests(unittest.TestCase):
    def test_prompt_keeps_title_separate_from_source_and_category(self):
        items = translator._build_items([{
            "source": "OpenAI News", "category": "AI / Official News",
            "title": "New model", "excerpt": "Released today.",
        }])
        self.assertEqual(items, '[{"title": "New model", "excerpt": "Released today."}]')
        self.assertNotIn("OpenAI News", items)

    def test_gateway_title_echo_is_cleaned(self):
        article = {"source": "OpenAI News", "category": "AI", "title": "New model",
                   "excerpt": "News", "information_class": "public"}
        with patch.dict(os.environ, {"TRANSLATOR": "gateway", "LLM_GATEWAY_API_KEY": "key"}, clear=True), \
             patch.object(translator, "_call_gateway",
                          return_value='[{"t":"OpenAI新闻|AI/新闻|新模型","s":"发布。"}]'):
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "新模型")

    def test_old_hosted_backend_name_falls_back_without_call(self):
        article = {"source": "OpenAI", "category": "AI", "title": "New model", "excerpt": "News",
                   "information_class": "public"}
        with patch.dict(os.environ, {"TRANSLATOR": "gemini", "LLM_GATEWAY_API_KEY": "key"}), \
             patch.object(translator, "_call_gateway") as call:
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "New model")
        call.assert_not_called()

    def test_gateway_sends_only_public_news_with_dedicated_identity(self):
        article = {"source": "OpenAI", "category": "AI", "title": "New model", "excerpt": "News",
                   "information_class": "public"}
        with patch.dict(os.environ, {"TRANSLATOR": "gateway",
                                  "LLM_GATEWAY_URL": "http://127.0.0.1:8525",
                                  "LLM_GATEWAY_API_KEY": "fairing-client-key"}, clear=True), \
             patch("requests.post") as post:
            post.return_value.json.return_value = {
                "content": '[{"t":"新模型","s":"模型发布。值得关注。"}]'}
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "新模型")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://127.0.0.1:8525/chat")
        self.assertEqual(kwargs["headers"], {"X-API-Key": "fairing-client-key"})
        self.assertEqual(kwargs["json"]["information_class"], "public")
        self.assertEqual(kwargs["json"]["caller"], "fairing/translation")
        self.assertNotIn("DEEPSEEK_API_KEY", kwargs["headers"])

    def test_gateway_without_key_keeps_english(self):
        article = {"source": "OpenAI", "category": "AI", "title": "New model", "excerpt": "News",
                   "information_class": "public"}
        with patch.dict(os.environ, {"TRANSLATOR": "gateway"}, clear=True), \
             patch("requests.post") as post:
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "New model")
        post.assert_not_called()

    def test_gateway_refuses_remote_key_destination(self):
        with patch.dict(os.environ, {"LLM_GATEWAY_URL": "https://api.example.com"}):
            with self.assertRaises(ValueError):
                translator._gateway_endpoint()

    def test_partial_translation_falls_back_for_whole_batch(self):
        article = {"source": "OpenAI", "category": "AI", "title": "New model", "excerpt": "News",
                   "information_class": "public"}
        with patch.dict(os.environ, {"TRANSLATOR": "gateway", "LLM_GATEWAY_API_KEY": "key"}, clear=True), \
             patch.object(translator, "_call_gateway", return_value="[]"):
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "New model")

    def test_private_source_never_reaches_gateway(self):
        article = {"source": "Local", "category": "AI", "title": "Internal note",
                   "excerpt": "Private text", "information_class": "ruoyi-private"}
        with patch.dict(os.environ, {"TRANSLATOR": "gateway", "LLM_GATEWAY_API_KEY": "key"}, clear=True), \
             patch.object(translator, "_call_gateway") as call:
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "Internal note")
        call.assert_not_called()

    def test_mixed_batch_excludes_private_text_from_prompt(self):
        articles = [
            {"source": "Local", "category": "AI", "title": "Secret title",
             "excerpt": "Private text", "information_class": "ruoyi-private"},
            {"source": "OpenAI", "category": "AI", "title": "Public news",
             "excerpt": "Release", "information_class": "public"},
        ]
        with patch.dict(os.environ, {"TRANSLATOR": "gateway", "LLM_GATEWAY_API_KEY": "key"}, clear=True), \
             patch.object(translator, "_call_gateway",
                          return_value='[{"t":"公开新闻","s":"发布了。"}]') as call:
            result = translator.translate(articles)
        self.assertEqual(result[0]["title_zh"], "Secret title")
        self.assertEqual(result[1]["title_zh"], "公开新闻")
        self.assertNotIn("Secret title", call.call_args.args[0])
        self.assertNotIn("Private text", call.call_args.args[0])

    def test_source_file_sets_information_class(self):
        with TemporaryDirectory() as directory:
            public = Path(directory) / "sources.yaml"
            local = Path(directory) / "sources.local.yaml"
            public.write_text("rss:\n  - name: Public\n    url: https://example.com/rss\n", encoding="utf-8")
            local.write_text("rss:\n  - name: Local\n    url: https://internal.example/rss\n", encoding="utf-8")
            with patch.object(config, "_PUBLIC_SOURCES", public), \
                 patch.object(config, "_LOCAL_SOURCES", local):
                sources = {s.name: s for s in config.Config().rss_sources}
        self.assertEqual(sources["Public"].information_class, "public")
        self.assertEqual(sources["Local"].information_class, "ruoyi-private")


if __name__ == "__main__":
    unittest.main()
