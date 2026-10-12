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
_GENERALIST_AI_SOURCES = frozenset({
    "Simon Willison", "LessWrong", "Astral Codex Ten",
    "MIT Sloan Management Review", "One Useful Thing", "ByteByteGo",
})
_AI_TOPIC = re.compile(
    r"\b(?:AI|LLM|GPT|ChatGPT|Codex|Claude|OpenAI|Anthropic|DeepMind|"
    r"Copilot|transformer|machine learning|neural|inference|embeddings?|"
    r"agents?|robotics?)\b|"
    r"人工智能|大模型|语言模型|机器学习|智能体|神经网络|多模态|"
    r"生成式|推理模型|机器人|嵌入模型",
    re.IGNORECASE,
)
_VENDOR_CASE_STUDY = re.compile(
    r"\b(?:uses?|using|leverag(?:es|ing))\b.{0,80}"
    r"\b(?:OpenAI|ChatGPT|Codex|GPT)\b|"
    r"(?:借助|使用|通过|如何用|用).{0,60}(?:OpenAI|ChatGPT|Codex|GPT)",
    re.IGNORECASE,
)
_VENDOR_OUTCOME_STUDY = re.compile(
    r"\b(?:cuts?|reduces?|saves?|speeds?|boosts?|slashes?|accelerates?)\b"
    r".{0,90}\b(?:with|using)\b.{0,30}\b(?:OpenAI|ChatGPT|Codex|GPT)\b|"
    r".{0,35}(?:将|把).{0,20}(?:OpenAI|ChatGPT|Codex|GPT)"
    r".{0,35}(?:成本|时间|耗时|效率|速度)"
    r".{0,25}(?:减半|降低|缩短|提升|提高|减少|节省)",
    re.IGNORECASE,
)
_LOW_INFORMATION_TITLE = re.compile(r"^(?:faq|about|home|index|首页|常见问题)$", re.IGNORECASE)


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


def is_default_filtered(article: dict) -> bool:
    """Keep the AI news page focused while retaining every item in the index."""
    if is_promotion(article):
        return True
    source = str(article.get("source") or "")
    title = clean_translated_title(article.get("title_zh") or article.get("title") or "")
    if _LOW_INFORMATION_TITLE.fullmatch(title.strip()):
        return True
    if source == "OpenAI News" and (
        _VENDOR_CASE_STUDY.search(title) or _VENDOR_OUTCOME_STUDY.search(title)
    ):
        return True
    if source in _GENERALIST_AI_SOURCES:
        summary = str(article.get("summary_zh") or article.get("summary")
                      or article.get("excerpt") or "")
        return not bool(_AI_TOPIC.search(f"{title} {summary}"))
    return False


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
    return not is_default_filtered(article)
