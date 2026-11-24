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


# Serve frontend build if present
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # don't swallow API
        if full_path.startswith("api/"):
            return {"error": {"code": "not_found", "message": "Not found"}}
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
