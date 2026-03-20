"""Tests for fairing/mailer.py — article hash, HTML builder, send_digest logic."""
import pytest


def _article(url: str = "https://a.com", title: str = "Title",
             source: str = "Src", category: str = "Cat",
             excerpt: str = "Excerpt.", published: str = "2026-03-20") -> dict:
    return {"url": url, "title": title, "source": source,
            "category": category, "excerpt": excerpt, "published": published}


# ── _article_hash ──────────────────────────────────────────────────────────────

def test_article_hash_is_deterministic():
    from fairing.mailer import _article_hash
    articles = [_article()]
    assert _article_hash(articles) == _article_hash(articles)


def test_article_hash_changes_with_content():
    from fairing.mailer import _article_hash
    a1 = [_article(title="Old")]
    a2 = [_article(title="New")]
    assert _article_hash(a1) != _article_hash(a2)


def test_article_hash_order_independent():
    """Hash should be the same regardless of article order (sorted internally)."""
    from fairing.mailer import _article_hash
    a = _article(url="https://a.com", title="A")
    b = _article(url="https://b.com", title="B")
    assert _article_hash([a, b]) == _article_hash([b, a])


def test_article_hash_returns_string():
    from fairing.mailer import _article_hash
    h = _article_hash([_article()])
    assert isinstance(h, str) and len(h) == 32  # MD5 hex


# ── _build_html ────────────────────────────────────────────────────────────────

def test_build_html_contains_title():
    from fairing.mailer import _build_html
    articles = [_article(title="My Unique Title")]
    html = _build_html(articles, "2026-03-20")
    assert "My Unique Title" in html


def test_build_html_contains_source_header():
    from fairing.mailer import _build_html
    articles = [_article(source="ClickHouse Blog")]
    html = _build_html(articles, "2026-03-20")
    assert "ClickHouse Blog" in html


def test_build_html_contains_article_url():
    from fairing.mailer import _build_html
    articles = [_article(url="https://specific.example.com/post")]
    html = _build_html(articles, "2026-03-20")
    assert "https://specific.example.com/post" in html


def test_build_html_numbered_articles():
    """Email articles should have #N index in the rendered HTML."""
    from fairing.mailer import _build_html
    # Provide 2 articles with distinct scores to trigger numbering
    a1 = {**_article(url="https://a.com"), "score": 0.9}
    a2 = {**_article(url="https://b.com"), "score": 0.5}
    html = _build_html([a1, a2], "2026-03-20")
    assert "#1" in html
    assert "#2" in html


def test_build_html_rest_section_for_many_articles():
    """Articles beyond TOP_N should appear in the compact rest section."""
    from fairing.mailer import _build_html
    from fairing.writer import TOP_N
    articles = []
    for i in range(TOP_N + 3):
        a = _article(url=f"https://example.com/{i}", title=f"Article {i}")
        a["score"] = 1.0 - i * 0.01
        articles.append(a)
    html = _build_html(articles, "2026-03-20")
    assert "其余" in html


# ── send_digest — SMTP skip when unconfigured ──────────────────────────────────

def test_send_digest_skips_when_not_configured(monkeypatch):
    """send_digest should silently skip when SMTP credentials are absent."""
    import fairing.mailer as m
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("MAIL_TO", "")
    # Should not raise
    m.send_digest([_article()])


# ── hash dedup guard ───────────────────────────────────────────────────────────

def test_send_digest_skips_duplicate_hash(tmp_path, monkeypatch):
    """When hash matches stored hash, email is skipped (not force)."""
    import fairing.mailer as m
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_USER",     "user@163.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("MAIL_TO",       "dest@example.com")

    articles     = [_article()]
    current_hash = m._article_hash(articles)
    from fairing.paths import digest_hash_file
    digest_hash_file().write_text(current_hash)

    sent = []
    monkeypatch.setattr(m.smtplib, "SMTP_SSL", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not send")))
    # Should not raise — duplicate hash causes early return
    m.send_digest(articles, force=False)


def test_send_digest_force_bypasses_hash(tmp_path, monkeypatch):
    """force=True should attempt send even when hash matches."""
    import fairing.mailer as m, smtplib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_USER",     "user@163.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("MAIL_TO",       "dest@example.com")

    articles     = [_article()]
    current_hash = m._article_hash(articles)
    from fairing.paths import digest_hash_file
    digest_hash_file().write_text(current_hash)

    calls = []

    class _FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, u, p): calls.append("login")
        def sendmail(self, *a): calls.append("sendmail")

    monkeypatch.setattr(m.smtplib, "SMTP_SSL", _FakeSMTP)
    m.send_digest(articles, force=True)
    assert "sendmail" in calls
