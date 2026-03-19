"""Send a digest summary email via 163 SMTP after each run.

Dedup logic: MD5 hash of today's article URLs is stored in .digest_hash.
If the hash matches the previous send, the email is skipped to avoid spam.
"""
import hashlib
import logging
import os
import re
import smtplib
import ssl
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

_HASH_FILE = Path(__file__).parent.parent / ".digest_hash"


def _article_hash(articles: list[dict]) -> str:
    """Stable MD5 of today's article raw content (url + title + excerpt).

    Hashing raw content (not just URLs) ensures a new email is triggered
    when article text changes, not only when the article set changes.
    """
    today = date.today().isoformat()
    parts = sorted(
        f"{a['url']}|{a['title']}|{a.get('excerpt', '')}"
        for a in articles
    )
    key = today + "|" + "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()


def _load_last_hash() -> str:
    if _HASH_FILE.exists():
        return _HASH_FILE.read_text().strip()
    return ""


def _save_hash(h: str) -> None:
    _HASH_FILE.write_text(h)


def _plain_text(raw: str) -> str:
    """Strip markdown syntax to get plain text safe for HTML insertion."""
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", raw)   # remove images ![](url)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)  # [text](url) → text
    text = re.sub(r"[\\*_`#>|]", "", text)             # remove markdown punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_html(articles: list[dict], today: str) -> str:
    by_source: dict[str, list[dict]] = {}
    for a in articles:
        by_source.setdefault(a["source"], []).append(a)

    rows = []
    for source, items in by_source.items():
        category = items[0]["category"]
        rows.append(
            f"<h3 style='color:#444;margin:24px 0 8px;padding-bottom:4px;"
            f"border-bottom:1px solid #eee'>"
            f"{source}"
            f"<span style='color:#aaa;font-weight:normal;font-size:12px;margin-left:8px'>"
            f"{category}</span></h3>"
        )
        for a in items:
            excerpt = _plain_text(a.get("excerpt", ""))
            published = a.get("published", "")
            pub_html = (
                f"<span style='color:#bbb;font-size:11px;margin-left:8px'>{published}</span>"
                if published and published != "unknown" else ""
            )
            excerpt_html = (
                f"<p style='margin:4px 0 0;color:#666;font-size:13px;line-height:1.5'>"
                f"{excerpt}</p>"
            ) if excerpt else ""
            rows.append(
                f"<div style='margin-bottom:14px;padding-left:4px'>"
                f"<a href='{a['url']}' style='color:#1a73e8;font-size:14px;font-weight:500'>"
                f"{a['title']}</a>"
                f"{pub_html}"
                f"{excerpt_html}"
                f"</div>"
            )

    # Subject prefix and header are kept stable for email filter rules.
    # Filter on: subject starts with "[fairing]"
    return f"""
<html><body style="font-family:Arial,sans-serif;max-width:720px;margin:auto;padding:24px;color:#333">
  <h2 style="border-bottom:2px solid #1a73e8;padding-bottom:10px;margin-bottom:6px">
    fairing Daily Digest
  </h2>
  <p style="color:#888;margin:0 0 20px;font-size:13px">
    {today} · {len(articles)} articles · {len(by_source)} sources
  </p>
  {"".join(rows)}
  <hr style="margin-top:32px;border:none;border-top:1px solid #eee">
  <p style="color:#ccc;font-size:11px;margin-top:8px">
    [fairing] · jieker_mail@163.com
  </p>
</body></html>
"""


def send_digest(articles: list[dict]) -> None:
    """Send digest email, skipping if content hash matches the previous send.

    Only sends when SMTP_USER / SMTP_PASSWORD / MAIL_TO are all configured.
    SMTP_PASSWORD must be the 163 authorization code (授权码), not the login password.

    @param articles: full article list for the current run
    @param new_count: number of newly written articles (shown as badge in email)
    """
    host = os.environ.get("SMTP_HOST", "smtp.163.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    mail_to = os.environ.get("MAIL_TO", "")

    if not all([user, password, mail_to]):
        logger.debug("Email not configured — skipping")
        return

    current_hash = _article_hash(articles)
    if current_hash == _load_last_hash():
        logger.warning("!" * 50)
        logger.warning("EMAIL SKIPPED — content identical to previous send")
        logger.warning("MD5: %s  (delete .digest_hash to force re-send)", current_hash[:8])
        logger.warning("!" * 50)
        return

    today = date.today().isoformat()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[fairing] {today}"
    msg["From"] = user
    msg["To"] = mail_to
    msg.attach(MIMEText(_build_html(articles, today), "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            server.login(user, password)
            server.sendmail(user, mail_to, msg.as_string())
        _save_hash(current_hash)
        logger.info("Email sent → %s  [MD5: %s]", mail_to, current_hash[:8])
    except Exception as exc:
        logger.warning("Email failed: %s", exc)
