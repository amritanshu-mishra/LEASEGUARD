from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .core.config import ALLOWED_ORIGINS, REPO_DIR
from .infra.db import init_db

app = FastAPI(title="LeaseGuard API", version="1.0.0")

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
