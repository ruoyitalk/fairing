from dataclasses import dataclass

from fairing.recover_chinese_note import prepare_articles


@dataclass
class Source:
    name: str
    category: str
    tags: list[str]
    information_class: str


def test_recovery_only_grants_public_to_unambiguous_source_names():
    rows = [
        {"url": "https://example.org/a", "title": "A", "summary": "A",
         "source": "Public", "published": "2026-10-12"},
        {"url": "https://example.org/b", "title": "B", "summary": "B",
         "source": "Private", "published": "2026-10-12"},
        {"url": "https://example.org/c", "title": "C", "summary": "C",
         "source": "Unknown", "published": "2026-10-12"},
        {"url": "https://example.org/d", "title": "D", "summary": "D",
         "source": "Shared Name", "published": "2026-10-12"},
    ]
    sources = [
        Source("Public", "AI", ["ai"], "public"),
        Source("Private", "Work", [], "ruoyi-private"),
        Source("Shared Name", "AI", ["ai"], "public"),
        Source("Shared Name", "Work", [], "ruoyi-private"),
    ]
    articles = prepare_articles(rows, sources)
    assert [a["information_class"] for a in articles] == [
        "public", "ruoyi-private", "ruoyi-private", "ruoyi-private"
    ]
