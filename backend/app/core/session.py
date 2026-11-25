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

from fastapi import Cookie, Request, Response

from ..infra import store

COOKIE_NAME = "lg_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # ~6 months: a tenant checks rent monthly

# A well-known identity used by the test-suite and the demo bootstrap so that the
# seeded sample case is reachable without a browser cookie.
DEMO_SESSION = "demo-session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(response: Response, request: Request) -> str:
    """Create a new anonymous session and attach it as an HttpOnly cookie."""
    token = secrets.token_urlsafe(32)
    user_id = f"user-{secrets.token_hex(8)}"
    store.create_session(_hash(token), user_id)
    _set_cookie(response, token, request)
    return user_id


def _cookie_secure(request: Request) -> bool:
    """Decide the Secure flag from how the request actually arrived.

    A hardcoded `secure=False` is wrong on a deployed HTTPS URL and, worse, is
    wrong in a way that is invisible until data appears to vanish: behind a TLS
    proxy the browser may drop the cookie or the proxy may strip it, and then
    every request looks like a new visitor. The symptom is not an error, it is
    "I loaded the sample and it disappeared".

    The proxy terminates TLS, so the request arriving at uvicorn is plain HTTP
    even though the browser is on https://. `X-Forwarded-Proto` is the signal,
    and plain HTTP locally still gets a non-Secure cookie so `localhost` works.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


# A companion header so a client that cannot keep cookies can still hold an
# identity. Embeds (and Safari's ITP) drop third-party cookies entirely, and then
# the app looks like it is losing data: you load the sample, it reports success,
# and the next page says "not found".
SESSION_HEADER = "x-session-token"
SESSION_HEADER_OUT = "X-Session-Token"


def _set_cookie(response: Response, token: str, request: Request) -> None:
    secure = _cookie_secure(request)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,          # JavaScript cannot read it, so XSS cannot exfiltrate it
        # The app is demoed inside a cross-origin iframe (and on preview hosts).
        # `lax` cookies are *not sent at all* in that context, so every request
        # arrived with no session and the UI kept saying "not found". None+Secure
        # is the correct pairing for a framed app; browsers require Secure with it.
        samesite="none" if secure else "lax",
        secure=secure,
        path="/",
    )
    # Also hand the token back in a header. The page stores it and replays it, so
    # cookies are an optimisation rather than a single point of failure. This is
    # readable by the page's own JavaScript, which is a deliberate trade: the demo
    # has to work in browsers that block third-party cookies outright.
    response.headers[SESSION_HEADER_OUT] = token


def resolve_user(session: str | None) -> str | None:
    """Map a cookie value to a user id, or None if it is absent/unknown."""
    if not session:
        return None
    if session == DEMO_SESSION:
        return "demo-user"
    return store.user_for_session_hash(_hash(session))


def current_user(
    response: Response,
    request: Request,
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> str:
    """FastAPI dependency: the caller's user id, creating a session if needed.

    A brand-new browser is given an identity rather than being rejected, so the demo
    starts working on first load with no signup. The response carries the cookie that
    makes the identity stick for subsequent calls, and the same token in a header for
    clients whose cookies do not survive.
    """
    user_id = resolve_user(session)
    if user_id:
        # Echo it even on the happy path: a tab that has a cookie but has not yet
        # stored the token would otherwise never learn it, and the fallback would
        # be useless exactly when it is needed (the cookie vanishes mid-demo).
        response.headers[SESSION_HEADER_OUT] = session
        return user_id

    # Cookie missing or unknown: fall back to the header the page replays.
    header_token = request.headers.get(SESSION_HEADER)
    if header_token and header_token != session:
        user_id = resolve_user(header_token)
        if user_id:
            # Refresh the cookie too, so one successful request repairs a browser
            # whose cookie was dropped.
            _set_cookie(response, header_token, request)
            return user_id

    return issue_session(response, request)
