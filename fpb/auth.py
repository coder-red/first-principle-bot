"""Optional shared-token access control.

Off unless APP_ACCESS_TOKEN is set, which keeps `python main.py` a zero-config
run and leaves the browser suites untouched. Set it on a public deploy and the
two endpoints that spend model credits stop being anonymous.

This is a shared secret, not user accounts. It answers "should this person be
able to spend my credits at all", which is the actual exposure on a public
URL — the rate limiter only ever bounded how fast a stranger could do it.
"""

import hmac
import os
from typing import Optional

from fastapi import HTTPException

COOKIE_NAME = "fp_access"

# A browser cannot set a header on a normal navigation, so the token is
# exchanged once for a cookie. Long-lived because the alternative is retyping
# a shared secret every day, which pushes people to pick a shorter one.
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def configured_token() -> str:
    """The expected token, or "" when access control is off."""
    return os.environ.get("APP_ACCESS_TOKEN", "").strip()


def is_enabled() -> bool:
    return bool(configured_token())


def _presented(request) -> Optional[str]:
    """The token this request carries, from whichever channel supplied it."""
    header = (request.headers.get("x-access-token") or "").strip()
    if header:
        return header

    authorization = (request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    cookie = request.cookies.get(COOKIE_NAME)
    return cookie.strip() if cookie else None


def token_matches(candidate: Optional[str]) -> bool:
    expected = configured_token()
    if not expected or not candidate:
        return False
    # Constant-time: a plain == leaks the shared secret one character at a
    # time to anyone who can measure the response.
    return hmac.compare_digest(candidate, expected)


def require_access(request) -> None:
    """Gate an endpoint. A no-op when APP_ACCESS_TOKEN is unset."""
    if not is_enabled():
        return
    if token_matches(_presented(request)):
        return
    raise HTTPException(
        status_code=401,
        detail="This instance requires an access token.",
    )
