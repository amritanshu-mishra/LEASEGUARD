from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .core.config import ALLOWED_ORIGINS, REPO_DIR
from .infra.db import init_db

app = FastAPI(title="LeaseGuard API", version="1.0.0")


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request, exc: RequestValidationError):
    """Return the app's error shape for framework-level validation failures too.

    FastAPI's default 422 body is a list of pydantic errors. The UI expects
    {"error": {"code", "message"}} everywhere, so missing/​malformed fields would
    otherwise render as "Request failed (422)" instead of a useful sentence.
    """
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
    if first.get("type") == "missing":
        message = f"Missing required field: {field or 'request body'}."
    else:
        message = first.get("msg") or "The request could not be processed."
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": message}},
    )

# A preview/hosting host adds its own origin, so allow anything when origins are
# configured as "*" and otherwise match the configured list plus preview hosts.
_allow_all = not ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else ALLOWED_ORIGINS,
    allow_origin_regex=r"https://[a-zA-Z0-9.-]+\.e2b\.app" if not _allow_all else None,
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
    # The page mirrors the session token out of this header; without exposure a
    # cross-origin front end can set cookies but cannot read the reply.
    expose_headers=["X-Session-Token"],
)

@app.exception_handler(405)
async def _method_not_allowed(request, exc):
    """Give 405s the same error shape as every other failure.

    Starlette's default body is `{"detail": "Method Not Allowed"}`, which the app's
    own client cannot read (it looks for `error.code`), so a wrong-method call from
    the UI surfaced as "Request failed (405)" with no hint of what to fix.
    """
    headers = getattr(exc, "headers", None) or {}
    allowed = headers.get("allow") or headers.get("Allow")
    return JSONResponse(
        status_code=405,
        content={
            "error": {
                "code": "method_not_allowed",
                "message": f"Use one of: {allowed}" if allowed else "Method not allowed",
            }
        },
        headers={"Allow": allowed} if allowed else None,
    )


@app.middleware("http")
async def _security_headers(request, call_next):
    """Baseline hardening for a page that displays a household's rent numbers.

    The app is a static SPA plus a JSON API, so it needs no inline scripts and no
    framing. `frame-ancestors 'none'` is deliberately not set to X-Frame-Options DENY
    alone: some preview hosts embed the app in an iframe, so framing is allowed only
    from the preview origin via CSP rather than being blocked outright.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    return response


app.include_router(router)


@app.on_event("startup")
def _startup():
    init_db()


def _methods_for(path: str) -> set[str]:
    """HTTP methods a path is actually registered for (empty if unknown)."""
    methods: set[str] = set()
    for route in app.routes:
        if getattr(route, "path", None) == path:
            methods.update(getattr(route, "methods", set()) or set())
    return methods - {"HEAD", "OPTIONS"}


# Serve frontend build if present
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Never let the SPA fallback answer for the API. Returning 200 with an
        # error body here is worse than useless: every client that checks
        # `res.ok` treats a typo'd endpoint or a method mismatch as a success and
        # then fails on a missing field, which is a confusing bug report far from
        # the cause. A real 404 in the app's error shape is unambiguous.
        if full_path.startswith("api/"):
            # The route exists but only for another method: that is a 405, and
            # saying "not found" sends the caller hunting for a typo that is not
            # there. Any other unmatched API path really is a 404.
            allowed = _methods_for("/" + full_path)
            if allowed:
                return JSONResponse(
                    status_code=405,
                    content={
                        "error": {
                            "code": "method_not_allowed",
                            "message": f"Use one of: {', '.join(sorted(allowed))}",
                        }
                    },
                    headers={"Allow": ", ".join(sorted(allowed))},
                )
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": "Not found"}},
            )
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
