from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from fairing.opml import build_public_opml, is_public_feed_url


ROOT = Path(__file__).parents[1]


class PublicOpmlTests(unittest.TestCase):
    def test_current_selection_exports_fifteen_public_feeds(self) -> None:
        document = build_public_opml(
            ROOT / "config" / "sources.yaml",
            ROOT / "config" / "ai-pilot-sources.yaml",
        )
        root = ET.fromstring(document)
        feeds = root.findall("./body/outline/outline")
        self.assertEqual(len(feeds), 15)
        self.assertTrue(all(feed.attrib["xmlUrl"].startswith("https://") for feed in feeds))
        self.assertIn("https://openai.com/news/rss.xml", [feed.attrib["xmlUrl"] for feed in feeds])

    def test_selection_cannot_leak_a_private_source(self) -> None:
        with TemporaryDirectory() as directory:
            selection = Path(directory) / "selection.yaml"
            selection.write_text("sources:\n  - Private Source\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not in the public catalog"):
                build_public_opml(ROOT / "config" / "sources.yaml", selection)

    def test_private_catalog_cannot_be_exported(self) -> None:
        with TemporaryDirectory() as directory:
            private = Path(directory) / "sources.local.yaml"
            private.write_text("rss:\n  - name: Private Source\n    url: https://private.example/feed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tracked public source catalog"):
                build_public_opml(private, ROOT / "config" / "ai-pilot-sources.yaml")

    def test_feed_url_rejects_tokens_and_local_addresses(self) -> None:
        self.assertFalse(is_public_feed_url("https://example.com/feed?token=private"))
        self.assertFalse(is_public_feed_url("https://127.0.0.1/feed"))
        self.assertTrue(is_public_feed_url("https://openai.com/news/rss.xml"))


if __name__ == "__main__":
    unittest.main()
