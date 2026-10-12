"""Small, explainable reading filters shared by the digest and web UI."""

from __future__ import annotations

import re


# These phrases describe an invitation, paywall pitch, or an explicitly paid
# placement. Product announcements and technical writeups remain eligible.
_PROMOTION = re.compile(
    r"^\s*\[?(?:sponsored|advertisement|advertorial|广告|赞助内容)\]?\s*[:：-]|"
    r"\b(?:subscriber[- ]only|meetups?|webinars?|coupon|discount code)\b|"
    r"订阅者专属|付费订阅|各地聚会|线下聚会|读者聚会|见面会|优惠码|限时优惠",
    re.IGNORECASE,
)
_CATEGORY_HINT = re.compile(
    r"AI|人工智能|研究|工程|新闻|资讯|数据库|数据平台|基础设施|架构|理性|商业|管理",
    re.IGNORECASE,
)


def clean_translated_title(title: str) -> str:
    """Remove source/category echoed by the old pipe-delimited translator."""
    parts = str(title).split("|", 2)
    if (len(parts) == 3 and all(part.strip() for part in parts)
            and len(parts[0]) <= 45 and len(parts[1]) <= 45
            and _CATEGORY_HINT.search(parts[1])):
        return parts[2].strip()
    return str(title)


def is_promotion(article: dict) -> bool:
    """Only suppress titles with an explicit promotional or event cue."""
    title = clean_translated_title(article.get("title_zh") or article.get("title") or "")
    return bool(_PROMOTION.search(title))


def feedback_by_url(feedback: list[dict]) -> dict[str, dict]:
    """Keep the last recorded opinion for each URL."""
    return {str(row["url"]): row for row in feedback if row.get("url")}


def is_visible(article: dict, labels: dict[str, dict]) -> bool:
    """A positive label can restore a title hidden by the automatic rule."""
    label = labels.get(article.get("url", ""), {}).get("label")
    if label == 1:
        return True
    if label == -1:
        return False
    return not is_promotion(article)
