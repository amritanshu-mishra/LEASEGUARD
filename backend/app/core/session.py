"""Account identity for the API.

The demo originally ran every request against a single hardcoded user, so anyone with
the URL could read every lease and the whole activity trail. That is fine on a
laptop and disqualifying on a deployed URL, since the product's whole premise is
that a tenant's rent figures are private.

How identity works here:

* A browser gets an opaque, random session token in an HttpOnly cookie on first use.
* The token is stored server-side as a SHA-256 hash, never in plain text, so a leaked
  database cannot be replayed as a login.
* Every query is scoped by the resulting user id, so two tenants on the same
  deployment cannot see each other's leases.

This is deliberately *not* a full auth system: there is no password, no email, no
recovery. It makes data private between separate browsers, which is the security
property the app was missing. Anything stronger (accounts that follow you between
devices) needs a real identity provider and is out of scope for this build.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import Cookie, Response

from ..infra import store

COOKIE_NAME = "lg_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # ~6 months: a tenant checks rent monthly

# A well-known identity used by the test-suite and the demo bootstrap so that the
# seeded sample case is reachable without a browser cookie.
DEMO_SESSION = "demo-session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(response: Response) -> str:
    """Create a new anonymous session and attach it as an HttpOnly cookie."""
    token = secrets.token_urlsafe(32)
    user_id = f"user-{secrets.token_hex(8)}"
    store.create_session(_hash(token), user_id)
    _set_cookie(response, token)
    return user_id


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,          # JavaScript cannot read it, so XSS cannot exfiltrate it
        samesite="lax",         # not sent on cross-site POSTs
        secure=False,           # set True when served over HTTPS; kept off for local demo
        path="/",
    )


def resolve_user(session: str | None) -> str | None:
    """Map a cookie value to a user id, or None if it is absent/unknown."""
    if not session:
        return None
    if session == DEMO_SESSION:
        return "demo-user"
    return store.user_for_session_hash(_hash(session))


def current_user(response: Response, session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> str:
    """FastAPI dependency: the caller's user id, creating a session if needed.

    A brand-new browser is given an identity rather than being rejected, so the demo
    starts working on first load with no signup. The response carries the cookie that
    makes the identity stick for subsequent calls.
    """
    user_id = resolve_user(session)
    if user_id:
        return user_id
    return issue_session(response)
