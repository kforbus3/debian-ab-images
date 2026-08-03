"""RAUC update bundles: build them, list them, and see what the fleet is running.

Imaging a machine writes its whole disk. Updating one writes only the slot it is
not running on, then reboots into it -- and falls back on its own if that does
not come up. This is the endpoint side of that: without it the only way to patch
a fleet is to re-image it, which is exactly what the A/B layout exists to avoid.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app import orchestrator as orch
from app.deployments import deployments
from app.jobs import jobs
from app.security import require_auth

router = APIRouter(tags=["updates"])


@router.get("/bundles")
async def list_bundles(_: str = Depends(require_auth)):
    bundles = await run_in_threadpool(orch.list_bundles)
    # What the fleet is actually running, so an operator can tell at a glance
    # whether a bundle has been rolled out or merely built.
    versions: dict[str, int] = {}
    for m in deployments.fleet():
        if m.get("state") == "running" and m.get("version"):
            versions[m["version"]] = versions.get(m["version"], 0) + 1
    return {"bundles": bundles, "running_versions": versions}


@router.post("/bundles/build")
async def build_bundle(body: dict = Body(...), _: str = Depends(require_auth)):
    image = str(body.get("image") or "").strip()
    if not image:
        raise HTTPException(400, "image is required")
    # The image name is interpolated into a container path, so it must be a
    # plain filename from the library rather than anything with a path in it.
    if "/" in image or ".." in image:
        raise HTTPException(400, "image must be a filename in the image library")
    known = {i["name"] for i in orch.list_images()[0]}
    if image not in known:
        raise HTTPException(404, f"no such image: {image}")
    if image.endswith((".zst", ".gz")):
        raise HTTPException(
            400,
            "Bundles are built from an uncompressed .img. Rebuild that image with "
            "compression set to none, or decompress it first.",
        )
    # An encrypted image keeps its root slot inside a LUKS container, so the
    # builder needs the passphrase to read it. Only the builder does: the bundle
    # itself carries a plain filesystem and installs on any machine.
    # list_images nests the builder's sidecar under "meta"; the flag lives there.
    row = next((i for i in orch.list_images()[0] if i["name"] == image), {})
    encrypted = bool(row.get("meta", {}).get("encrypted"))
    passphrase = str(body.get("luks_passphrase") or "")
    if encrypted and not passphrase:
        raise HTTPException(400, f"{image} is encrypted; its LUKS passphrase is needed "
                                 "to read the root slot. It is used only while building "
                                 "and is not stored in the bundle.")
    if jobs.running(type="bundle"):
        raise HTTPException(409, "A bundle build is already running")

    cmd, label, env = orch.build_bundle_cmd(
        image,
        version=str(body.get("version") or ""),
        description=str(body.get("description") or ""),
        encrypted=encrypted,
    )
    if encrypted:
        env["LUKS_PASS"] = passphrase
    job = await jobs.start(type="bundle", label=label, cmd=cmd, now=orch.now(), env=env)
    return job.public()
