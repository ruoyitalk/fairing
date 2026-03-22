"""Tests for fairing/reader.py — URL type detection."""


# ── _url_type ──────────────────────────────────────────────────────────────────

def test_url_type_article():
    from fairing.reader import _url_type
    assert _url_type("https://arxiv.org/abs/2501.12345") == "article"
    assert _url_type("https://example.com/blog/post-1") == "article"


def test_url_type_image():
    from fairing.reader import _url_type
    assert _url_type("https://example.com/figure.png")    == "image"
    assert _url_type("https://cdn.example.com/photo.jpg") == "image"
    assert _url_type("https://img.example.com/x.webp")    == "image"


def test_url_type_video_extension():
    from fairing.reader import _url_type
    assert _url_type("https://example.com/demo.mp4")  == "video"
    assert _url_type("https://example.com/talk.webm") == "video"


def test_url_type_video_domain():
    from fairing.reader import _url_type
    assert _url_type("https://youtube.com/watch?v=abc") == "video"
    assert _url_type("https://youtu.be/abc123")         == "video"
    assert _url_type("https://vimeo.com/123456")        == "video"
    assert _url_type("https://bilibili.com/video/BV1")  == "video"


def test_url_type_case_insensitive():
    from fairing.reader import _url_type
    assert _url_type("HTTPS://EXAMPLE.COM/PHOTO.JPG") == "image"
