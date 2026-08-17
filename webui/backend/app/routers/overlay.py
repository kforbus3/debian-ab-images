"""The files copied into every image — managed from the browser.

`overlay.d` is a directory in the repository, and until now the only way to put
something in it was a shell on the machine running the UI. That is a strange
gap in an app whose whole point is that you do not need one: everything else a
build depends on is configurable here, and the most site-specific part of it was
the one thing you had to ssh in for.

Paths are image paths (`/etc/hosts`), because that is what the file will be on
the machine. The mapping to `overlay.d/etc/hosts` is this module's business, and
the containment that goes with it lives in orchestrator.overlay_resolve().
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app import orchestrator as orch
from app.security import Principal, require_operator, require_viewer

router = APIRouter(tags=["overlay"])

# Big enough for a certificate bundle, a firmware blob, or a vendored binary --
# all things that legitimately belong in an image -- and small enough that a
# misdirected upload fails fast rather than filling the disk the builds need.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
# What the browser will edit inline. Past this the file is still shipped, it
# just cannot be typed into a textarea.
MAX_EDIT_BYTES = 1024 * 1024


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, orch.OverlayPathError):
        return HTTPException(400, str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, f"no such file in overlay.d: {exc}")
    if isinstance(exc, OSError):
        return HTTPException(500, f"could not write to overlay.d: {exc.strerror or exc}")
    return HTTPException(500, str(exc))


def _mode_from(body: dict, default: int = 0o644) -> int:
    """Explicit octal wins; otherwise the executable flag picks 0755 or 0644.

    The mode is preserved by the builder's `cp -a`, so a script shipped 0644 is
    a script that does not run on the machine -- worth making settable rather
    than guessing from the path.
    """
    raw = str(body.get("mode") or "").strip()
    if raw:
        try:
            mode = int(raw, 8)
        except ValueError:
            raise HTTPException(400, f"mode must be octal, e.g. 0644 (got '{raw}')")
        if not 0 <= mode <= 0o7777:
            raise HTTPException(400, "mode must be between 0000 and 7777")
        return mode
    if body.get("executable"):
        return 0o755
    return default


@router.get("/overlay")
async def overlay(_: Principal = Depends(require_viewer)):
    """Files that will be copied into the next image, and what that means."""
    files = await run_in_threadpool(orch.overlay_files)
    return {
        "files": files,
        "dir": f"{orch.host_project_dir()}/overlay.d",
        "count": len(files),
        # Empty when files can be managed from here. The UI needs to know before
        # offering an editor it cannot save from.
        "readonly_reason": await run_in_threadpool(orch.overlay_writable),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@router.get("/overlay/file")
async def read_file(path: str, _: Principal = Depends(require_viewer)):
    try:
        return await run_in_threadpool(orch.overlay_read, path, MAX_EDIT_BYTES)
    except (orch.OverlayPathError, FileNotFoundError, OSError) as exc:
        raise _fail(exc)


@router.get("/overlay/download")
async def download_file(path: str, _: Principal = Depends(require_viewer)):
    """The raw bytes, for files the browser cannot edit."""
    try:
        full, image_path = await run_in_threadpool(orch.overlay_resolve, path)
        if not os.path.isfile(full):
            raise FileNotFoundError(image_path)
        with open(full, "rb") as f:
            data = f.read()
    except (orch.OverlayPathError, FileNotFoundError, OSError) as exc:
        raise _fail(exc)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{os.path.basename(image_path)}"'})


@router.put("/overlay/file")
async def write_file(body: dict = Body(...), _: Principal = Depends(require_operator)):
    """Create or replace a text file. Omit `content` to change only the mode."""
    path = str(body.get("path") or "")
    mode = _mode_from(body)
    try:
        if body.get("content") is None:
            return await run_in_threadpool(orch.overlay_chmod, path, mode)
        return await run_in_threadpool(
            orch.overlay_write, path, str(body["content"]).encode(), mode)
    except (orch.OverlayPathError, FileNotFoundError, OSError) as exc:
        raise _fail(exc)


@router.post("/overlay/upload")
async def upload_file(path: str = Form(...), file: UploadFile = File(...),
                      mode: str = Form(""), executable: bool = Form(False),
                      _: Principal = Depends(require_operator)):
    """Upload a file to an image path. Binary is fine; it is copied verbatim."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file is larger than the {MAX_UPLOAD_BYTES // (1024*1024)} MiB limit")
    # An upload with no path falls back to the uploaded filename at the root,
    # which is almost never what was meant -- better to say so.
    if not path.strip():
        raise HTTPException(400, "a destination path is required, e.g. /usr/local/bin/tool")
    resolved_mode = _mode_from({"mode": mode, "executable": executable})
    try:
        return await run_in_threadpool(orch.overlay_write, path, data, resolved_mode)
    except (orch.OverlayPathError, OSError) as exc:
        raise _fail(exc)


@router.post("/overlay/move")
async def move_file(body: dict = Body(...), _: Principal = Depends(require_operator)):
    try:
        return await run_in_threadpool(
            orch.overlay_move, str(body.get("from") or ""), str(body.get("to") or ""))
    except (orch.OverlayPathError, FileNotFoundError, OSError) as exc:
        raise _fail(exc)


@router.delete("/overlay/file")
async def delete_file(path: str, _: Principal = Depends(require_operator)):
    try:
        return await run_in_threadpool(orch.overlay_delete, path)
    except (orch.OverlayPathError, FileNotFoundError, OSError) as exc:
        raise _fail(exc)
