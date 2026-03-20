"""fairing — interactive CLI shell.

Start interactive session:
    python main.py

Non-interactive (e.g. cron):
    python main.py run [--chinese] [--md] [--notebook] [--no-mail] [--force]
"""
import cmd
import json
import logging
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

ROOT          = Path(__file__).parent
PUBLIC_YAML   = ROOT / "config" / "sources.yaml"
LOCAL_YAML    = ROOT / "config" / "sources.local.yaml"
ENV_FILE      = ROOT / ".env"

from fairing.paths import (
    seen_urls_file     as _seen_urls_file,
    digest_hash_file   as _digest_hash_file,
    rate_pending_file  as _rate_pending_file,
    last_run_file      as _last_run_file,
    last_run_time_file as _last_run_time_file,
)

def SEEN_URLS():    return _seen_urls_file()
def DIGEST_HASH():  return _digest_hash_file()
def RATE_PENDING(): return _rate_pending_file()
def LAST_RUN():     return _last_run_file()

from fairing import __version__

# ── ANSI colours (for cmd.Cmd prompt which doesn't use rich) ──────────────────
try:
    import colorama
    colorama.init(autoreset=True)
    _CY = colorama.Fore.CYAN
    _DM = colorama.Style.DIM
    _R  = colorama.Style.RESET_ALL
except ImportError:
    _CY = _DM = _R = ""

LOGO = f"""
   ___      _      _
  / _|__ _ (_)_ __(_)_ _  __ _
 |  _/ _` || | '__| | ' \\/  _` |
 |_| \\__,_||_|_|  |_|_||_\\__, |
                            |___/
"""

_SHORTCUTS: dict[str, str] = {
    r"\r":    "run",
    r"\rate": "rate",
    r"\ms":   "model_status",
    r"\bk":   "backup",
    r"\rd":   "read",
    r"\rs":   "restore",
    r"\re":   "resend",
    r"\dl":   "remd",
    r"\c":    "config",
    r"\e":    "env",
    r"\l":    "log",
    r"\h":    "shortcuts",
    r"\?":    "shortcuts",
    r"\t":    "toggle",
    r"\lb":   "labels",
    r"\pd":   "payload",
    r"\ps":   "psearch",
    r"\sd":   "send",
    r"\li":   "license",
    r"\q":    "quit",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers — only let WARNING+ through.
for _noisy in (
    "transformers",
    "sentence_transformers",
    "torch",
    "filelock",
    "urllib3",
    "httpx",
    "httpcore",
    "absl",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_TZ_BEIJING = timezone(timedelta(hours=8))


def _today_beijing() -> str:
    """Return today's date string in Beijing time (UTC+8)."""
    return datetime.now(_TZ_BEIJING).date().isoformat()


# ── dynamic lookback ──────────────────────────────────────────────────────────

# Pipeline start date (Beijing time). Used as baseline on first ever run.
_PIPELINE_EPOCH = datetime(2026, 3, 20, 0, 0, 0, tzinfo=_TZ_BEIJING)


def _hours_since_last_run() -> float:
    """Return hours elapsed since the last successful run_digest().

    Falls back to the pipeline epoch (2026-03-20 00:00 Beijing) when the file
    does not exist so the first-ever run fetches a safe initial window.
    """
    now = datetime.now(_TZ_BEIJING)
    f   = _last_run_time_file()
    if f.exists():
        try:
            last = datetime.fromisoformat(f.read_text().strip())
            return max(0.0, (now - last).total_seconds() / 3600)
        except (ValueError, OSError):
            pass
    return max(0.0, (now - _PIPELINE_EPOCH).total_seconds() / 3600)


def _save_last_run_time() -> None:
    """Persist the current Beijing timestamp as the last-run marker."""
    _last_run_time_file().write_text(datetime.now(_TZ_BEIJING).isoformat())


# ── helpers ───────────────────────────────────────────────────────────────────

def _clear() -> None:
    """Clear terminal screen cross-platform."""
    import os
    os.system("cls" if os.name == "nt" else "clear")


def _load_yaml(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _mask(key: str, value: str) -> str:
    lower = key.lower()
    if "password" in lower or "auth" in lower:
        return "****"
    if "api_key" in lower or "apikey" in lower:
        return value[:6] + "****" if len(value) > 6 else "****"
    return value


# ── rate-gate ─────────────────────────────────────────────────────────────────

def _load_pending() -> dict:
    if RATE_PENDING().exists():
        return json.loads(RATE_PENDING().read_text(encoding="utf-8"))
    return {}


def _save_pending(data: dict) -> None:
    RATE_PENDING().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_rate_gate(force: bool = False) -> bool:
    pending = _load_pending()
    if pending and not pending.get("completed", True) and not force:
        done      = len(pending.get("done_urls", []))
        total     = len(pending.get("sample_urls", []))
        console.print(Panel(
            f"[yellow]上次打标任务尚未完成[/yellow]  ({done}/{total} 篇)\n\n"
            f"完成标注后才能拉取新文章，避免训练数据断档。\n"
            f"  · 继续标注：[cyan]\\rate[/cyan]\n"
            f"  · 强制跳过：[dim]run --force[/dim]（不推荐）",
            title="[red bold]待完成标注[/red bold]", border_style="red",
        ))
        return False
    return True


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
        console.print("[yellow].env 文件不存在或为空[/yellow]")
    else:
        t = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE)
        t.add_column("变量",  style="cyan")
        t.add_column("值")
        for k, v in data.items():
            masked = _mask(k, v)
            style  = "dim" if masked != v else ""
            t.add_row(k, masked, style=style)
        console.print(Panel(t, title="[bold].env[/bold]", border_style="blue"))



def _set_env(key: str, value: str) -> None:
    data = _load_env_file()
    data[key] = value
    _save_env_file(data)
    console.print(f"  [green]Updated[/green] [cyan]{key}[/cyan] in .env")


# ── config display ────────────────────────────────────────────────────────────

def _show_sources() -> None:
    from datetime import timedelta
    from fairing.embedder import load_store
    from fairing.state import normalize_url as _norm

    # ── Build per-source 7-day counts ─────────────────────────────────────────
    src_7d: dict[str, int] = {}
    total_7d = 0
    if SEEN_URLS().exists():
        cutoff    = (datetime.now(_TZ_BEIJING) - timedelta(days=7)).date().isoformat()
        seen_data = json.loads(SEEN_URLS().read_text(encoding="utf-8"))
        store     = load_store()
        norm_to_src = {_norm(url): e.get("source", "") for url, e in store.items()}
        for day, val in seen_data.items():
            if day < cutoff:
                continue
            for nu in (val.get("urls", val) if isinstance(val, dict) else val):
                total_7d += 1
                src = norm_to_src.get(nu, "")
                if src:
                    src_7d[src] = src_7d.get(src, 0) + 1

    console.print(
        f"  [dim]过去 7 天收录[/dim]  [bold cyan]{total_7d}[/bold cyan] 篇（不重复）\n"
    )

    # ── Build per-source last-article datetime (hours ago) ────────────────────
    from fairing.paths import title_index_file
    import json as _json
    now_utc = datetime.now(timezone.utc)
    src_last_h: dict[str, float] = {}
    _fmt = "%Y-%m-%d %H:%M UTC"
    if title_index_file().exists():
        src_latest: dict[str, datetime] = {}
        for line in title_index_file().read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e   = _json.loads(line)
                src = e.get("source", "")
                raw = e.get("date", "")
                if not src or not raw or raw == "unknown":
                    continue
                try:
                    dt = datetime.strptime(raw, _fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                if src not in src_latest or dt > src_latest[src]:
                    src_latest[src] = dt
            except (json.JSONDecodeError, KeyError):
                continue
        for src, dt in src_latest.items():
            src_last_h[src] = (now_utc - dt).total_seconds() / 3600

    for yaml_path, label in [(PUBLIC_YAML, "public"), (LOCAL_YAML, "private")]:
        data = _load_yaml(yaml_path)
        rss  = data.get("rss", [])
        color = "green" if label == "public" else "yellow"
        if not rss:
            console.print(f"[bold]{yaml_path.name}[/bold] ([{color}]{label}[/{color}])  [dim](empty)[/dim]")
            continue
        disabled_set = set(_load_yaml(LOCAL_YAML).get("disabled", []))
        t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD)
        t.add_column("#",     style="dim",  width=3)
        t.add_column("名称",  style="cyan", min_width=22)
        t.add_column("分类",  min_width=12)
        t.add_column("7天",   justify="right", width=5, style="cyan")
        t.add_column("上次",  justify="right", width=7)
        t.add_column("状态",  width=5)
        for i, s in enumerate(rss, 1):
            name    = s.get("name", "")
            cnt     = str(src_7d.get(name, 0)) if src_7d.get(name) else "[dim]0[/dim]"
            enabled = name not in disabled_set and s.get("enabled", True)
            status  = "[green]开[/green]" if enabled else "[red]关[/red]"
            if name in src_last_h:
                h = src_last_h[name]
                if h < 1:
                    last_str = "[green]<1h[/green]"
                elif h < 48:
                    last_str = f"[cyan]{int(h)}h[/cyan]"
                else:
                    last_str = f"[yellow]{int(h // 24)}d[/yellow]"
            else:
                last_str = "[dim]—[/dim]"
            t.add_row(str(i), name, s.get("category", ""), cnt, last_str, status)
        console.print(Panel(t, title=f"[bold]{yaml_path.name}[/bold] [{color}]({label})[/{color}]",
                            border_style=color))


# ── log ───────────────────────────────────────────────────────────────────────

def _show_log() -> None:
    import datetime as dt
    if not SEEN_URLS().exists():
        console.print(Panel("[dim]暂无运行记录[/dim]", border_style="dim"))
        return

    from fairing.embedder import load_store
    from fairing.state import normalize_url as _norm_url

    data       = json.loads(SEEN_URLS().read_text(encoding="utf-8"))
    days       = sorted(data.keys(), reverse=True)
    email_date = ""
    if DIGEST_HASH().exists():
        email_date = dt.datetime.fromtimestamp(
            DIGEST_HASH().stat().st_mtime
        ).strftime("%Y-%m-%d")

    # Build normalized-url → source map from scoring store
    store = load_store()
    norm_to_source: dict[str, str] = {
        _norm_url(url): entry.get("source", "") for url, entry in store.items()
    }

    # Backfill unmatched: try domain-based attribution from configured RSS sources
    from urllib.parse import urlparse as _urlparse
    from fairing.config import Config as _Cfg
    def _bare_domain(u: str) -> str:
        try:
            return _urlparse(u.lower()).netloc.lstrip("www.")
        except Exception:
            return ""
    _domain_to_src = {_bare_domain(s.url): s.name for s in _Cfg().rss_sources if s.url}
    for _nu, _src in list(norm_to_source.items()):
        if not _src:
            _d = _bare_domain(_nu)
            _matched = _domain_to_src.get(_d, "")
            if not _matched:
                for _sd, _sn in _domain_to_src.items():
                    if _sd and (_sd in _d or _d in _sd):
                        _matched = _sn
                        break
            if _matched:
                norm_to_source[_nu] = _matched

    # ── 汇总表 ────────────────────────────────────────────────────────────────
    t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD)
    t.add_column("日期",     style="cyan")
    t.add_column("文章数",   justify="right")
    t.add_column("邮件",     width=6)
    for day in days:
        val   = data[day]
        urls  = val.get("urls", val) if isinstance(val, dict) else val
        count = len(urls)
        sent  = "[green]✓[/green]" if day == email_date else "[dim]—[/dim]"
        t.add_row(day, str(count), sent)
    total = sum(
        len(v.get("urls", v) if isinstance(v, dict) else v) for v in data.values()
    )
    console.print(Panel(
        t,
        title=f"[bold]运行记录[/bold]  共 {len(days)} 天 · {total} 篇",
        border_style="blue",
    ))

    # ── 今日各源头明细 ────────────────────────────────────────────────────────
    if days:
        today_val  = data[days[0]]
        today_urls = today_val.get("urls", today_val) if isinstance(today_val, dict) else today_val
        src_counts: dict[str, int] = {}
        unknown    = 0
        for norm_url in today_urls:
            src = norm_to_source.get(norm_url)
            if src:
                src_counts[src] = src_counts.get(src, 0) + 1
            else:
                unknown += 1
        if src_counts or unknown:
            st = Table(show_header=True, header_style="bold dim", box=box.SIMPLE, padding=(0, 1))
            st.add_column("来源",   style="cyan")
            st.add_column("数量",   justify="right")
            for src, cnt in sorted(src_counts.items(), key=lambda x: -x[1]):
                st.add_row(src, str(cnt))
            if unknown:
                st.add_row(
                    f"[dim]（未匹配 {unknown} 篇）[/dim]",
                    "[dim]已记录 URL 但无嵌入缓存，\n通常为旧版本运行的历史数据[/dim]",
                )
            console.print(Panel(
                st,
                title=f"[bold]今日来源明细[/bold]  {days[0]}",
                border_style="dim",
            ))

    # ── 订阅源失败警告 ────────────────────────────────────────────────────────
    from fairing.rss import load_feed_errors
    errors = load_feed_errors()
    troubled = {k: v for k, v in errors.items() if v.get("consecutive_failures", 0) >= 5}
    if troubled:
        et = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
        et.add_column("订阅源",     style="cyan", min_width=24)
        et.add_column("连续失败",   justify="right", width=8)
        et.add_column("最后失败",   width=12)
        et.add_column("错误摘要",   style="dim")
        for name, info in sorted(troubled.items(), key=lambda x: -x[1].get("consecutive_failures", 0)):
            et.add_row(
                name,
                str(info.get("consecutive_failures", 0)),
                info.get("last_failed", "—"),
                info.get("last_error", "")[:60],
            )
        console.print(Panel(
            et,
            title="[bold red]订阅源持续失败（≥5次）[/bold red]",
            border_style="red",
        ))
        console.print("  [dim]成功拉取后自动清除。可用 [cyan]\\t[/cyan] 暂时关闭异常源。[/dim]")


# ── model status ──────────────────────────────────────────────────────────────

def _show_model_status() -> None:
    from fairing.trainer import (model_status, load_feedback, tfidf_top_terms,
                                  DECAY_BASE, DECAY_UNIT, ACCURACY_THRESHOLD,
                                  MIN_TOTAL, MIN_POS, MIN_NEG)
    from fairing.embedder import load_store

    st       = model_status()
    feedback = load_feedback()
    pos      = [f for f in feedback if f["label"] ==  1]
    neg      = [f for f in feedback if f["label"] == -1]
    translator_backend = os.environ.get("TRANSLATOR", "gemini")

    # Main status table
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("Key",   style="dim",  width=18)
    t.add_column("Value", style="bold")

    deployed_txt = (f"[green]✓ 已部署[/green]  {st.get('train_date','')}"
                    if st["deployed"] else "[yellow]✗ 未部署[/yellow]  (标注积累中)")
    t.add_row("部署状态", deployed_txt)
    t.add_row("训练样本", f"[cyan]{st['n_labels']}[/cyan] 条  "
                          f"([green]+{st['n_pos']}[/green] / [red]-{st['n_neg']}[/red])")
    if st["deployed"]:
        t.add_row("分类器",   f"{st.get('model_type','?')}  C={st.get('model_C','?'):.3f}")
        t.add_row("特征维度", f"{st.get('n_features','?')}  (all-MiniLM-L6-v2)")

    t.add_row("", "")
    t.add_row("衰减 base",   f"{DECAY_BASE}  (每档权重减半)")
    t.add_row("衰减 unit",   f"{DECAY_UNIT} 篇/档")
    t.add_row("部署阈值",    f"balanced_accuracy ≥ {ACCURACY_THRESHOLD}")
    t.add_row("最低样本",    f"总 {MIN_TOTAL} 条 / 正 {MIN_POS} / 负 {MIN_NEG}")
    t.add_row("", "")
    t.add_row("翻译后端",    f"[cyan]{translator_backend}[/cyan]  "
                             f"(TRANSLATOR env var)")

    # Progress bar toward training threshold
    pct   = min(1.0, len(feedback) / MAX(MIN_TOTAL, 1))
    bar_w = 30
    filled = int(pct * bar_w)
    bar   = "[green]" + "█" * filled + "[/green][dim]" + "░" * (bar_w - filled) + "[/dim]"
    t.add_row("", "")
    t.add_row("标注进度", f"{bar}  {len(feedback)}/{MIN_TOTAL}")

    console.print(Panel(t, title="[bold]模型状态[/bold]", border_style="cyan"))

    # TF-IDF signals
    if pos or neg:
        store     = load_store()
        pos_texts = [store[f["url"]]["text_for_scoring"] for f in pos if f["url"] in store]
        neg_texts = [store[f["url"]]["text_for_scoring"] for f in neg if f["url"] in store]
        top_pos   = tfidf_top_terms(pos_texts, neg_texts, n=8)
        top_neg   = tfidf_top_terms(neg_texts, pos_texts, n=5)

        sig_t = Table(show_header=True, header_style="bold", box=box.SIMPLE)
        sig_t.add_column("方向",  width=8)
        sig_t.add_column("Top 信号词 (TF-IDF)")
        if top_pos:
            sig_t.add_row("[green]+[/green]", "  ".join(f"[green]{w}[/green]" for w in top_pos))
        if top_neg:
            sig_t.add_row("[red]−[/red]",   "  ".join(f"[red]{w}[/red]"   for w in top_neg))
        console.print(Panel(sig_t, title="[bold]语义信号[/bold]", border_style="dim"))


def MAX(a, b):  # tiny helper to avoid importing max ambiguity
    return a if a > b else b


# ── rate ──────────────────────────────────────────────────────────────────────

def _calc_sample_n(total: int) -> int:
    return min(8, max(3, total // 4))


def _sample_articles(articles: list[dict]) -> list[dict]:
    from fairing.trainer import load_feedback
    rated = {f["url"] for f in load_feedback()}
    pool  = [a for a in articles if a["url"] not in rated]
    if not pool:
        return []
    n           = _calc_sample_n(len(articles))
    pool_sorted = sorted(pool, key=lambda a: a.get("score", 0.5))
    third       = max(1, len(pool_sorted) // 3)
    low, mid, high = pool_sorted[:third], pool_sorted[third:2*third], pool_sorted[2*third:]
    sample = (random.sample(high, min(2, len(high))) +
              random.sample(mid,  min(3, len(mid)))  +
              random.sample(low,  min(1, len(low))))
    seen_src = {f.get("source") for f in load_feedback()}
    new_src  = [a for a in pool if a["source"] not in seen_src
                and a["url"] not in {s["url"] for s in sample}]
    if new_src and len(sample) < n:
        sample.append(random.choice(new_src))
    return sample[:n]


def _prompt_choice(a: dict, idx: int, total: int,
                   session_pos: int, session_neg: int,
                   pos_count: int, neg_count: int, total_hist: int,
                   mode_tag: str = "",
                   current_label: int | None = None,
                   can_go_back: bool = False,
                   daily_done: int = 0,
                   daily_total: int = 0) -> str:
    """Display one article card, prompt for input, and return the choice.

    Valid returns: '+' '-' 'n' 's' 'o' (open URL) 'p' (previous, only if can_go_back).
    daily_done / daily_total: overall today's task progress (mandatory mode only).
    """
    _clear()

    if current_label is not None:
        prog_line = (
            f"本次已改 [bold]{session_pos + session_neg}[/bold] 条  │  "
            f"历史 [green]+{pos_count}[/green] / [red]-{neg_count}[/red]  共 [bold]{total_hist}[/bold] 条"
        )
    elif daily_total > 0:
        # Mandatory mode: show today's overall task progress bar
        bar_w  = 15
        filled = int(daily_done / daily_total * bar_w)
        bar    = "[cyan]" + "█" * filled + "[/cyan][dim]" + "░" * (bar_w - filled) + "[/dim]"
        prog_line = (
            f"今日任务  {bar}  {daily_done}/{daily_total} 篇  │  "
            f"本次 [green]+{session_pos}[/green]/[red]-{session_neg}[/red]  │  "
            f"历史 [green]+{pos_count}[/green]/[red]-{neg_count}[/red] 共 {total_hist} 条"
        )
    else:
        prog_line = (
            f"本次  [green]+{session_pos}[/green] 有价值  /  [red]-{session_neg}[/red] 不感兴趣  │  "
            f"历史  [green]+{pos_count}[/green] / [red]-{neg_count}[/red]  共 [bold]{total_hist}[/bold] 条"
        )
    console.print(Panel(prog_line, border_style="dim", padding=(0, 1)))

    from fairing.export import article_id_for, load_payload_queue
    url       = a.get("url", "")
    aid       = article_id_for(url) if url else ""
    in_queue  = any(e["article_id"] == aid for e in load_payload_queue()) if aid else False

    title_line = f"[bold cyan]{a.get('title', '(no title)')}[/bold cyan]"
    meta_parts = [f"[dim]{a.get('source', '')}[/dim]"]
    if aid:
        queue_badge = "[green]📥[/green]" if in_queue else ""
        meta_parts.append(f"[dim cyan]{aid}[/dim cyan]{queue_badge}")
    if "score" in a:
        meta_parts.append(f"[dim]score={a['score']:.2f}[/dim]")
    if current_label is not None:
        badge = "[green]有价值 ✓[/green]" if current_label == 1 else "[red]不感兴趣 ✗[/red]"
        meta_parts.append(f"当前: {badge}")
    meta_line = "  ".join(meta_parts)
    url_line  = f"[dim]{url}[/dim]" if url else ""
    text      = a.get("text_for_scoring", a.get("excerpt", ""))
    preview   = text[:700]
    if len(text) > 700:
        preview += f"\n[dim]...（共 {len(text)} 字）[/dim]"
    body = f"{meta_line}\n{url_line}\n\n{preview}" if url_line else f"{meta_line}\n\n{preview}"

    panel_title = f"[dim][{idx}/{total}]"
    if mode_tag:
        panel_title += f"  {mode_tag}"
    panel_title += "[/dim]"
    console.print(Panel(body, title=f"{title_line}  {panel_title}",
                        border_style="blue", padding=(1, 2)))

    back_hint = "  [dim][ p ][/dim] 上一篇" if can_go_back else ""
    if current_label is not None:
        console.print(
            "  [green bold][ + ][/green bold] 改为有价值   "
            "[red bold][ - ][/red bold] 改为不感兴趣   "
            "[yellow][ n ][/yellow] 保持不变   "
            "[dim][ o ][/dim] 浏览器   [dim][ r ][/dim] 详读   "
            "[magenta][ d ][/magenta] 加入payload"
            + back_hint +
            "   [cyan][ s ][/cyan] 保存退出"
        )
    else:
        console.print(
            "  [green bold][ + ][/green bold] 有价值   "
            "[red bold][ - ][/red bold] 不感兴趣   "
            "[yellow][ n ][/yellow] 跳过   "
            "[dim][ o ][/dim] 浏览器   [dim][ r ][/dim] 详读   "
            "[magenta][ d ][/magenta] 加入payload"
            + back_hint +
            "   [cyan][ s ][/cyan] 保存退出"
        )
    valid = {"+", "-", "n", "s", "o", "r", "d"} | ({"p"} if can_go_back else set())
    hint  = "+ / - / n / o / r / d" + (" / p" if can_go_back else "") + " / s"
    while True:
        choice = input("  > ").strip().lower()
        if choice in valid:
            if choice == "o":
                import webbrowser
                webbrowser.open(url)
                continue
            if choice == "r":
                import os as _os
                from fairing.reader import read_article, save_readnote
                content = read_article(url, title=a.get("title", ""))
                if content is not None:
                    readnotes_raw = _os.environ.get("READNOTES_DIR", "")
                    if readnotes_raw:
                        rn_dir = Path(readnotes_raw).expanduser()
                    else:
                        from fairing.config import Config
                        rn_dir = Path(Config().obsidian_dir) / "readnotes"
                    save_readnote(url=url, title=a.get("title", ""),
                                  content=content, source=a.get("source", ""),
                                  readnotes_dir=rn_dir)
                continue          # re-display same card after returning from editor
            if choice == "d":
                _dispatch_to_payload(a, ask_label=True)
                continue
            return choice
        console.print(f"  [yellow]请输入 {hint}[/yellow]")


def _show_train_result(result, pos_count: int, neg_count: int, total_hist: int) -> None:
    from fairing.trainer import MIN_POS, MIN_NEG, MIN_TOTAL
    if result is None:
        bar_w  = 20
        filled = int(min(1.0, total_hist / max(MIN_TOTAL, 1)) * bar_w)
        bar    = "[cyan]" + "█" * filled + "[/cyan][dim]" + "░" * (bar_w - filled) + "[/dim]"
        gaps   = []
        if total_hist  < MIN_TOTAL: gaps.append(f"总量 {total_hist}/{MIN_TOTAL}")
        if pos_count   < MIN_POS:   gaps.append(f"正样本 {pos_count}/{MIN_POS}")
        if neg_count   < MIN_NEG:   gaps.append(f"负样本 {neg_count}/{MIN_NEG}")
        gap_str = "  |  ".join(gaps) if gaps else "数据充足，等待下次触发"
        console.print(Panel(
            f"训练进度  {bar}  {total_hist} / {MIN_TOTAL} 条\n"
            f"[dim]缺口：{gap_str}[/dim]",
            title="[dim]训练未触发[/dim]", border_style="dim",
        ))
    elif result.deployed:
        console.print(Panel(
            f"[green]模型已自动更新[/green]\n\n"
            f"  准确率   [bold]{result.cv_accuracy:.2%}[/bold] [dim]± {result.cv_std:.2%}[/dim]"
            f"  ({result.n_folds} 折交叉验证)\n"
            f"  参数     C = {result.C_selected:.3f}\n"
            f"  样本数   {result.n_samples} 条"
            f"  ([green]+{result.n_pos}[/green] / [red]-{result.n_neg}[/red])",
            title="[bold green]训练结果[/bold green]", border_style="green",
        ))
    else:
        console.print(Panel(
            f"[yellow]评估未达部署标准[/yellow]  (需 ≥ 75%)\n\n"
            f"  准确率   [bold]{result.cv_accuracy:.2%}[/bold] [dim]± {result.cv_std:.2%}[/dim]"
            f"  ({result.n_folds} 折)\n"
            f"  样本数   {result.n_samples} 条\n\n"
            f"[dim]继续标注，数据更多后精度会提升[/dim]",
            title="[bold yellow]训练结果[/bold yellow]", border_style="yellow",
        ))


def _run_rate(pending: dict) -> None:
    """Mandatory rate mode: label today's sampled articles.

    Tracks cumulative daily progress so the card header shows how many
    articles have been labeled across the entire day, including any
    articles completed in a previous session (done_at_start).
    """
    from fairing.trainer import load_feedback, save_feedback, maybe_auto_train
    from fairing.embedder import load_store

    store     = load_store()
    all_urls  = pending.get("sample_urls", [])
    done_urls: set[str] = set(pending.get("done_urls", []))
    sample    = [dict(store[u]) for u in all_urls if u not in done_urls and u in store]

    if not sample:
        console.print(Panel(
            "当前没有待标注的文章。\n运行 [cyan]\\r[/cyan] 拉取新文章后再来。",
            border_style="yellow",
        ))
        return

    feedback   = load_feedback()
    pos_count  = sum(1 for f in feedback if f["label"] ==  1)
    neg_count  = sum(1 for f in feedback if f["label"] == -1)
    total_hist = len(feedback)
    session_pos = session_neg = 0
    quit_early  = False
    session_map: dict[str, int | None] = {}

    done_at_start = len(done_urls)      # articles already done from previous sessions
    daily_total   = len(all_urls)       # total today's sample size

    cursor = 0
    while cursor < len(sample):
        a          = sample[cursor]
        daily_done = done_at_start + cursor   # cumulative progress this day
        choice = _prompt_choice(a, cursor + 1, len(sample),
                                session_pos, session_neg,
                                pos_count, neg_count, total_hist,
                                can_go_back=cursor > 0,
                                daily_done=daily_done,
                                daily_total=daily_total)
        if choice == "s":
            quit_early = True
            break

        if choice == "p":
            cursor -= 1
            prev_url = sample[cursor]["url"]
            done_urls.discard(prev_url)
            if prev_url in session_map:
                prev_label = session_map.pop(prev_url)
                if prev_label is not None:
                    pos_count   -= prev_label == 1
                    neg_count   -= prev_label == -1
                    session_pos -= prev_label == 1
                    session_neg -= prev_label == -1
                    total_hist  -= 1
            continue

        done_urls.add(a["url"])
        if choice in ("+", "-"):
            label = 1 if choice == "+" else -1
            save_feedback({
                "url":         a["url"],
                "title":       a.get("title", ""),
                "source":      a.get("source", ""),
                "label":       label,
                "label_index": total_hist,
                "date":        _today_beijing(),
            })
            session_map[a["url"]] = label
            pos_count   += label == 1
            neg_count   += label == -1
            session_pos += label == 1
            session_neg += label == -1
            total_hist  += 1
        else:
            session_map[a["url"]] = None
        cursor += 1

    pending["done_urls"] = list(done_urls)
    pending["completed"] = not quit_early
    _save_pending(pending)

    _clear()
    remaining     = len(all_urls) - len(done_urls)
    session_total = session_pos + session_neg
    if quit_early and remaining > 0:
        console.print(Panel(
            f"[yellow]进度已保存[/yellow]  —  可随时回来继续\n\n"
            f"  本次标注   [green]+{session_pos}[/green] 有价值  /  [red]-{session_neg}[/red] 不感兴趣  =  {session_total} 篇\n"
            f"  剩余待标   [bold]{remaining}[/bold] 篇\n\n"
            f"[dim]下次执行 [cyan]\\rate[/cyan] 自动跳过已标文章继续[/dim]",
            title="[yellow]任务暂停[/yellow]", border_style="yellow",
        ))
    else:
        console.print(Panel(
            f"[green]今日必需任务完成[/green]\n\n"
            f"  本次标注   [green]+{session_pos}[/green] 有价值  /  [red]-{session_neg}[/red] 不感兴趣  =  {session_total} 篇",
            title="[bold green]任务完成[/bold green]", border_style="green",
        ))
        store  = load_store()
        result = maybe_auto_train(store)
        _show_train_result(result, pos_count, neg_count, total_hist)


def _run_extra_rate(extra_pool: list[dict]) -> None:
    """Extra rate mode: label additional articles beyond the mandatory sample."""
    from fairing.trainer import load_feedback, save_feedback, maybe_auto_train
    from fairing.embedder import load_store

    # Newest first
    pool = sorted(extra_pool, key=lambda a: a.get("date", ""), reverse=True)

    feedback    = load_feedback()
    pos_count   = sum(1 for f in feedback if f["label"] ==  1)
    neg_count   = sum(1 for f in feedback if f["label"] == -1)
    total_hist  = len(feedback)
    session_pos = session_neg = 0
    total       = len(pool)
    session_map: dict[str, int | None] = {}

    cursor = 0
    while cursor < total:
        a      = pool[cursor]
        choice = _prompt_choice(a, cursor + 1, total,
                                session_pos, session_neg,
                                pos_count, neg_count, total_hist,
                                mode_tag="额外", can_go_back=cursor > 0)
        if choice == "s":
            break

        if choice == "p":
            cursor -= 1
            prev_url = pool[cursor]["url"]
            if prev_url in session_map:
                prev_label = session_map.pop(prev_url)
                if prev_label is not None:
                    pos_count   -= prev_label == 1
                    neg_count   -= prev_label == -1
                    session_pos -= prev_label == 1
                    session_neg -= prev_label == -1
                    total_hist  -= 1
            continue

        if choice in ("+", "-"):
            label = 1 if choice == "+" else -1
            save_feedback({
                "url":         a["url"],
                "title":       a.get("title", ""),
                "source":      a.get("source", ""),
                "label":       label,
                "label_index": total_hist,
                "date":        _today_beijing(),
            })
            session_map[a["url"]] = label
            pos_count   += label == 1
            neg_count   += label == -1
            session_pos += label == 1
            session_neg += label == -1
            total_hist  += 1
        else:
            session_map[a["url"]] = None
        cursor += 1

    session_total = session_pos + session_neg
    _clear()
    console.print(Panel(
        f"  本次标注   [green]+{session_pos}[/green] 有价值  /  "
        f"[red]-{session_neg}[/red] 不感兴趣  =  {session_total} 篇",
        title="[bold cyan]额外打标完成[/bold cyan]", border_style="cyan",
    ))
    if session_total > 0:
        store  = load_store()
        result = maybe_auto_train(store)
        _show_train_result(result, pos_count, neg_count, total_hist)


def _run_review_rate(store: dict, feedback: list[dict]) -> None:
    """Review/edit mode: flip through previously labeled articles and correct labels."""
    from fairing.trainer import save_feedback, maybe_auto_train
    from fairing.embedder import load_store as _reload_store

    # Newest first; only show articles whose text is in store
    review = sorted(
        [f for f in feedback if f["url"] in store],
        key=lambda f: f.get("label_index", 0),
        reverse=True,
    )
    if not review:
        console.print(Panel(
            "历史标注记录中没有可供复习的文章（文章文本未缓存）。",
            border_style="yellow",
        ))
        return

    pos_count  = sum(1 for f in feedback if f["label"] ==  1)
    neg_count  = sum(1 for f in feedback if f["label"] == -1)
    total_hist = len(feedback)
    changed    = 0
    total      = len(review)

    cursor = 0
    while cursor < total:
        f             = review[cursor]
        url           = f["url"]
        a             = dict(store[url])
        current_label = f["label"]

        choice = _prompt_choice(a, cursor + 1, total,
                                changed, 0,          # session_pos=changed, session_neg=0 (reused for "edits")
                                pos_count, neg_count, total_hist,
                                mode_tag="复习", current_label=current_label,
                                can_go_back=cursor > 0)
        if choice == "s":
            break

        if choice == "p":
            cursor -= 1
            continue

        if choice in ("+", "-"):
            new_label = 1 if choice == "+" else -1
            if new_label != current_label:
                save_feedback({
                    "url":         url,
                    "title":       f.get("title", a.get("title", "")),
                    "source":      f.get("source", a.get("source", "")),
                    "label":       new_label,
                    "label_index": total_hist,
                    "date":        _today_beijing(),
                })
                pos_count  += new_label == 1
                neg_count  += new_label == -1
                pos_count  -= current_label == 1
                neg_count  -= current_label == -1
                total_hist += 1
                changed    += 1
                f["label"] = new_label
        cursor += 1

    _clear()
    if changed:
        console.print(Panel(
            f"  修改记录   {changed} 条\n"
            f"  当前标注   [green]+{pos_count}[/green] 有价值  /  "
            f"[red]-{neg_count}[/red] 不感兴趣  =  {total_hist} 条",
            title="[bold green]复习修改完成[/bold green]", border_style="green",
        ))
        result = maybe_auto_train(_reload_store())
        _show_train_result(result, pos_count, neg_count, total_hist)
    else:
        console.print(Panel(
            f"  浏览记录   {total} 条\n"
            f"  [dim]未作任何修改[/dim]",
            title="[dim]复习完成[/dim]", border_style="dim",
        ))


# ── label entry editor ────────────────────────────────────────────────────────

def _edit_label_entry(entry: dict, store: dict) -> bool:
    """Show a card for an already-labeled article and allow changing its label.
    @return: True if the label was changed, False otherwise
    """
    from fairing.trainer import load_feedback, save_feedback
    url           = entry["url"]
    current_label = entry["label"]
    feedback      = load_feedback()
    pos_count     = sum(1 for f in feedback if f["label"] ==  1)
    neg_count     = sum(1 for f in feedback if f["label"] == -1)
    total_hist    = len(feedback)
    a = dict(entry)
    if url in store:
        a["text_for_scoring"] = store[url].get("text_for_scoring", "")
    choice = _prompt_choice(
        a, idx=1, total=1,
        session_pos=0, session_neg=0,
        pos_count=pos_count, neg_count=neg_count, total_hist=total_hist,
        mode_tag="修改标注",
        current_label=current_label,
        can_go_back=False,
    )
    if choice in ("+", "-"):
        new_label = 1 if choice == "+" else -1
        if new_label != current_label:
            save_feedback({
                "url":         url,
                "title":       entry.get("title", ""),
                "source":      entry.get("source", ""),
                "label":       new_label,
                "label_index": total_hist,
                "date":        _today_beijing(),
            })
            return True
    return False


# ── extended rate ─────────────────────────────────────────────────────────────

def _run_ext_rate() -> None:
    """Extended labeling: all unlabeled articles from title_index, newest first.
    No quota, no rate-gate. User quits with 's'.
    """
    from fairing.trainer import load_feedback, save_feedback, maybe_auto_train
    from fairing.embedder import load_store
    from fairing.paths import title_index_file, scoring_store_file

    feedback   = load_feedback()
    labeled    = {f["url"] for f in feedback}
    pos_count  = sum(1 for f in feedback if f["label"] ==  1)
    neg_count  = sum(1 for f in feedback if f["label"] == -1)
    total_hist = len(feedback)

    store = load_store()

    seen_aids: set[str] = set()
    pool: list[dict] = []

    index_src = title_index_file() if title_index_file().exists() else None
    fallback  = scoring_store_file() if not index_src and scoring_store_file().exists() else None
    src_file  = index_src or fallback

    if src_file:
        for line in src_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e   = json.loads(line)
                url = e.get("url", "")
                if index_src:
                    aid = e.get("article_id", "")
                else:
                    from fairing.export import article_id_for
                    aid = article_id_for(url)
                if not url or not aid or url in labeled or aid in seen_aids:
                    continue
                seen_aids.add(aid)
                a = dict(e)
                if url in store:
                    a["text_for_scoring"] = store[url].get("text_for_scoring", "")
                pool.append(a)
            except (json.JSONDecodeError, KeyError):
                continue

    pool.sort(key=lambda e: e.get("date", ""), reverse=True)

    if not pool:
        console.print(Panel(
            "没有未打标的文章。",
            border_style="dim",
        ))
        return

    console.print(Panel(
        f"  待标注   [bold]{len(pool)}[/bold] 篇  （按时间从新到旧）\n"
        f"  历史     [green]+{pos_count}[/green] / [red]-{neg_count}[/red]  共 {total_hist} 条",
        title="[cyan]扩展打标[/cyan]", border_style="cyan",
    ))

    session_pos = session_neg = 0
    session_map: dict[str, int | None] = {}
    quit_early  = False
    cursor      = 0

    while cursor < len(pool):
        a      = pool[cursor]
        choice = _prompt_choice(
            a, cursor + 1, len(pool),
            session_pos, session_neg,
            pos_count, neg_count, total_hist,
            can_go_back=(cursor > 0),
        )
        if choice == "s":
            quit_early = True
            break
        if choice == "p":
            cursor -= 1
            prev_url = pool[cursor]["url"]
            if prev_url in session_map:
                prev_label = session_map.pop(prev_url)
                if prev_label is not None:
                    pos_count   -= prev_label == 1
                    neg_count   -= prev_label == -1
                    session_pos -= prev_label == 1
                    session_neg -= prev_label == -1
                    total_hist  -= 1
            continue
        if choice in ("+", "-"):
            label = 1 if choice == "+" else -1
            save_feedback({
                "url":         a["url"],
                "title":       a.get("title", ""),
                "source":      a.get("source", ""),
                "label":       label,
                "label_index": total_hist,
                "date":        _today_beijing(),
            })
            session_map[a["url"]] = label
            pos_count   += label == 1
            neg_count   += label == -1
            session_pos += label == 1
            session_neg += label == -1
            total_hist  += 1
        else:
            session_map[a["url"]] = None
        cursor += 1

    session_total = session_pos + session_neg
    _clear()
    remaining = len(pool) - cursor
    if quit_early and remaining > 0:
        console.print(Panel(
            f"[yellow]标注已暂停[/yellow]\n\n"
            f"  本次标注   [green]+{session_pos}[/green] 有价值  /  "
            f"[red]-{session_neg}[/red] 不感兴趣  =  {session_total} 篇\n"
            f"  剩余可标   [bold]{remaining}[/bold] 篇",
            title="[yellow]暂停[/yellow]", border_style="yellow",
        ))
    else:
        console.print(Panel(
            f"[green]本轮标注完成[/green]\n\n"
            f"  本次标注   [green]+{session_pos}[/green] 有价值  /  "
            f"[red]-{session_neg}[/red] 不感兴趣  =  {session_total} 篇",
            title="[bold green]完成[/bold green]", border_style="green",
        ))
    if session_total > 0:
        result = maybe_auto_train(load_store())
        _show_train_result(result, pos_count, neg_count, total_hist)


# ── payload dispatch helper ───────────────────────────────────────────────────

def _dispatch_to_payload(a: dict, ask_label: bool = True) -> None:
    """Add article to payload queue with optional positive-label prompt."""
    from fairing.export import add_to_payload_queue, article_id_for
    from fairing.trainer import load_feedback, save_feedback

    url = a.get("url", "")
    aid = article_id_for(url)
    added = add_to_payload_queue(a)
    if added:
        console.print(f"  [magenta]✓ 已加入 payload 队列 [{aid}][/magenta]")
    else:
        console.print(f"  [dim]已在 payload 队列中 [{aid}][/dim]")

    if not ask_label:
        return

    feedback    = load_feedback()
    existing    = next((f for f in feedback if f["url"] == url), None)
    already_pos = existing and existing["label"] == 1
    already_neg = existing and existing["label"] == -1

    if already_pos:
        console.print("  [dim]已标注为有价值[/dim]")
        return

    prompt = "  改为有价值？[y/n] " if already_neg else "  同时标注为有价值？[y/n] "
    if input(prompt).strip().lower() == "y":
        feedback = load_feedback()
        save_feedback({
            "url":         url,
            "title":       a.get("title", ""),
            "source":      a.get("source", ""),
            "label":       1,
            "label_index": len(feedback),
            "date":        _today_beijing(),
        })
        console.print("  [green]✓ 已标注为有价值[/green]")


# ── shortcuts ─────────────────────────────────────────────────────────────────

def _show_shortcuts() -> None:
    # ── Shell commands ────────────────────────────────────────────────────────
    cmd_t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
    cmd_t.add_column("快捷键", style="cyan",     width=8)
    cmd_t.add_column("命令",   style="bold",     width=14)
    cmd_t.add_column("参数",   style="dim cyan", width=30)
    cmd_t.add_column("说明")
    _CMD_ROWS = [
        # shortcut    command           params                        description
        (r"\r",    "run",          "[--no-md] [--no-notebook] [--no-mail] [--chinese] [--force]",
                                                               "拉取 RSS · 嵌入评分 · 写文件 · 发邮件  （参数默认值见下方）"),
        ("",       "",             "",                          ""),
        (r"\rate", "rate",         "[--ext]",                   "必需打标（未完成阻塞 \\r）；--ext 扩展全量未标文章新→旧"),
        (r"\lb",   "labels",       "[英文关键词]",              "标注记录管理：搜索 · 翻页 · 修改"),
        ("",       "",             "",                          ""),
        (r"\ms",   "model_status", "",                          "分类器状态 · 训练进度 · 语义信号词"),
        (r"\rd",   "read",         "[N] [--zh]",                "列出今日文章；N 按编号抓全文阅读；--zh 附中文摘要"),
        (r"\re",   "resend",       "",                          "重建今日文章列表并强制重发邮件"),
        (r"\dl",   "remd",         "[--no-md] [--no-notebook]", "重建今日文件（不发邮件，供从设备同步使用）"),
        ("",       "",             "",                          ""),
        (r"\t",    "toggle",       "<N>",                       "按编号开启 / 关闭 RSS 源"),
        (r"\c",    "config",       "",                          "所有 RSS 源 · 7 天文章量 · 距上次收录时长"),
        (r"\e",    "env",          "[set KEY VALUE]",           "查看 / 修改 .env 变量"),
        (r"\l",    "log",          "",                          "历次运行记录"),
        (r"\bk",   "backup",       "",                          "手动触发数据备份"),
        (r"\rs",   "restore",      "",                          "从历史备份回档（差异对比 + 确认）"),
        ("",       "",             "",                          ""),
        (r"\pd",   "payload",      "[clear]",                   "查看全文下载队列；clear 清空"),
        (r"\ps",   "psearch",      "<英文关键词>",              "搜索文章并加入下载队列"),
        (r"\sd",   "send",         "<id> [id2 ...]",            "按 article_id（16 位）加入下载队列"),
        ("",       "",             "",                          ""),
        (r"\li",   "license",      "",                          "查看 MIT 许可证"),
        (r"\?  \h","shortcuts",    "",                          "显示本帮助"),
        (r"\q",    "quit",         "",                          "退出"),
    ]
    for shortcut, command, params, desc in _CMD_ROWS:
        cmd_t.add_row(shortcut, command, params, desc)
    console.print(Panel(cmd_t, title="[bold]Shell 命令[/bold]", border_style="cyan"))

    # ── run 参数 ─────────────────────────────────────────────────────────────
    d = _load_run_defaults()
    def _on(flag: bool, env_key: str) -> str:
        """Positive-logic display: green=on means the output IS being written."""
        state = "[green]开[/green]" if flag else "[red]关[/red]"
        return f"{state}  [dim]{env_key}[/dim]"
    def _off(flag: bool, env_key: str) -> str:
        """Negative-logic display: green=on means the modifier IS active."""
        state = "[green]开[/green]" if flag else "[dim]关[/dim]"
        return f"{state}  [dim]{env_key}[/dim]"

    args_t = Table(show_header=True, header_style="bold dim", box=box.SIMPLE, padding=(0, 1))
    args_t.add_column("参数",      style="cyan", width=16)
    args_t.add_column("说明")
    args_t.add_column("当前默认",  width=20)

    # Output toggles (positive logic, both on by default)
    args_t.add_row("--no-md",       "禁用 Obsidian 输出",                        _on(d["write_md"],       "RUN_MD"))
    args_t.add_row("--no-notebook", "禁用 NotebookLM 输出",                      _on(d["write_notebook"], "RUN_NOTEBOOK"))
    # Email modifiers
    args_t.add_row("",              "",                                            "")
    args_t.add_row("--no-mail",     "跳过发送邮件",                               _off(d["no_mail"],  "RUN_NO_MAIL"))
    args_t.add_row("--chinese",     "邮件发中文（MD/NotebookLM 保持英文）",       _off(d["chinese"],  "RUN_CHINESE"))

    console.print(Panel(args_t,
                        title="[bold dim]run 参数  （--no-md 与 --no-notebook 不可同时使用）[/bold dim]",
                        border_style="dim"))


# ── digest runner ─────────────────────────────────────────────────────────────

def _load_run_defaults() -> dict:
    """Read run parameter defaults from .env.

    Output flags use positive (enable) logic and default to on:
      RUN_MD        — write Obsidian vault output    (default: on; set 'false' to disable)
      RUN_NOTEBOOK  — write NotebookLM source file   (default: on; set 'false' to disable)

    Modifier flags default to off:
      RUN_CHINESE   — email in Chinese
      RUN_NO_MAIL   — skip email

    CLI flags (--no-md, --no-notebook, --no-mail, etc.) always override .env defaults.
    """
    def _bool(key: str) -> bool:
        return os.environ.get(key, "").strip().lower() in ("1", "true", "yes")

    def _bool_enabled(key: str) -> bool:
        """Return True (enabled) unless explicitly set to a falsy value."""
        val = os.environ.get(key, "").strip().lower()
        return val not in ("0", "false", "no")

    return {
        "chinese":        _bool("RUN_CHINESE"),
        "no_mail":        _bool("RUN_NO_MAIL"),
        "write_md":       _bool_enabled("RUN_MD"),
        "write_notebook": _bool_enabled("RUN_NOTEBOOK"),
    }



def run_digest(chinese: bool = False,
               no_mail: bool = False, force: bool = False,
               write_md: bool = True, write_notebook: bool = True) -> None:
    """Fetch, score, and deliver the daily digest end-to-end.

    @param chinese:        translate email to Chinese (MD and NotebookLM stay English)
    @param no_mail:        skip sending the digest email
    @param force:          bypass the rate-gate check (skip pending label warning)
    @param write_md:       write Obsidian vault output (default True)
    @param write_notebook: write NotebookLM source file (default True; also requires
                           NOTEBOOKLM_DIR to be configured)
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)

    if not _check_rate_gate(force):
        return

    elapsed_hours = _hours_since_last_run()
    if elapsed_hours > 24:
        logger.info("Dynamic lookback: %.1f h since last run (extending all windows)", elapsed_hours)

    from fairing.config import Config
    from fairing.rss import fetch_rss
    from fairing.embedder import enrich, load_store
    from fairing.scorer import score_articles
    from fairing.writer import write_obsidian, write_chinese, write_notebooklm, archive_vault
    from fairing.mailer import send_digest
    from fairing.state import filter_unseen, mark_seen

    cfg        = Config()
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    logger.info("=== Fetching RSS feeds ===")
    articles = fetch_rss(cfg.rss_sources, min_lookback_hours=elapsed_hours)
    if not articles:
        logger.warning("No articles collected.")
        return
    logger.info("Total collected: %d", len(articles))

    articles = filter_unseen(articles)
    if not articles:
        logger.info("No new articles after dedup.")
        return
    logger.info("Fresh: %d articles", len(articles))

    logger.info("=== Computing embeddings ===")
    articles = enrich(articles)
    articles = score_articles(articles)

    # Persist ordered article list for \rd command (score-sorted, same as email)
    from fairing.export import article_id_for as _aid
    LAST_RUN().write_text(json.dumps([
        {"idx": i, "url": a["url"], "article_id": _aid(a["url"]),
         "title": a.get("title", ""), "source": a.get("source", ""),
         "published": a.get("published", ""), "score": round(a.get("score", 0.0), 4)}
        for i, a in enumerate(articles, 1)
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    moved = archive_vault(cfg.obsidian_dir)
    if moved:
        logger.info("Archived %d note(s) into week folders", moved)

    # Write outputs. Both are on by default; each can be independently disabled.
    if write_notebook and cfg.notebooklm_dir:
        nlm = write_notebooklm(articles, cfg.notebooklm_dir)
        logger.info("NotebookLM (EN) -> %s", nlm)
    elif write_notebook and not cfg.notebooklm_dir:
        logger.warning("NotebookLM requested but NOTEBOOKLM_DIR is not configured — skipped")

    if write_md:
        path, count = write_obsidian(articles, cfg.obsidian_dir)
        logger.info("Obsidian (EN)   -> %s  [+%d]", path, count)

    mark_seen(articles)

    # Email: optionally translate to Chinese for the digest only.
    # Translation operates on a COPY — original articles are unmodified,
    # so training data (embeddings, seen_urls) always reflects English content.
    if no_mail:
        logger.info("Email skipped (--no-mail)")
    else:
        email_articles = articles
        if chinese:
            translator_key = os.environ.get("GEMINI_API_KEY", "")
            if not translator_key and os.environ.get("TRANSLATOR", "gemini") == "gemini":
                logger.warning("--chinese: GEMINI_API_KEY not set — email sent in English")
            else:
                from fairing.translator import translate
                import copy
                logger.info("=== Translating for email (EN→ZH) ===")
                email_articles = translate(copy.deepcopy(articles))
        send_digest(email_articles)

    # Merge any remaining un-labeled URLs from a previous incomplete batch so
    # that no selected article is silently dropped when \r runs a second time.
    existing_pending = _load_pending()
    prev_remaining: list[str] = []
    if existing_pending and not existing_pending.get("completed", True):
        done_set       = set(existing_pending.get("done_urls", []))
        prev_remaining = [u for u in existing_pending.get("sample_urls", [])
                         if u not in done_set]
        if prev_remaining:
            logger.info("Merging %d un-labeled URLs from previous pending batch",
                        len(prev_remaining))

    sample    = _sample_articles(articles)
    new_urls  = [a["url"] for a in sample]
    prev_set  = set(prev_remaining)
    merged    = prev_remaining + [u for u in new_urls if u not in prev_set]

    _save_pending({
        "run_date":    _today_beijing(),
        "sample_urls": merged,
        "completed":   False,
    })
    logger.info("Rate sample ready: %d articles (%d merged from previous) — run \\rate to label",
                len(merged), len(prev_remaining))

    from fairing.backup import run_backup
    run_backup()
    _save_last_run_time()


# ── interactive shell ─────────────────────────────────────────────────────────

class Shell(cmd.Cmd):
    prompt = f"{_CY}fairing{_R} {_DM}>{_R} "

    def preloop(self) -> None:
        _clear()
        console.print(Panel(
            f"[bold cyan]{LOGO}[/bold cyan]"
            f"\n"
            f"  [bold white]Wraps the noise, delivers the signal.[/bold white]\n"
            f"  [dim]A daily feed aggregator · tech blogs, newsletters, research → clean digest[/dim]\n"
            f"\n"
            f"  [dim]v{__version__}  JiekerTime <zhangjunjie@apache.org>  MIT[/dim]\n\n"
            f"  [cyan]\\?[/cyan] [dim]shortcuts[/dim]   [cyan]\\li[/cyan] [dim]license[/dim]",
            border_style="cyan", padding=(0, 1),
        ))

    def do_run(self, line: str) -> None:
        """Run the daily digest.

  Output (both enabled by default; disable per-run with --no-md / --no-notebook):
    --no-md         skip Obsidian vault output for this run
    --no-notebook   skip NotebookLM output for this run

  Modifiers (combine freely):
    --no-mail     skip email notification
    --chinese     email in Chinese (MD and NotebookLM stay English)
    --force       bypass rate gate (emergency use)"""
        _clear()
        args          = line.split()
        defaults      = _load_run_defaults()
        chinese       = "--chinese"     in args or (defaults["chinese"] and "--no-chinese" not in args)
        no_mail       = "--no-mail"     in args or (defaults["no_mail"] and "--mail" not in args)
        write_md       = defaults["write_md"]       and "--no-md"       not in args
        write_notebook = defaults["write_notebook"] and "--no-notebook" not in args
        force          = "--force" in args

        if not write_md and not write_notebook:
            console.print(Panel(
                "[red]--no-md 和 --no-notebook 不能同时使用[/red]\n"
                "两者都禁用会导致没有任何文件输出。",
                border_style="red",
            ))
            return

        try:
            run_digest(chinese=chinese,
                       no_mail=no_mail, force=force,
                       write_md=write_md, write_notebook=write_notebook)
        except Exception as exc:
            logger.error("Run failed: %s", exc)

    def do_rate(self, line: str) -> None:
        """Label articles for training.

  Usage:
    rate          mandatory daily batch — today's sampled articles (rate-gate)
    rate --ext    extended: all unlabeled articles newest-first (requires daily done)
                  English titles only; case-insensitive; no time-window limit"""
        _clear()
        if "--ext" in line.split():
            # Extended mode: check mandatory done first
            pending = _load_pending()
            if not pending:
                console.print(Panel(
                    "[yellow]今日尚无打标任务[/yellow]\n\n"
                    "请先运行 [cyan]\\r[/cyan] 拉取今日文章，完成 [cyan]\\rate[/cyan] 必需任务后再使用 [cyan]\\rate --ext[/cyan]。",
                    title="[yellow]不可用[/yellow]", border_style="yellow",
                ))
                return
            if not pending.get("completed", False):
                done  = len(pending.get("done_urls", []))
                total = len(pending.get("sample_urls", []))
                console.print(Panel(
                    f"[yellow]今日必需打标尚未完成[/yellow]  ({done}/{total} 篇)\n\n"
                    "请先执行 [cyan]\\rate[/cyan] 完成今日样本，再使用 [cyan]\\rate --ext[/cyan]。",
                    title="[yellow]不可用[/yellow]", border_style="yellow",
                ))
                return
            console.print(Panel(
                "  来源：全量历史未打标文章（英文标题，大小写不敏感）\n"
                "  排序：按发布时间从新到旧",
                title="[cyan]扩展打标[/cyan]", border_style="cyan",
            ))
            _run_ext_rate()
            return

        # Mandatory daily mode
        pending = _load_pending()
        if not pending:
            console.print(Panel(
                "当前没有待打标的文章。\n运行 [cyan]\\r[/cyan] 拉取新文章后再来。",
                border_style="yellow",
            ))
            return
        if pending.get("completed"):
            from fairing.trainer import load_feedback
            feedback  = load_feedback()
            today     = _today_beijing()
            today_pos = sum(1 for f in feedback if f.get("date") == today and f["label"] ==  1)
            today_neg = sum(1 for f in feedback if f.get("date") == today and f["label"] == -1)
            console.print(Panel(
                f"[green]今日必需打标已完成[/green]\n\n"
                f"  今日已标   [green]+{today_pos}[/green] 有价值  /  "
                f"[red]-{today_neg}[/red] 不感兴趣  =  {today_pos + today_neg} 篇\n\n"
                f"[dim]扩展打标：[cyan]\\rate --ext[/cyan]　查看 / 修改：[cyan]\\lb[/cyan][/dim]",
                title="[bold green]今日完成[/bold green]", border_style="green",
            ))
            return
        done      = set(pending.get("done_urls", []))
        total     = len(pending.get("sample_urls", []))
        remaining = total - len(done)
        if done:
            console.print(Panel(
                f"断点续标 — 继续未完成的今日样本\n\n"
                f"  剩余   [bold]{remaining}[/bold] / {total} 篇",
                title="[dim]继续[/dim]", border_style="dim",
            ))
        _run_rate(pending)

    def do_extra(self, _line: str) -> None:
        """Alias for 'rate --ext' (backward compat)."""
        self.do_rate("--ext")

    def do_labels(self, line: str) -> None:
        """Browse and edit labeled articles with search and pagination.

  Usage:
    labels                     show all labeled articles (newest first)
    labels <english keywords>  filter by English title keywords (case-insensitive)
  Navigation: <N> edit  n/p page  q quit"""
        _clear()
        from fairing.trainer import load_feedback, maybe_auto_train
        from fairing.embedder import load_store
        from fairing.export import article_id_for

        feedback = load_feedback()
        if not feedback:
            console.print(Panel("还没有标注记录。先执行 [cyan]\\rate[/cyan] 标注文章", border_style="dim"))
            return

        query = line.strip()
        words = query.lower().split() if query else []

        def _filter(pool):
            if not words:
                return pool
            return [f for f in pool if all(w in f.get("title", "").lower() for w in words)]

        PAGE_SIZE     = 20
        changed_total = 0
        page          = 0

        while True:
            feedback = load_feedback()
            pool     = sorted(_filter(feedback), key=lambda f: f.get("date", ""), reverse=True)
            if not pool:
                console.print(Panel(
                    f"[yellow]未找到英文标题含 \"{query}\" 的标注记录[/yellow]\n"
                    "[dim]搜索仅匹配英文标题，大小写不敏感，多个词均须出现[/dim]",
                    border_style="yellow",
                ))
                break
            total_pages = max(1, (len(pool) + PAGE_SIZE - 1) // PAGE_SIZE)
            page        = min(page, total_pages - 1)
            while True:
                _clear()
                start      = page * PAGE_SIZE
                page_items = pool[start:start + PAGE_SIZE]
                t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
                t.add_column("#",    style="dim",      width=4)
                t.add_column("标注", width=4)
                t.add_column("ID",   style="dim cyan", width=18)
                t.add_column("标题", style="cyan",     min_width=36)
                t.add_column("来源", width=16)
                t.add_column("日期", width=12)
                for i, f in enumerate(page_items, 1):
                    badge = "[green]+[/green]" if f["label"] == 1 else "[red]−[/red]"
                    aid   = article_id_for(f["url"])
                    t.add_row(str(i), badge, aid, f.get("title", "")[:50],
                              f.get("source", "")[:14], f.get("date", ""))
                title_suffix = f"  [{query}]" if query else ""
                console.print(Panel(
                    t,
                    title=f"[bold]标注记录{title_suffix}  [{len(pool)} 篇]  第 {page+1}/{total_pages} 页[/bold]",
                    border_style="cyan",
                ))
                nav = []
                if page < total_pages - 1: nav.append("[cyan]n[/cyan] 下页")
                if page > 0:               nav.append("[cyan]p[/cyan] 上页")
                nav.append("[dim]q[/dim] 退出")
                console.print("  输入编号修改标注  │  " + "  ".join(nav))
                raw = input("  > ").strip().lower()
                if raw in ("q", ""):
                    if changed_total > 0:
                        feedback = load_feedback()
                        result   = maybe_auto_train(load_store())
                        _show_train_result(
                            result,
                            sum(1 for f in feedback if f["label"] ==  1),
                            sum(1 for f in feedback if f["label"] == -1),
                            len(feedback),
                        )
                    return
                if raw == "n" and page < total_pages - 1:
                    page += 1; continue
                if raw == "p" and page > 0:
                    page -= 1; continue
                if raw.isdigit():
                    idx = int(raw) - 1
                    if 0 <= idx < len(page_items):
                        f = page_items[idx]
                        if _edit_label_entry(f, load_store()):
                            changed_total += 1
                            break
                        continue
                console.print(f"  [yellow]请输入编号或 n / p / q[/yellow]")

    def do_payload(self, line: str) -> None:
        """Show or manage the payload download queue.

  Usage:
    payload           list queued articles
    payload clear     empty the payload queue"""
        _clear()
        from fairing.export import load_payload_queue, _write_queue
        if line.strip() == "clear":
            _write_queue([])
            console.print(Panel("[yellow]payload 队列已清空[/yellow]", border_style="yellow"))
            return
        queue = load_payload_queue()
        if not queue:
            console.print(Panel(
                "payload 队列为空\n\n"
                "[dim]在 [cyan]\\rate[/cyan] / [cyan]\\rd[/cyan] 中按 [magenta]d[/magenta] 加入文章，"
                "或用 [cyan]\\ps <关键词>[/cyan] 搜索添加[/dim]",
                border_style="dim",
            ))
            return
        t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
        t.add_column("ID",   style="dim cyan", width=18)
        t.add_column("标题", style="cyan",     min_width=40)
        t.add_column("来源", width=20)
        t.add_column("日期", width=12)
        for e in queue:
            t.add_row(e["article_id"], e["title"][:60], e["source"][:20], e.get("queued_date", ""))
        console.print(Panel(
            t,
            title=f"[bold]payload 队列  [{len(queue)} 篇][/bold]",
            border_style="magenta",
        ))
        console.print("  [dim]使用 [cyan]payload clear[/cyan] 清空队列[/dim]")

    def do_psearch(self, line: str) -> None:
        """Search all known articles by English title and add to payload queue.

  Usage:
    psearch <english keyword(s)>

  English titles only; case-insensitive; all keywords must appear in title."""
        _clear()
        query = line.strip()
        if not query:
            console.print(Panel(
                "[yellow]用法: psearch <英文关键词>[/yellow]\n"
                "[dim]大小写不敏感 · 多个词均须出现在标题中 · 仅支持英文标题[/dim]",
                border_style="yellow",
            ))
            return
        from fairing.export import search_by_title, add_to_payload_queue
        results = search_by_title(query)
        if not results:
            console.print(Panel(
                f"[yellow]未找到英文标题含 \"{query}\" 的文章[/yellow]\n"
                "[dim]搜索仅匹配英文标题，大小写不敏感，多个词均须出现[/dim]",
                border_style="yellow",
            ))
            return
        shown = results[:30]
        t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
        t.add_column("#",    style="dim",      width=4)
        t.add_column("ID",   style="dim cyan", width=18)
        t.add_column("标题", style="cyan",     min_width=40)
        t.add_column("来源", width=20)
        for i, a in enumerate(shown, 1):
            t.add_row(str(i), a["article_id"], a["title"][:60], a["source"][:20])
        suffix = f"（显示前 30，共 {len(results)} 条）" if len(results) > 30 else ""
        console.print(Panel(
            t,
            title=f"[bold]搜索结果: \"{query}\"  [{len(results)} 篇]{suffix}[/bold]",
            border_style="blue",
        ))
        console.print("  输入编号选择文章（如 [cyan]1[/cyan] 或 [cyan]1 3 5[/cyan]），回车取消：", end="")
        raw = input(" ").strip()
        if not raw:
            return
        selected = []
        for token in raw.split():
            if token.isdigit():
                idx = int(token) - 1
                if 0 <= idx < len(shown):
                    selected.append(shown[idx])
        if not selected:
            return
        console.print(f"\n  已选择 [bold]{len(selected)}[/bold] 篇：")
        for a in selected:
            console.print(f"    [{a['article_id']}] [cyan]{a['title'][:55]}[/cyan]  [dim]{a['source']}[/dim]")
        confirm = input("\n  确认加入 payload 队列？[y/n] ").strip().lower()
        if confirm != "y":
            console.print("  [dim]已取消[/dim]")
            return
        label_all = input("  同时将所有选中文章标注为有价值？[y/n] ").strip().lower() == "y"
        for a in selected:
            _dispatch_to_payload(a, ask_label=False)
            if label_all:
                from fairing.trainer import load_feedback, save_feedback
                feedback = load_feedback()
                existing = next((f for f in feedback if f["url"] == a.get("url", "")), None)
                if not (existing and existing["label"] == 1):
                    save_feedback({
                        "url":         a.get("url", ""),
                        "title":       a.get("title", ""),
                        "source":      a.get("source", ""),
                        "label":       1,
                        "label_index": len(feedback),
                        "date":        _today_beijing(),
                    })
                    console.print(f"  [green]✓ 已标注有价值:[/green] {a['title'][:50]}")

    def do_send(self, line: str) -> None:
        """Add article(s) to payload queue by article_id.

  Usage:
    send <article_id> [article_id2 ...]

  article_id is the 16-char hex ID shown in \\rate, \\rd, and \\ps."""
        _clear()
        ids = line.split()
        if not ids:
            console.print(Panel(
                "[yellow]用法: send <article_id> [article_id2 ...][/yellow]\n"
                "[dim]article_id 在 [cyan]\\rate[/cyan]、[cyan]\\rd[/cyan]、[cyan]\\ps[/cyan] 中显示[/dim]",
                border_style="yellow",
            ))
            return
        from fairing.export import find_by_id
        for aid in ids:
            a = find_by_id(aid)
            if a is None:
                console.print(f"  [yellow]未找到 id={aid}[/yellow]")
                continue
            console.print(
                f"\n  [{aid}] [cyan]{a['title'][:60]}[/cyan]\n"
                f"  [dim]{a['source']}  {a.get('date', '')}[/dim]"
            )
            confirm = input("  加入 payload 队列？[y/n] ").strip().lower()
            if confirm != "y":
                console.print("  [dim]已跳过[/dim]")
                continue
            _dispatch_to_payload(a, ask_label=True)

    def do_license(self, _line: str) -> None:
        """Show the MIT license."""
        _clear()
        import pathlib as _pl
        license_path = _pl.Path(__file__).parent / "LICENSE"
        if license_path.exists():
            text = license_path.read_text(encoding="utf-8")
        else:
            text = (
                "MIT License\n\n"
                "Copyright (c) 2026 ruoyitalk\n\n"
                "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
                "of this software and associated documentation files (the \"Software\"), to deal\n"
                "in the Software without restriction, including without limitation the rights\n"
                "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
                "copies of the Software, and to permit persons to whom the Software is\n"
                "furnished to do so, subject to the following conditions:\n\n"
                "The above copyright notice and this permission notice shall be included in all\n"
                "copies or substantial portions of the Software.\n\n"
                "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND."
            )
        console.print(Panel(text.strip(), title="[bold]MIT License[/bold]", border_style="dim"))

    def do_toggle(self, line: str) -> None:
        """Enable or disable an RSS source by its index number in \\c.

  Usage:  toggle <N>"""
        _clear()
        parts = line.strip().split()
        if not parts or not parts[0].isdigit():
            console.print(Panel(
                "用法：[cyan]toggle <N>[/cyan]\n"
                "先执行 [cyan]\\c[/cyan] 查看来源编号，再用此命令开关。",
                border_style="dim",
            ))
            return

        # Build global numbered list of all sources (public + private)
        all_rss: list[dict] = []
        for yaml_path in (PUBLIC_YAML, LOCAL_YAML):
            data = _load_yaml(yaml_path)
            all_rss.extend(data.get("rss", []))

        n = int(parts[0])
        if not (1 <= n <= len(all_rss)):
            console.print(Panel(
                f"[yellow]编号 {n} 不存在[/yellow]  有效范围：1 – {len(all_rss)}",
                border_style="yellow",
            ))
            return

        name = all_rss[n - 1].get("name", "")

        # Toggle in sources.local.yaml's disabled list
        local_data = _load_yaml(LOCAL_YAML)
        disabled: list[str] = local_data.get("disabled", [])
        if name in disabled:
            disabled.remove(name)
            now_enabled = True
        else:
            disabled.append(name)
            now_enabled = False
        local_data["disabled"] = disabled

        import yaml as _yaml
        LOCAL_YAML.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_YAML.write_text(
            _yaml.dump(local_data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        state_str = "[green]已启用[/green]" if now_enabled else "[red]已禁用[/red]"
        console.print(Panel(
            f"  来源   [cyan]{name}[/cyan]\n"
            f"  状态   {state_str}\n\n"
            f"[dim]下次 [cyan]\\r[/cyan] 运行时生效[/dim]",
            title="[bold]订阅源开关[/bold]", border_style="cyan",
        ))

    def do_config(self, _line: str) -> None:
        """Show all configured feed sources."""
        _clear()
        _show_sources()

    def do_env(self, line: str) -> None:
        """View or update .env variables.
  env                  show all (sensitive fields masked)
  env set KEY VALUE    update a key"""
        _clear()
        parts = line.split(maxsplit=2)
        if not parts:
            _show_env()
        elif parts[0] == "set" and len(parts) == 3:
            _set_env(parts[1], parts[2])
        else:
            console.print("[yellow]Usage: env  |  env set KEY VALUE[/yellow]")

    def do_log(self, _line: str) -> None:
        """Show run history."""
        _clear()
        _show_log()

    def do_model_status(self, _line: str) -> None:
        """Show model parameters, label statistics, and TF-IDF signals."""
        _clear()
        _show_model_status()

    def do_read(self, line: str) -> None:
        """Deep-read an article by index number from the last run.

  Usage:
    read            list today's articles with index numbers
    read <N>        open article #N in $EDITOR and save readnote
    read <N> --zh   same, with Chinese translation prepended"""
        from dotenv import load_dotenv
        load_dotenv(override=True)
        _clear()

        # ── Load last-run article list ────────────────────────────────────────
        if not LAST_RUN().exists():
            console.print(Panel(
                "还没有运行记录。先执行 [cyan]\\r[/cyan] 拉取文章。",
                border_style="yellow",
            ))
            return
        articles = json.loads(LAST_RUN().read_text(encoding="utf-8"))
        if not articles:
            console.print(Panel("上次运行没有文章。", border_style="yellow"))
            return

        args      = line.split()
        translate = "--zh" in args
        idx_args  = [a for a in args if not a.startswith("--")]

        # ── No argument: show numbered list ───────────────────────────────────
        if not idx_args:
            from fairing.export import article_id_for, load_payload_queue
            queued_ids = {e["article_id"] for e in load_payload_queue()}
            t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
            t.add_column("#",    style="dim",       width=4)
            t.add_column("ID",   style="dim cyan",  width=18)
            t.add_column("标题", style="cyan",      min_width=36)
            t.add_column("来源", width=18)
            t.add_column("分数", justify="right",   width=6)
            for a in articles:
                score_str = f"{a['score']:.2f}" if a.get("score") else "—"
                aid       = article_id_for(a["url"])
                id_cell   = f"[magenta]{aid}[/magenta]" if aid in queued_ids else aid
                t.add_row(str(a["idx"]), id_cell, a["title"][:50], a["source"][:18], score_str)
            console.print(Panel(t, title="[bold]上次运行文章列表[/bold]", border_style="blue"))
            console.print(
                "  [dim]使用 [cyan]read <N>[/cyan] 详读，[cyan]read <N> --zh[/cyan] 翻译后详读，"
                "[magenta]send <ID>[/magenta] 加入 payload 队列[/dim]"
            )
            return

        # ── Validate index ────────────────────────────────────────────────────
        raw = idx_args[0]
        if not raw.isdigit():
            console.print(Panel(
                f"[yellow]{raw!r} 不是有效编号[/yellow]\n"
                f"直接输入 [cyan]read[/cyan] 查看文章列表",
                border_style="yellow",
            ))
            return
        n = int(raw)
        matched = [a for a in articles if a["idx"] == n]
        if not matched:
            console.print(Panel(
                f"[yellow]编号 {n} 不存在[/yellow]  有效范围：1 – {len(articles)}",
                border_style="yellow",
            ))
            return
        article = matched[0]
        url     = article["url"]
        title   = article["title"]
        source  = article["source"]

        # ── Fetch, display, save ──────────────────────────────────────────────
        from fairing.reader import read_article, save_readnote
        console.print(f"  [dim]抓取 #{n} {title[:60]} ...[/dim]")
        content = read_article(url, title=title, translate=translate)

        if content is not None:
            # Determine readnotes dir: READNOTES_DIR env → OBSIDIAN_DIR/readnotes/
            import os as _os
            readnotes_raw = _os.environ.get("READNOTES_DIR", "")
            if readnotes_raw:
                readnotes_dir = Path(readnotes_raw).expanduser()
            else:
                from fairing.config import Config
                readnotes_dir = Path(Config().obsidian_dir) / "readnotes"
            note_path = save_readnote(
                url=url, title=title, content=content,
                source=source, readnotes_dir=readnotes_dir,
                translated=translate,
            )
            console.print(Panel(
                f"  文章   [cyan]#{n}[/cyan]  {title[:60]}\n"
                f"  落盘   [dim]{note_path}[/dim]",
                title="[bold green]详读完成[/bold green]", border_style="green",
            ))

    def do_remd(self, _line: str) -> None:
        """Rebuild today's Obsidian/NotebookLM files without sending email.

        Useful on secondary devices: pull the latest articles from the sync
        directory and regenerate output files locally. No email is sent.
        """
        from dotenv import load_dotenv
        load_dotenv(override=True)
        _clear()

        from fairing.embedder import load_store
        from fairing.state import normalize_url as _norm, today_beijing
        from fairing.scorer import score_articles
        from fairing.config import Config
        from fairing.writer import write_obsidian, write_notebooklm, archive_vault

        if not SEEN_URLS().exists():
            console.print(Panel("还没有运行记录。先执行 [cyan]\\r[/cyan]。", border_style="yellow"))
            return

        today     = today_beijing()
        seen_data = json.loads(SEEN_URLS().read_text(encoding="utf-8"))
        today_val = seen_data.get(today, {})
        today_norms = set(today_val.get("urls", []) if isinstance(today_val, dict) else today_val)
        if not today_norms:
            console.print(Panel(f"今日（{today}）暂无文章。", border_style="yellow"))
            return

        store   = load_store()
        cfg     = Config()
        src_cat = {s.name: s.category for s in cfg.rss_sources}
        articles = []
        for url, entry in store.items():
            if _norm(url) in today_norms:
                articles.append({
                    "url":       url,
                    "title":     entry.get("title", ""),
                    "source":    entry.get("source", ""),
                    "published": entry.get("date", ""),
                    "excerpt":   entry.get("text_for_scoring", "")[:400],
                    "category":  src_cat.get(entry.get("source", ""), ""),
                    "embedding": entry.get("embedding"),
                })

        if not articles:
            console.print(Panel("无法从缓存重建今日文章。", border_style="yellow"))
            return

        articles = score_articles(articles)
        moved = archive_vault(cfg.obsidian_dir)

        written = []
        if cfg.notebooklm_dir:
            nlm = write_notebooklm(articles, cfg.notebooklm_dir)
            written.append(f"NotebookLM → {nlm}")
        path, count = write_obsidian(articles, cfg.obsidian_dir)
        written.append(f"Obsidian   → {path}  [+{count}]")

        console.print(Panel(
            f"  今日文章   [bold]{len(articles)}[/bold] 篇\n"
            + "\n".join(f"  {w}" for w in written)
            + (f"\n  归档       {moved} 个历史文件" if moved else ""),
            title="[bold green]文件已重建[/bold green]", border_style="green",
        ))

    def do_resend(self, line: str) -> None:
        """Rebuild today's full article list from seen_urls + store and re-send email.

  Usage:
    resend          send consolidated digest of all today's articles
    resend --zh     same, with Chinese translation"""
        from dotenv import load_dotenv
        load_dotenv(override=True)
        _clear()

        from fairing.embedder import load_store
        from fairing.state import normalize_url as _norm, today_beijing
        from fairing.scorer import score_articles
        from fairing.mailer import send_digest
        from fairing.config import Config

        if not SEEN_URLS().exists():
            console.print(Panel("还没有运行记录。先执行 [cyan]\\r[/cyan]。",
                                border_style="yellow"))
            return

        today     = today_beijing()
        seen_data = json.loads(SEEN_URLS().read_text(encoding="utf-8"))
        today_val = seen_data.get(today, {})
        today_norms = set(today_val.get("urls", []) if isinstance(today_val, dict) else today_val)
        if not today_norms:
            console.print(Panel(f"今日（{today}）暂无文章。", border_style="yellow"))
            return

        # Rebuild article dicts from scoring_store
        store    = load_store()
        cfg      = Config()
        src_cat  = {s.name: s.category for s in cfg.rss_sources}
        articles = []
        for url, entry in store.items():
            if _norm(url) in today_norms:
                articles.append({
                    "url":       url,
                    "title":     entry.get("title", ""),
                    "source":    entry.get("source", ""),
                    "published": entry.get("date", ""),
                    "excerpt":   entry.get("text_for_scoring", "")[:400],
                    "category":  src_cat.get(entry.get("source", ""), ""),
                    "embedding": entry.get("embedding"),
                })
        if not articles:
            console.print(Panel("无法从缓存重建今日文章。", border_style="yellow"))
            return

        articles = score_articles(articles)

        args      = line.split()
        translate = "--zh" in args
        email_articles = articles
        if translate:
            translator_key = os.environ.get("GEMINI_API_KEY", "")
            if translator_key:
                from fairing.translator import translate as _translate
                import copy
                console.print("  [dim]正在翻译...[/dim]")
                email_articles = _translate(copy.deepcopy(articles))
            else:
                console.print("  [yellow]GEMINI_API_KEY 未配置，以英文发送[/yellow]")

        console.print(
            f"  [dim]汇总今日文章 [bold]{len(articles)}[/bold] 篇，强制发送...[/dim]"
        )
        send_digest(email_articles, force=True)
        console.print(Panel(
            f"  今日文章   [bold]{len(articles)}[/bold] 篇\n"
            f"  来自       {len({a['source'] for a in articles})} 个来源\n"
            f"  翻译       {'中文' if translate else '英文（原文）'}",
            title="[bold green]邮件已发送[/bold green]", border_style="green",
        ))

    def do_restore(self, _line: str) -> None:
        """Restore data files from a historical backup snapshot."""
        _clear()
        from fairing.backup import list_backups, diff_summary, restore_backup
        backups = list_backups()
        if not backups:
            console.print(Panel("没有可用的备份。先运行 [cyan]\\r[/cyan] 生成备份。",
                                border_style="yellow"))
            return

        # ── 列出可用备份 ─────────────────────────────────────────────────────
        bk_t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
        bk_t.add_column("#",    style="dim",  width=4)
        bk_t.add_column("日期", style="cyan")
        for i, d in enumerate(backups, 1):
            bk_t.add_row(str(i), d)
        console.print(Panel(bk_t, title="[bold]可用备份[/bold]", border_style="blue"))
        console.print("  输入序号选择回档日期，其他任意键取消：")
        choice = input("  > ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(backups)):
            console.print(Panel("[dim]已取消[/dim]", border_style="dim"))
            return
        date_str = backups[int(choice) - 1]

        # ── 完全一致则自动取消 ────────────────────────────────────────────────
        from fairing.backup import all_identical as _all_identical
        if _all_identical(date_str):
            console.print(Panel(
                f"当前数据与 [cyan]{date_str}[/cyan] 备份完全一致（MD5 逐文件核验）。\n"
                f"无需回档，已自动取消。",
                title="[dim]无差异[/dim]", border_style="dim",
            ))
            return

        # ── 展示差异 ─────────────────────────────────────────────────────────
        diffs = diff_summary(date_str)
        diff_t = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1))
        diff_t.add_column("文件",       style="cyan")
        diff_t.add_column("当前",       justify="right")
        diff_t.add_column("备份",       justify="right")
        diff_t.add_column("差值",       justify="right")
        for d in diffs:
            cur  = d["current_lines"] if d["current_lines"] is not None else f"{d['current_size']//1024} KB"
            bak  = d["backup_lines"]  if d["backup_lines"]  is not None else f"{d['backup_size']//1024} KB"
            if d["current_lines"] is not None and d["backup_lines"] is not None:
                delta = d["backup_lines"] - d["current_lines"]
                delta_str = (f"[green]+{delta}[/green]" if delta > 0
                             else f"[red]{delta}[/red]"  if delta < 0
                             else "[dim]±0[/dim]")
            else:
                delta_str = "[dim]—[/dim]"
            if not d["current_exists"]:
                cur = "[dim]不存在[/dim]"
            if not d["backup_exists"]:
                bak = "[dim]不存在[/dim]"
                delta_str = "[dim]—[/dim]"
            diff_t.add_row(d["name"], str(cur), str(bak), delta_str)
        console.print(Panel(
            diff_t,
            title=f"[bold yellow]回档差异  {date_str}[/bold yellow]",
            border_style="yellow",
        ))

        # ── 确认 ─────────────────────────────────────────────────────────────
        console.print(Panel(
            f"[yellow]回档将用 {date_str} 的备份覆盖当前数据文件。[/yellow]\n"
            f"当前数据将被替换，此操作不可撤销。\n\n"
            f"输入 [cyan]yes[/cyan] 确认回档，其他任意键取消：",
            title="[bold red]确认回档[/bold red]", border_style="red",
        ))
        confirm = input("  > ").strip().lower()
        if confirm != "yes":
            console.print(Panel("[dim]已取消，当前数据未改动[/dim]", border_style="dim"))
            return
        restored = restore_backup(date_str)
        console.print(Panel(
            f"[green]回档完成[/green]  共恢复 [bold]{len(restored)}[/bold] 个文件\n"
            + "\n".join(f"  · {f}" for f in restored),
            title="[bold green]回档成功[/bold green]", border_style="green",
        ))

    def do_backup(self, _line: str) -> None:
        """Manually trigger a backup of all data files."""
        _clear()
        from fairing.backup import run_backup, backup_dir
        dest, files = run_backup()
        if files:
            t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
            t.add_column("File", style="cyan")
            for f in files:
                t.add_row(f)
            console.print(Panel(
                t,
                title=f"[bold green]备份完成[/bold green]  →  {dest}",
                border_style="green",
            ))
            console.print(f"  [dim]保留最近 7 天，备份目录：{backup_dir()}[/dim]")
        else:
            console.print("[yellow]No data files found to back up.[/yellow]")

    def do_shortcuts(self, _line: str) -> None:
        r"""Show all shortcuts and key bindings (\r, \rate, \ms, \c, \e, \l, \h, \?, \q)."""
        _clear()
        _show_shortcuts()

    def do_help(self, _line: str) -> None:
        """Show help (same as shortcuts)."""
        _clear()
        _show_shortcuts()

    def do_exit(self, _line: str) -> bool:
        """Exit fairing."""
        console.print("[dim]bye[/dim]")
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
        console.print(f"  [yellow]Unknown:[/yellow] {line!r}  (type [cyan]\\?[/cyan])")

    def emptyline(self) -> None:
        pass


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from dotenv import load_dotenv
        load_dotenv(override=True)
        args           = sys.argv[2:]
        defaults       = _load_run_defaults()
        chinese        = "--chinese"  in args or (defaults["chinese"] and "--no-chinese" not in args)
        no_mail        = "--no-mail"  in args or (defaults["no_mail"] and "--mail" not in args)
        write_md       = defaults["write_md"]       and "--no-md"       not in args
        write_notebook = defaults["write_notebook"] and "--no-notebook" not in args
        force          = "--force" in args
        if not write_md and not write_notebook:
            console.print("[red]Error:[/red] --no-md and --no-notebook cannot both be set")
            sys.exit(1)
        run_digest(chinese=chinese,
                   no_mail=no_mail, force=force,
                   write_md=write_md, write_notebook=write_notebook)
        return
    Shell().cmdloop()


if __name__ == "__main__":
    main()
