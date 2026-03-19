"""fairing — interactive CLI shell.

Start interactive session:
    python main.py

Non-interactive (e.g. cron):
    python main.py run [--chinese] [--notebooklm] [--fulltext] [--all]
"""
import cmd
import datetime
import json
import logging
import os
import re
import sys
from pathlib import Path

import yaml

try:
    import colorama
    colorama.init(autoreset=True)
    def _c(code: str, text: str) -> str:
        return f"{code}{text}{colorama.Style.RESET_ALL}"
    R  = colorama.Style.RESET_ALL
    B  = colorama.Style.BRIGHT
    DM = colorama.Style.DIM
    CY = colorama.Fore.CYAN
    GR = colorama.Fore.GREEN
    YL = colorama.Fore.YELLOW
    RD = colorama.Fore.RED
    BL = colorama.Fore.BLUE
except ImportError:
    def _c(_code: str, text: str) -> str: return text
    R = B = DM = CY = GR = YL = RD = BL = ""

ROOT        = Path(__file__).parent
PUBLIC_YAML = ROOT / "config" / "sources.yaml"
LOCAL_YAML  = ROOT / "config" / "sources.local.yaml"
SEEN_URLS   = ROOT / ".seen_urls.json"
DIGEST_HASH = ROOT / ".digest_hash"
ENV_FILE    = ROOT / ".env"

from fairing import __version__

LOGO = f"""{B}{CY}
   ___      _      _
  / _|__ _ (_)_ __(_)_ _  __ _
 |  _/ _` || | '__| | ' \\/ _` |
 |_| \\__,_||_|_|  |_|_||_\\__, |
                            |___/{R}
  {DM}v{__version__}  ·  ruoyi_talk  ·  {BL}zhangjunjie@apache.org{R}
"""

# Slash shortcuts (database-style)
_SHORTCUTS: dict[str, str] = {
    r"\r":  "run",
    r"\rm": "run --md",
    r"\rq": "run --no-mail",
    r"\rc": "run --chinese",
    r"\rf": "run --fulltext",
    r"\ra": "run --all",
    r"\c":  "config",
    r"\e":  "env",
    r"\l":  "log",
    r"\h":  "shortcuts",
    r"\?":  "shortcuts",
    r"\q":  "quit",
}

def _clear() -> None:
    """Clear the terminal screen (cross-platform)."""
    print("\033[2J\033[H", end="", flush=True)


logging.basicConfig(
    level=logging.INFO,
    format=f"{DM}%(asctime)s{R}  {CY}%(levelname)-7s{R}  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _table(headers: list[str], rows: list[list[str]], indent: int = 2) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    pad = " " * indent
    sep = f"{DM}" + pad + "  ".join("─" * w for w in widths) + R
    hdr = pad + "  ".join(f"{B}{h.ljust(widths[i])}{R}" for i, h in enumerate(headers))
    lines = [hdr, sep]
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            s = str(cell).ljust(widths[i])
            if headers[i] == "Full-text" and cell == "yes":
                s = f"{GR}{s}{R}"
            elif headers[i] == "Lookback" and "168" in str(cell):
                s = f"{YL}{s}{R}"
            elif headers[i] in ("#", "URL", "Hash"):
                s = f"{DM}{s}{R}"
            cells.append(s)
        lines.append(pad + "  ".join(cells))
    return "\n".join(lines)


def _mask(key: str, value: str) -> str:
    lower = key.lower()
    if "password" in lower or "auth" in lower:
        return "****"
    if "api_key" in lower or "apikey" in lower:
        return value[:6] + "****" if len(value) > 6 else "****"
    return value


# ── env ───────────────────────────────────────────────────────────────────────

def _load_env_file() -> dict[str, str]:
    result: dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _save_env_file(data: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "\n".join(f"{k}={v}" for k, v in data.items()) + "\n",
        encoding="utf-8",
    )


def _show_env() -> None:
    data = _load_env_file()
    if not data:
        print(f"  {YL}.env not found or empty{R}")
        return
    print(f"\n  {B}.env{R}\n")
    kw = max(len(k) for k in data)
    for k, v in data.items():
        masked  = _mask(k, v)
        key_str = f"{CY}{k.ljust(kw)}{R}"
        val_str = f"{DM}{masked}{R}" if masked != v else v
        print(f"    {key_str}  {val_str}")
    print()


def _set_env(key: str, value: str) -> None:
    data = _load_env_file()
    data[key] = value
    _save_env_file(data)
    print(f"  {GR}Updated{R} {CY}{key}{R} in .env")


# ── config display ────────────────────────────────────────────────────────────

def _show_sources() -> None:
    for yaml_path, label_color in [
        (PUBLIC_YAML, f"{GR}public{R}"),
        (LOCAL_YAML,  f"{YL}private{R}"),
    ]:
        data = _load_yaml(yaml_path)
        rss  = data.get("rss", [])
        print(f"\n  {B}{yaml_path.name}{R}  ({label_color})")
        if not rss:
            print(f"  {DM}  (empty){R}")
            continue
        rows = []
        for i, s in enumerate(rss, 1):
            ft = "yes" if s.get("firecrawl_fulltext") else ""
            rows.append([
                str(i),
                s.get("name", ""),
                s.get("category", ""),
                f"{s.get('lookback_hours', 24)} h",
                ft,
                s.get("url", ""),
            ])
        print(_table(["#", "Name", "Category", "Lookback", "Full-text", "URL"], rows))
    print()


# ── run log ───────────────────────────────────────────────────────────────────

def _show_log() -> None:
    print()
    if not SEEN_URLS.exists():
        print(f"  {DM}No run history yet.{R}\n")
        return

    data = json.loads(SEEN_URLS.read_text(encoding="utf-8"))
    days = sorted(data.keys(), reverse=True)

    # email info
    email_date = email_hash = ""
    if DIGEST_HASH.exists():
        email_hash = DIGEST_HASH.read_text().strip()
        mtime = DIGEST_HASH.stat().st_mtime
        email_date = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

    print(f"  {B}Run history{R}  ({len(days)} day(s) on record)\n")
    rows = []
    for day in days:
        count  = len(data[day])
        emailed = f"{GR}yes{R}" if day == email_date else ""
        rows.append([day, str(count), emailed])
    print(_table(["Date", "Articles", "Emailed"], rows))

    if email_hash:
        print(f"\n  Last email  {DM}hash: {email_hash[:16]}…{R}  sent: {email_date}")
    print()


# ── shortcuts help ────────────────────────────────────────────────────────────

def _show_shortcuts() -> None:
    print(f"\n  {B}Shortcuts{R}\n")
    pairs = [
        (r"\r",  "run"),
        (r"\rm", "run --md       (Obsidian only, no NotebookLM)"),
        (r"\rq", "run --no-mail  (skip email)"),
        (r"\rc", "run --chinese"),
        (r"\rf", "run --fulltext"),
        (r"\ra", "run --all      (--chinese + --fulltext)"),
        (r"\c",  "config"),
        (r"\e",  "env"),
        (r"\l",  "log"),
        (r"\h",  "shortcuts"),
        (r"\?",  "shortcuts"),
        (r"\q",  "quit"),
    ]
    kw = max(len(k) for k, _ in pairs)
    for k, v in pairs:
        print(f"    {CY}{k.ljust(kw)}{R}  {v}")
    print()


# ── digest runner ─────────────────────────────────────────────────────────────

def _enrich_fulltext(articles: list[dict], api_key: str,
                     sources_with_fulltext: set[str]) -> list[dict]:
    targets = [a for a in articles if a["source"] in sources_with_fulltext]
    if not targets:
        return articles
    from firecrawl import Firecrawl
    app = Firecrawl(api_key=api_key)
    ok = failed = 0
    for a in targets:
        try:
            doc  = app.scrape(a["url"], formats=["markdown"])
            full = (doc.markdown or "").strip()
            if full:
                a["full_text"] = full[:5000]
                if not a.get("image_url"):
                    m = re.search(r"!\[[^\]]*\]\((https?://[^\)]+)\)", full)
                    if m:
                        a["image_url"] = m.group(1)
                ok += 1
        except Exception as exc:
            failed += 1
            logger.warning("FIRECRAWL FAILED  %s  %s", a["title"][:60], exc)
    logger.info("Firecrawl fulltext: %d ok / %d failed", ok, failed)
    return articles


def run_digest(chinese: bool = False, fulltext: bool = False,
               md_only: bool = False, no_mail: bool = False) -> None:
    from dotenv import load_dotenv
    load_dotenv(override=True)

    from fairing.config import Config
    from fairing.rss import fetch_rss
    from fairing.writer import write_obsidian, write_chinese, write_notebooklm, archive_vault
    from fairing.mailer import send_digest
    from fairing.state import filter_unseen, mark_seen
    cfg        = Config()

    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    logger.info("=== Fetching RSS feeds ===")
    articles = fetch_rss(cfg.rss_sources)

    if not articles:
        logger.warning("No articles collected.")
        return
    logger.info("Total: %d articles", len(articles))

    articles = filter_unseen(articles)
    if not articles:
        logger.info("No new articles after cross-day filtering.")
        return
    logger.info("Fresh: %d articles", len(articles))

    sources_with_fulltext = {s.name for s in cfg.rss_sources if s.firecrawl_fulltext}
    if sources_with_fulltext and cfg.firecrawl_api_key:
        logger.info("=== Fetching full text (Firecrawl) ===")
        articles = _enrich_fulltext(articles, cfg.firecrawl_api_key, sources_with_fulltext)
    elif sources_with_fulltext:
        logger.warning("FIRECRAWL_API_KEY not set — full-text skipped")

    if chinese:
        if not gemini_key:
            logger.warning("--chinese: GEMINI_API_KEY not set — skipping")
            chinese = False
        else:
            from fairing.translator import translate
            logger.info("=== Translating (Gemini) ===")
            articles = translate(articles, api_key=gemini_key)

    moved = archive_vault(cfg.obsidian_dir)
    if moved:
        logger.info("Archived %d note(s) into week folders", moved)

    # Always write Obsidian note
    path, count = write_obsidian(articles, cfg.obsidian_dir)
    logger.info("Obsidian (EN) -> %s  [+%d]", path, count)

    # NotebookLM — default on (if dir configured), skipped in --md mode
    if not md_only and cfg.notebooklm_dir:
        nlm = write_notebooklm(articles, cfg.notebooklm_dir)
        logger.info("NotebookLM    -> %s", nlm)
    elif not md_only and not cfg.notebooklm_dir:
        logger.warning("NOTEBOOKLM_DIR not set — NotebookLM output skipped")

    # Chinese translation is opt-in
    if chinese:
        path_zh, cnt = write_chinese(articles, cfg.obsidian_dir)
        logger.info("Obsidian (ZH) -> %s  [+%d]", path_zh, cnt)

    mark_seen(articles)

    if no_mail:
        logger.info("Email skipped (--no-mail)")
    else:
        send_digest(articles)


# ── interactive shell ─────────────────────────────────────────────────────────

class Shell(cmd.Cmd):
    intro  = LOGO + f"  {DM}type '\\?' for shortcuts, 'help' for full docs{R}\n"
    prompt = f"{CY}fairing{R} {DM}>{R} "

    def do_run(self, line: str) -> None:
        """Run the daily digest.
  run              Obsidian + NotebookLM (if configured) + email
  run --md         Obsidian only, no NotebookLM
  run --no-mail    skip email
  run --chinese    also write Chinese-translated note (GEMINI_API_KEY required)
  run --fulltext   fetch full article body via Firecrawl
  run --all        --chinese + --fulltext"""
        _clear()
        args    = line.split()
        all_    = "--all"      in args
        chinese = "--chinese"  in args or all_
        ft      = "--fulltext" in args or all_
        md_only = "--md"       in args
        no_mail = "--no-mail"  in args
        try:
            run_digest(chinese=chinese, fulltext=ft, md_only=md_only, no_mail=no_mail)
        except Exception as exc:
            logger.error("Run failed: %s", exc)

    def do_config(self, _line: str) -> None:
        """Show all configured feed sources."""
        _clear()
        _show_sources()

    def do_env(self, line: str) -> None:
        """View or update .env variables.
  env                  show all values (sensitive fields masked)
  env set KEY VALUE    update a single key in .env"""
        _clear()
        parts = line.split(maxsplit=2)
        if not parts:
            _show_env()
        elif parts[0] == "set":
            if len(parts) < 3:
                print(f"  {YL}Usage: env set KEY VALUE{R}")
            else:
                _set_env(parts[1], parts[2])
        else:
            print(f"  {YL}Usage: env  |  env set KEY VALUE{R}")

    def do_log(self, _line: str) -> None:
        """Show run history: articles processed per day and email status."""
        _clear()
        _show_log()

    def do_shortcuts(self, _line: str) -> None:
        r"""Show available slash shortcuts (\r, \c, \e, \l, \h, \q)."""
        _clear()
        _show_shortcuts()

    def do_exit(self, _line: str) -> bool:
        """Exit fairing."""
        print(f"  {DM}bye{R}")
        return True

    def do_quit(self, line: str) -> bool:
        """Exit fairing."""
        return self.do_exit(line)

    def do_EOF(self, _line: str) -> bool:
        print()
        return True

    def default(self, line: str) -> None:
        word = line.split()[0] if line.split() else line
        if word in _SHORTCUTS:
            expanded = _SHORTCUTS[word]
            rest = line[len(word):].strip()
            return self.onecmd(f"{expanded} {rest}".strip())
        print(f"  {YL}Unknown:{R} {line!r}  (type '\\?' for shortcuts)")

    def emptyline(self) -> None:
        pass


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        args    = sys.argv[2:]
        all_    = "--all"      in args
        chinese = "--chinese"  in args or all_
        ft      = "--fulltext" in args or all_
        md_only = "--md"       in args
        no_mail = "--no-mail"  in args
        run_digest(chinese=chinese, fulltext=ft, md_only=md_only, no_mail=no_mail)
        return
    Shell().cmdloop()


if __name__ == "__main__":
    main()
