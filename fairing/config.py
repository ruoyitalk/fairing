import os
import pathlib
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

load_dotenv()

_CONFIG_DIR     = pathlib.Path(__file__).parent.parent / "config"
_PUBLIC_SOURCES = _CONFIG_DIR / "sources.yaml"
_LOCAL_SOURCES  = _CONFIG_DIR / "sources.local.yaml"


@dataclass
class RssSource:
    name: str
    url: str
    category: str
    lookback_hours: int = 24
    firecrawl_fulltext: bool = False


def _load_sources_yaml(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class Config:
    firecrawl_api_key: str = field(default_factory=lambda: os.environ.get("FIRECRAWL_API_KEY", ""))
    obsidian_dir: str = field(default_factory=lambda: str(
        pathlib.Path(os.environ["OBSIDIAN_DIR"]).expanduser() if os.environ.get("OBSIDIAN_DIR")
        else pathlib.Path.home() / "Documents" / "ruoyinote"
    ))
    notebooklm_dir: str = field(default_factory=lambda: str(
        pathlib.Path(os.environ["NOTEBOOKLM_DIR"]).expanduser() if os.environ.get("NOTEBOOKLM_DIR")
        else ""
    ))
    rss_sources: list[RssSource] = field(default_factory=list)

    def __post_init__(self) -> None:
        public = _load_sources_yaml(_PUBLIC_SOURCES)
        local  = _load_sources_yaml(_LOCAL_SOURCES)
        rss_entries = public.get("rss", []) + local.get("rss", [])
        self.rss_sources = [
            RssSource(
                name=s["name"],
                url=s["url"],
                category=s.get("category", "General"),
                lookback_hours=s.get("lookback_hours", 24),
                firecrawl_fulltext=s.get("firecrawl_fulltext", False),
            )
            for s in rss_entries
        ]
