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
from app import secretstore
from app.deployments import deployments
from app.jobs import jobs
from app.security import Principal, require_operator, require_viewer

router = APIRouter(tags=["updates"])


@router.get("/bundles")
async def list_bundles(_: Principal = Depends(require_viewer)):
    bundles = await run_in_threadpool(orch.list_bundles)
    # What the fleet is actually running, so an operator can tell at a glance
    # whether a bundle has been rolled out or merely built.
    versions: dict[str, int] = {}
    for m in deployments.fleet():
        if m.get("state") == "running" and m.get("version"):
            versions[m["version"]] = versions.get(m["version"], 0) + 1
    return {"bundles": bundles, "running_versions": versions}


@router.post("/bundles/build")
async def build_bundle(body: dict = Body(...), _: Principal = Depends(require_operator)):
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
    # An encrypted image keeps its root slot inside a LUKS container, so the
    # builder needs the passphrase to read it. Only the builder does: the bundle
    # itself carries a plain filesystem and installs on any machine.
    # list_images nests the builder's sidecar under "meta"; the flag lives there.
    row = next((i for i in orch.list_images()[0] if i["name"] == image), {})
    encrypted = bool(row.get("meta", {}).get("encrypted"))
    passphrase = str(body.get("luks_passphrase") or "")
    if encrypted and not passphrase:
        # If the image was built with a generated passphrase, the store already
        # has it filed under this image's name -- so this is the one place the
        # integration pays for itself repeatedly rather than once.
        try:
            passphrase = await run_in_threadpool(secretstore.fetch_passphrase, image) or ""
        except secretstore.SecretStoreError as exc:
            raise HTTPException(502, f"could not read {image}'s passphrase from the "
                                     f"secrets manager: {exc}")
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


@router.delete("/bundles/{name}")
async def delete_bundle(name: str, _: Principal = Depends(require_operator)):
    """Remove a bundle and its sidecars.

    A bundle a machine has already installed is not needed by that machine --
    the update is on its disk, and rollback uses the other slot, not the bundle
    -- so this is safe to do while the fleet runs that version. What it does
    take away is the ability to install that version anywhere else, which the
    UI says before asking.

    Not allowed while a bundle build is running: that build ends by rewriting
    the `latest` pointer, and a deletion repairing the same pointer at the same
    moment is a race whose loser is every unattended machine in the fleet.
    """
    if jobs.running(type="bundle"):
        raise HTTPException(409, "A bundle build is running; wait for it to finish")
    try:
        return await run_in_threadpool(orch.delete_bundle, name)
    except FileNotFoundError:
        raise HTTPException(404, "Bundle not found")
    except ValueError:
        raise HTTPException(400, "Invalid name")
