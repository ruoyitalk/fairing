"""Recover a missing Chinese daily note from the already-written English note.

This is for a run interrupted after English output. It never sends mail,
changes dedup state, or sends unknown/private sources to the LLM Gateway.
"""

from datetime import date
from pathlib import Path

from .state import today_beijing
from .translator import translate
from .web_digest import parse_digest
from .writer import select_digest_articles, write_chinese


def prepare_articles(rows: list[dict], sources: list) -> list[dict]:
    public = {source.name: source for source in sources
              if source.information_class == "public"}
    ambiguous = {source.name for source in sources
                 if source.information_class != "public"}
    for name in ambiguous:
        public.pop(name, None)
    prepared = []
    for row in rows:
        source = public.get(row["source"])
        prepared.append({
            "url": row["url"],
            "title": row["title"],
            "excerpt": row["summary"],
            "source": row["source"],
            "category": source.category if source else "Unknown",
            "tags": source.tags if source else [],
            "published": row["published"],
            "information_class": "public" if source else "ruoyi-private",
        })
    return prepared


def main() -> None:
    from .config import Config

    cfg = Config()
    today = date.fromisoformat(today_beijing())
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    english = Path(cfg.news_dir) / week / f"{today.isoformat()}.md"
    if not english.is_file():
        raise SystemExit("No English daily note to recover")
    rows = parse_digest(english.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit("English daily note contains no readable article blocks")
    selected = select_digest_articles(prepare_articles(rows, cfg.rss_sources))
    translated = translate(selected)
    public_count = sum(a["information_class"] == "public" for a in translated)
    translated_count = sum(
        a["information_class"] == "public" and a.get("title_zh") != a["title"]
        for a in translated
    )
    if public_count and not translated_count:
        raise SystemExit("Gateway translated no public article titles")
    path, written = write_chinese(translated, cfg.news_dir)
    print(f"recovered_note={path.name} selected={len(selected)} "
          f"public={public_count} translated={translated_count} written={written}")


if __name__ == "__main__":
    main()
