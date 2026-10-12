import unittest

from fairing.web_articles import latest_articles


class LatestArticlesTests(unittest.TestCase):
    def test_newest_valid_article_wins_and_filters_are_plain_text(self):
        rows = [
            {"url": "https://news.example/one", "title": "Old model", "source": "A", "date": "2026-01-01"},
            {"url": "javascript:alert(1)", "title": "Bad", "source": "A", "date": "2026-12-01"},
            {"url": "https://[broken", "title": "Bad host", "source": "A", "date": "2026-11-01"},
            {"url": "https://news.example/two", "title": "New model", "source": "B", "date": "2026-02-01"},
            {"url": "https://news.example/one", "title": "Updated model", "source": "A", "date": "2026-03-01"},
        ]
        self.assertEqual(
            [row["title"] for row in latest_articles(rows)],
            ["Updated model", "New model"],
        )
        self.assertEqual(
            [row["title"] for row in latest_articles(rows, query="MODEL", source="B")],
            ["New model"],
        )


if __name__ == "__main__":
    unittest.main()
