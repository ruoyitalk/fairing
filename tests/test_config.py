"""Tests for fairing/config.py — RssSource, Config loading, disabled list."""
import pathlib
import pytest
import yaml


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Redirect Config to use temp yaml files."""
    import fairing.config as c
    pub = tmp_path / "sources.yaml"
    loc = tmp_path / "sources.local.yaml"
    monkeypatch.setattr(c, "_PUBLIC_SOURCES", pub)
    monkeypatch.setattr(c, "_LOCAL_SOURCES",  loc)
    return pub, loc


def _write_yaml(path: pathlib.Path, data: dict) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


# ── RssSource defaults ─────────────────────────────────────────────────────────

def test_rss_source_defaults():
    from fairing.config import RssSource
    s = RssSource(name="Test", url="https://example.com/rss", category="Tech")
    assert s.enabled is True


def test_rss_source_explicit_disabled():
    from fairing.config import RssSource
    s = RssSource(name="Off", url="https://example.com/rss", category="Tech", enabled=False)
    assert s.enabled is False


# ── Config loading ─────────────────────────────────────────────────────────────

def test_config_loads_public_sources(tmp_config):
    pub, loc = tmp_config
    _write_yaml(pub, {"rss": [{"name": "ArXiv", "url": "https://arxiv.org/rss/cs", "category": "CS"}]})
    from fairing.config import Config
    cfg = Config()
    assert len(cfg.rss_sources) == 1
    assert cfg.rss_sources[0].name == "ArXiv"


def test_config_merges_private_sources(tmp_config):
    pub, loc = tmp_config
    _write_yaml(pub, {"rss": [{"name": "Pub", "url": "https://pub.com/rss", "category": "A"}]})
    _write_yaml(loc, {"rss": [{"name": "Priv", "url": "https://priv.com/rss", "category": "B"}]})
    from fairing.config import Config
    cfg = Config()
    names = [s.name for s in cfg.rss_sources]
    assert "Pub"  in names
    assert "Priv" in names


def test_config_disabled_list_marks_sources(tmp_config):
    pub, loc = tmp_config
    _write_yaml(pub, {"rss": [
        {"name": "Active", "url": "https://a.com/rss", "category": "X"},
        {"name": "Paused", "url": "https://b.com/rss", "category": "X"},
    ]})
    _write_yaml(loc, {"disabled": ["Paused"]})
    from fairing.config import Config
    cfg = Config()
    by_name = {s.name: s for s in cfg.rss_sources}
    assert by_name["Active"].enabled is True
    assert by_name["Paused"].enabled is False


def test_config_disabled_list_empty_by_default(tmp_config):
    pub, loc = tmp_config
    _write_yaml(pub, {"rss": [{"name": "Src", "url": "https://s.com/rss", "category": "X"}]})
    # No local yaml
    from fairing.config import Config
    cfg = Config()
    assert all(s.enabled for s in cfg.rss_sources)


def test_config_source_level_enabled_false(tmp_config):
    """enabled: false in yaml entry should override."""
    pub, loc = tmp_config
    _write_yaml(pub, {"rss": [{"name": "Off", "url": "https://off.com/rss", "category": "X", "enabled": False}]})
    from fairing.config import Config
    cfg = Config()
    assert cfg.rss_sources[0].enabled is False


