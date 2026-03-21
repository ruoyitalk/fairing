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
    # False when the source name appears in the 'disabled' list of sources.local.yaml,
    # or when the source entry itself sets enabled: false.
    enabled: bool = True


def _load_sources_yaml(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _news_dir() -> pathlib.Path:
    """Resolve NEWS_DIR: the root output directory for daily digests.

    Falls back to ~/Documents/fairing-news when NEWS_DIR is not set.
    """
    raw = os.environ.get("NEWS_DIR", "").strip()
    return pathlib.Path(raw).expanduser() if raw else pathlib.Path.home() / "Documents" / "fairing-news"


@dataclass
class Config:
    news_dir: str = field(default_factory=lambda: str(_news_dir()))
    rss_sources: list[RssSource] = field(default_factory=list)

    def __post_init__(self) -> None:
        public  = _load_sources_yaml(_PUBLIC_SOURCES)
        local   = _load_sources_yaml(_LOCAL_SOURCES)
        # 'disabled' is a name list in sources.local.yaml that overrides individual
        # source entries without requiring changes to sources.yaml.
        disabled: set[str] = set(local.get("disabled", []))
        rss_entries = public.get("rss", []) + local.get("rss", [])
        self.rss_sources = [
            RssSource(
                name=s["name"],
                url=s["url"],
                category=s.get("category", "General"),
                enabled=s.get("enabled", True) and s["name"] not in disabled,
            )
            for s in rss_entries
        ]
