"""Authentication helpers for the Fairing Streamlit UI.

Keep secret I/O and identity normalization here so they can be tested without
starting Streamlit. The web entrypoint owns the actual login UI.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any, Iterable


def read_secret(path: str | Path) -> str:
    """Read a mounted secret without accepting an empty value."""
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"empty secret file: {path}")
    return value


def parse_allowed_emails(raw: str) -> frozenset[str]:
    """Parse newline- or comma-separated Google account allowlists."""
    normalized = raw.replace(",", "\n")
    return frozenset(
        item.strip().casefold() for item in normalized.splitlines() if item.strip()
    )


def user_email(user: Any) -> str:
    """Extract an email from Streamlit's user object across supported shapes."""
    if user is None:
        return ""
    if isinstance(user, dict):
        return str(user.get("email", "")).strip().casefold()
    get = getattr(user, "get", None)
    if callable(get):
        try:
            return str(get("email", "")).strip().casefold()
        except (TypeError, AttributeError):
            pass
    return str(getattr(user, "email", "")).strip().casefold()


def google_user_allowed(user: Any, allowed_emails: Iterable[str]) -> bool:
    allowed = {str(email).strip().casefold() for email in allowed_emails if email}
    email = user_email(user)
    return bool(email and email in allowed)


def homeserver_key_matches(candidate: str, expected: str) -> bool:
    """Compare the recovery key without timing-dependent early exits."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
