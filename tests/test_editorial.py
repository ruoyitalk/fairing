from fairing.editorial import (
    clean_translated_title, feedback_by_url, is_promotion, is_visible,
)


def test_explicit_events_and_subscription_pitches_are_suppressed():
    for title in (
        "Santiago——2026年秋季ACX各地聚会",
        "[订阅者专属]纽约订阅者聚会！",
        "Sponsored: AI tool roundup",
        "Join our developer meetup",
    ):
        assert is_promotion({"title": title})
    assert not is_promotion({"title": "New model architecture and evaluation"})
    assert not is_promotion({"title": "OpenAI releases new API"})


def test_last_feedback_overrides_automatic_filter():
    article = {"url": "https://example.org/a", "title": "Developer meetup"}
    assert not is_visible(article, {})
    labels = feedback_by_url([
        {"url": article["url"], "label": -1},
        {"url": article["url"], "label": 1},
    ])
    assert is_visible(article, labels)
    labels = feedback_by_url([
        {"url": article["url"], "label": 1},
        {"url": article["url"], "label": -1, "reason": "advertisement"},
    ])
    assert not is_visible(article, labels)


def test_old_translation_prefix_is_removed_without_damaging_plain_title():
    assert clean_translated_title("LessWrong|AI / 理性|城市聚会") == "城市聚会"
    assert clean_translated_title("A | B | C") == "A | B | C"
