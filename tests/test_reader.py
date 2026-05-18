"""Tests for fairing/reader.py — URL type detection."""
from unittest.mock import patch


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


class _Response:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetch_full_result_prefers_search_gateway(monkeypatch):
    from fairing.reader import fetch_full_result
    monkeypatch.setenv("SEARCH_GATEWAY_URL", "http://search-gateway:8520")
    monkeypatch.setenv("SEARCH_GATEWAY_API_KEY", "sg_key")
    with patch("fairing.reader.requests.post", return_value=_Response({
        "content": "Fetched body",
        "engine": "scrapling_get",
        "blocked": False,
        "error_type": None,
        "block_reason": None,
        "upstream_status": 200,
    })) as mock_post:
        result = fetch_full_result("https://example.com")

    assert result["content"] == "Fetched body"
    assert result["engine"] == "scrapling_get"
    assert result["blocked"] is False
    assert mock_post.call_args.kwargs["headers"]["X-API-Key"] == "sg_key"


def test_fetch_full_result_preserves_blocked_diagnostics(monkeypatch):
    from fairing.reader import fetch_full_result
    monkeypatch.setenv("SEARCH_GATEWAY_URL", "http://search-gateway:8520")
    with patch("fairing.reader.requests.post", return_value=_Response({
        "content": "Access Denied",
        "engine": "trafilatura",
        "blocked": True,
        "error_type": "blocked_by_waf",
        "block_reason": "akamai_edgesuite",
        "upstream_status": 403,
    })):
        result = fetch_full_result("https://blocked.example")

    assert result["blocked"] is True
    assert result["block_reason"] == "akamai_edgesuite"
