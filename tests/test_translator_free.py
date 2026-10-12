"""Free-only translation must never select a hosted provider."""

import os
import unittest
from unittest.mock import patch

from fairing import translator


class FreeTranslatorTests(unittest.TestCase):
    def test_old_hosted_backend_name_falls_back_without_call(self):
        article = {"source": "OpenAI", "category": "AI", "title": "New model", "excerpt": "News"}
        with patch.dict(os.environ, {"TRANSLATOR": "gemini", "OLLAMA_MODEL": "local-model"}), \
             patch.object(translator, "_call_ollama") as call:
            result = translator.translate([article])
        self.assertEqual(result[0]["title_zh"], "New model")
        call.assert_not_called()

    def test_nonlocal_ollama_address_is_rejected(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "https://api.example.com"}):
            with self.assertRaises(ValueError):
                translator._ollama_endpoint()


if __name__ == "__main__":
    unittest.main()
