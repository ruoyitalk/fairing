from types import SimpleNamespace

import pytest

from fairing.web_auth import (
    google_user_allowed,
    homeserver_key_matches,
    parse_allowed_emails,
    read_secret,
    user_email,
)


def test_parse_allowed_emails_normalizes_case_and_separators():
    assert parse_allowed_emails("A@example.com, b@example.com\n") == {
        "a@example.com",
        "b@example.com",
    }


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"email": " A@Example.com "}, "a@example.com"),
        (SimpleNamespace(email="b@example.com"), "b@example.com"),
        (None, ""),
    ],
)
def test_user_email(user, expected):
    assert user_email(user) == expected


def test_google_user_requires_allowlist_match():
    assert google_user_allowed({"email": "a@example.com"}, ["A@example.com"])
    assert not google_user_allowed({"email": "x@example.com"}, ["a@example.com"])


def test_homeserver_key_comparison_rejects_empty_values():
    assert homeserver_key_matches("correct", "correct")
    assert not homeserver_key_matches("wrong", "correct")
    assert not homeserver_key_matches("", "")


def test_read_secret_rejects_empty_file(tmp_path):
    path = tmp_path / "secret"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty secret"):
        read_secret(path)
