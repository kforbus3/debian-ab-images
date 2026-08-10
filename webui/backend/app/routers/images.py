import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app import orchestrator as orch
from app.config import settings
from app.security import require_auth

router = APIRouter(prefix="/images", tags=["images"])


@router.get("")
async def list_images(_: str = Depends(require_auth)):
    items, imager_ready = orch.list_images()
    # imager_ready stays amd64-only for compatibility with anything already
    # reading it; imager_arches says which architectures can actually netboot.
    # imager_features says which imager.* parameters the built imager
    # understands, so the UI can warn when an assignment needs one it lacks
    # rather than letting the machine image with the setting silently ignored.
    return {"images": items, "imager_ready": imager_ready,
            "imager_arches": orch.imager_arches(),
            "imager_features": orch.imager_features()}


@router.get("/disk")
async def disk(_: str = Depends(require_auth)):
    return orch.disk_usage()


@router.delete("/{name}")
async def delete_image(name: str, _: str = Depends(require_auth)):
    try:
        orch.delete_image(name)
    except FileNotFoundError:
        raise HTTPException(404, "Image not found")
    except ValueError:
        raise HTTPException(400, "Invalid name")
    return {"deleted": name}


@router.get("/{name}/download")
async def download_image(name: str, _: str = Depends(require_auth)):
    if "/" in name or ".." in name:
        raise HTTPException(400, "Invalid name")
    path = os.path.join(settings.output_dir, name)
    if not os.path.isfile(path):
        raise HTTPException(404, "Image not found")
    return FileResponse(path, filename=name, media_type="application/octet-stream")
