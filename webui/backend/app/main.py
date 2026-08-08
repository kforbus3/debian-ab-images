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


# Paths that belong to the provisioning HTTP server, not to this app. Answering
# them with the SPA is worse than answering nothing: rauc streamed index.html
# from /bundles/x.raucb, read the last eight bytes of the page as the bundle's
# signature size, and reported "Signature size (4336799815442382346) exceeds
# bundle size" -- which is "</html>\n" as a big-endian integer, and reads like a
# corrupt bundle rather than a wrong port.
_NOT_OURS = ("bundles/", "images/", "imager/", "hosts/")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if full_path.startswith(_NOT_OURS):
        return JSONResponse(status_code=404, content={
            "detail": f"/{full_path} is served by the provisioning server on port 80, "
                      "not by the web UI"})
    # Resolve and contain: os.path.join ignores the base for absolute paths,
    # and encoded ../ sequences arrive decoded — both would escape STATIC_DIR.
    root = os.path.realpath(STATIC_DIR)
    candidate = os.path.realpath(os.path.join(root, full_path))
    if full_path and candidate.startswith(root + os.sep) and os.path.isfile(candidate):
        return FileResponse(candidate)
    # Past that point the file does not exist. A path whose last segment has an
    # extension was asking for a file rather than for a client-side route, so
    # 404 instead of falling through: an HTML body under a name that promises
    # otherwise fails somewhere further along than the mistake, which is how a
    # wrong port turned into a corrupt-bundle report above. Real assets are
    # served by the branch just above and by the /assets mount, so this only
    # ever catches names that are not there.
    if "." in full_path.rsplit("/", 1)[-1]:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse(content={"message": "Debian A/B Images UI API", "version": __version__})
