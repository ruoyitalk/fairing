"""Export a reviewed public feed selection for free local RSS readers."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import yaml


_CONFIG = Path(__file__).resolve().parent.parent / "config"


def is_public_feed_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme != "https" or not hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or hostname == "localhost"
            or hostname.endswith((".local", ".internal", ".localhost"))):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return False


def build_public_opml(source_file: Path, selection_file: Path) -> str:
    if source_file.resolve() != (_CONFIG / "sources.yaml").resolve():
        raise ValueError("OPML export is restricted to the tracked public source catalog")
    public = yaml.safe_load(source_file.read_text(encoding="utf-8")) or {}
    selection = yaml.safe_load(selection_file.read_text(encoding="utf-8")) or {}
    entries = public.get("rss")
    names = selection.get("sources")
    if not isinstance(entries, list) or not isinstance(names, list) or not names:
        raise ValueError("public feeds and a nonempty pilot selection are required")
    if len(names) != len(set(names)):
        raise ValueError("pilot selection contains duplicate names")

    by_name = {entry["name"]: entry for entry in entries}
    if len(by_name) != len(entries):
        raise ValueError("public feed names must be unique")

    root = ET.Element("opml", version="2.0")
    ET.SubElement(root, "head").append(ET.Element("title"))
    root.find("head/title").text = "Fairing public AI pilot"
    body = ET.SubElement(root, "body")
    groups: dict[str, ET.Element] = {}
    for name in names:
        if name not in by_name:
            raise ValueError(f"pilot source is not in the public catalog: {name}")
        entry = by_name[name]
        if not entry.get("enabled", True):
            raise ValueError(f"pilot source is disabled: {name}")
        url = entry.get("url", "")
        if not is_public_feed_url(url):
            raise ValueError(f"pilot source must have a public HTTPS feed URL: {name}")
        category = str(entry.get("category") or "AI")
        group = groups.get(category)
        if group is None:
            group = ET.SubElement(body, "outline", text=category, title=category)
            groups[category] = group
        ET.SubElement(group, "outline", type="rss", text=name, title=name, xmlUrl=url)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export public Fairing feeds as OPML")
    parser.add_argument("--selection", type=Path, default=_CONFIG / "ai-pilot-sources.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = build_public_opml(_CONFIG / "sources.yaml", args.selection)
    if args.output is None:
        sys.stdout.write(document)
    else:
        args.output.write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
