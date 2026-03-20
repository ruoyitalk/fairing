"""Send a digest summary email via 163 SMTP after each run.

Dedup logic: MD5 hash of today's article URLs is stored in .digest_hash.
If the hash matches the previous send, the email is skipped to avoid spam.

Large digests are split into multiple emails (configurable via MAIL_SPLIT_N).
"""
import hashlib
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_TZ_BEIJING = timezone(timedelta(hours=8))
_DEFAULT_SPLIT_N = 60


def _today_beijing() -> str:
    return datetime.now(_TZ_BEIJING).date().isoformat()

logger = logging.getLogger(__name__)

from .paths import digest_hash_file as _digest_hash_file


def _article_hash(articles: list[dict]) -> str:
    """Stable MD5 of today's article raw content (url + title + excerpt).

    Hashing raw content (not just URLs) ensures a new email is triggered
    when article text changes, not only when the article set changes.
    """
    today = _today_beijing()
    parts = sorted(
        f"{a['url']}|{a['title']}|{a.get('excerpt', '')}"
        for a in articles
    )
    key = today + "|" + "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()


def _load_last_hash() -> str:
    if _digest_hash_file().exists():
        return _digest_hash_file().read_text().strip()
    return ""


def _save_hash(h: str) -> None:
    _digest_hash_file().write_text(h)


def _plain_text(raw: str) -> str:
    """Strip markdown syntax to get plain text safe for HTML insertion."""
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", raw)   # remove images ![](url)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)  # [text](url) → text
    text = re.sub(r"[\\*_`#>|]", "", text)             # remove markdown punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _article_row_full(a: dict, idx: int, article_id: str = "") -> str:
    # Use Chinese title/summary when available (--chinese email mode);
    # fall back to English originals. MD and NotebookLM are never affected.
    title     = a.get("title_zh") or a["title"]
    excerpt   = _plain_text(a.get("summary_zh") or a.get("excerpt", ""))
    published = a.get("published", "")
    pub_html  = (f"<span style='color:#bbb;font-size:11px;margin-left:8px'>{published}</span>"
                 if published and published != "unknown" else "")
    exc_html  = (f"<p style='margin:4px 0 0;color:#666;font-size:13px;line-height:1.5'>{excerpt}</p>"
                 if excerpt else "")
    num_html  = (f"<span style='color:#bbb;font-size:11px;font-weight:normal;"
                 f"margin-right:6px'>#{idx}</span>")
    id_html = (f"<code style='color:#bbb;font-size:10px;font-family:monospace;"
               f"margin-right:6px;background:#f5f5f5;padding:1px 4px;border-radius:3px'>"
               f"{article_id}</code>"
               if article_id else "")
    return (f"<div style='margin-bottom:14px;padding-left:4px'>"
            f"{num_html}{id_html}"
            f"<a href='{a['url']}' style='color:#1a73e8;font-size:14px;font-weight:500'>"
            f"{title}</a>{pub_html}{exc_html}</div>")


def _build_html(articles: list[dict], today: str,
                rank_offset: int = 0, part: int = 1, total_parts: int = 1) -> str:
    from .writer import top_n
    from .export import article_id_for

    scored   = any("score" in a for a in articles)
    n        = top_n(len(articles))
    featured = articles[:n] if scored else articles
    rest     = articles[n:]  if scored else []

    # Pre-compute article_id for every article in this batch
    ids_of = {a["url"]: article_id_for(a["url"]) for a in articles}

    summary = (f"{len(featured)} 篇全文 + {len(rest)} 篇标题"
               if scored else f"{len(articles)} 篇")

    rows: list[str] = []

    # Build a global index map: article url -> sequential rank (1-based, score order)
    all_articles = list(featured) + list(rest)
    rank_of = {a["url"]: rank_offset + i for i, a in enumerate(all_articles, 1)}

    # Part indicator in header when sending multiple emails
    part_label = f" ({part}/{total_parts})" if total_parts > 1 else ""

    # Featured: grouped by source, full detail
    by_source: dict[str, list[dict]] = {}
    for a in featured:
        by_source.setdefault(a["source"], []).append(a)
    for source, items in by_source.items():
        cat = items[0]["category"]
        rows.append(
            f"<h3 style='color:#444;margin:24px 0 8px;padding-bottom:4px;"
            f"border-bottom:1px solid #eee'>{source}"
            f"<span style='color:#aaa;font-weight:normal;font-size:12px;"
            f"margin-left:8px'>{cat}</span></h3>"
        )
        for a in items:
            rows.append(_article_row_full(a, rank_of[a["url"]], article_id=ids_of[a["url"]]))

    # Rest: compact title list
    if rest:
        rows.append("<hr style='margin:24px 0;border:none;border-top:1px solid #eee'>")
        rows.append(f"<p style='color:#888;font-size:12px'>其余 {len(rest)} 篇</p><ul "
                    f"style='padding-left:20px;color:#666;font-size:13px'>")
        for a in rest:
            aid = ids_of[a["url"]]
            rows.append(
                f"<li style='margin:3px 0'>"
                f"<span style='color:#ccc;font-size:11px'>#{rank_of[a['url']]}</span> "
                f"<code style='color:#bbb;font-size:10px;font-family:monospace;"
                f"background:#f5f5f5;padding:1px 4px;border-radius:3px'>{aid}</code> "
                f"<a href='{a['url']}' style='color:#555'>{a['title']}</a>"
                f" <span style='color:#bbb'>· {a['source']}</span></li>"
            )
        rows.append("</ul>")

    # Subject prefix and header are kept stable for email filter rules.
    # Filter on: subject starts with "[fairing]"
    return f"""
<html><body style="font-family:Arial,sans-serif;max-width:720px;margin:auto;padding:24px;color:#333">
  <h2 style="border-bottom:2px solid #1a73e8;padding-bottom:10px;margin-bottom:6px">
    fairing Daily Digest{part_label}
  </h2>
  <p style="color:#888;margin:0 0 20px;font-size:13px">
    {today} · {len(articles)} articles · {len(by_source)} sources
  </p>
  {"".join(rows)}
  <hr style="margin-top:32px;border:none;border-top:1px solid #eee">
  <p style="color:#ccc;font-size:11px;margin-top:8px">
    [fairing] · JiekerTime
  </p>
</body></html>
"""


def _split_batches(articles: list[dict], split_n: int) -> list[list[dict]]:
    """Split articles into batches of at most split_n each."""
    if split_n <= 0 or len(articles) <= split_n:
        return [articles]
    return [articles[i:i + split_n] for i in range(0, len(articles), split_n)]


def _send_one(msg: MIMEMultipart, host: str, port: int,
              user: str, password: str, mail_to: str) -> None:
    """Send a single MIME message via SMTP_SSL."""
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        server.login(user, password)
        server.sendmail(user, mail_to, msg.as_string())


def send_digest(articles: list[dict], force: bool = False) -> None:
    """Send digest email, skipping if content hash matches the previous send.

    Only sends when SMTP_USER / SMTP_PASSWORD / MAIL_TO are all configured.
    SMTP_PASSWORD must be the 163 authorization code (授权码), not the login password.
    Large digests are split into multiple emails of at most MAIL_SPLIT_N articles each.

    @param articles: full article list for the current run
    @param force:    if True, bypass the duplicate-hash check and always send
    """
    host     = os.environ.get("SMTP_HOST", "smtp.163.com")
    port     = int(os.environ.get("SMTP_PORT", "465"))
    user     = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    mail_to  = os.environ.get("MAIL_TO", "")
    split_n  = int(os.environ.get("MAIL_SPLIT_N", str(_DEFAULT_SPLIT_N)))

    if not all([user, password, mail_to]):
        logger.debug("Email not configured — skipping")
        return

    current_hash = _article_hash(articles)
    if not force and current_hash == _load_last_hash():
        logger.warning("EMAIL SKIPPED — content identical to previous send "
                       "(MD5: %s)", current_hash[:8])
        return

    today   = _today_beijing()
    batches = _split_batches(articles, split_n)
    n_parts = len(batches)
    rank_offset = 0

    for part_idx, batch in enumerate(batches, 1):
        subject = (f"[fairing] {today}" if n_parts == 1
                   else f"[fairing] {today} ({part_idx}/{n_parts})")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = mail_to
        msg.attach(MIMEText(
            _build_html(batch, today, rank_offset=rank_offset,
                        part=part_idx, total_parts=n_parts),
            "html", "utf-8",
        ))
        try:
            _send_one(msg, host, port, user, password, mail_to)
            logger.info("Email part %d/%d sent → %s  [MD5: %s]",
                        part_idx, n_parts, mail_to, current_hash[:8])
        except Exception as exc:
            logger.warning("Email part %d/%d failed: %s", part_idx, n_parts, exc)
            return
        rank_offset += len(batch)

    _save_hash(current_hash)
