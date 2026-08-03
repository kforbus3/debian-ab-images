"""FastAPI app: API under /api, built SPA at /."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import importlib
import pkgutil

from app import __version__
from app import routers as _routers

STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "static"))

# The SPA is served same-origin (and the vite dev server proxies /api), so no
# CORS policy is needed — browsers then refuse cross-origin API use outright.
app = FastAPI(title="Debian A/B Images UI", version=__version__)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}


# Every router in app.routers is mounted, rather than a hand-written list.
# The list was the bug: the imaging router was imported and then left out of it,
# so /api/imaging fell through to the SPA handler below and answered "Not found"
# -- a plausible-looking 404 from a route that was never registered at all. A
# new router file is now reachable the moment it exists.
for _mod in pkgutil.iter_modules(_routers.__path__):
    _router = getattr(importlib.import_module(f"app.routers.{_mod.name}"), "router", None)
    if _router is not None:
        app.include_router(_router, prefix="/api")

_assets = os.path.join(STATIC_DIR, "assets")
if os.path.isdir(_assets):
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    # Resolve and contain: os.path.join ignores the base for absolute paths,
    # and encoded ../ sequences arrive decoded — both would escape STATIC_DIR.
    root = os.path.realpath(STATIC_DIR)
    candidate = os.path.realpath(os.path.join(root, full_path))
    if full_path and candidate.startswith(root + os.sep) and os.path.isfile(candidate):
        return FileResponse(candidate)
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse(content={"message": "Debian A/B Images UI API", "version": __version__})
