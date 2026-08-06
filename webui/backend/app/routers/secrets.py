"""Secrets-manager settings, and the passphrases the builder put there.

Configuring a store is optional and changes nothing about how an image boots.
What it changes is who has to remember the LUKS recovery passphrase: with a
store, nobody does. See app/secretstore.py for why that is worth a feature.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app import secretstore as store
from app.security import require_auth

router = APIRouter(tags=["secrets"], prefix="/secrets")


def _safe_image(image: str) -> str:
    """An image name that cannot escape the store's path prefix."""
    name = image.strip()
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, "image must be a filename in the image library")
    return name


@router.get("/config")
async def get_config(_: str = Depends(require_auth)):
    cfg = await run_in_threadpool(store.read_config)
    return {"config": store.public_config(cfg), "configured": store.is_configured(),
            "providers": sorted(set(store.PROVIDERS))}


@router.put("/config")
async def put_config(body: dict = Body(...), _: str = Depends(require_auth)):
    # Enabling a store the app cannot reach would make every encrypted build
    # fail at the point of storing the passphrase, so it is proven here instead.
    # Saving it disabled is always allowed -- that is how you park a
    # half-finished configuration.
    if body.get("enabled"):
        merged = {**(await run_in_threadpool(store.read_config)), **{
            k: v for k, v in body.items()
            if k in store.DEFAULT_CONFIG and not (k in store.SECRET_FIELDS and not v)
        }}
        try:
            await run_in_threadpool(lambda: store.get_store(merged, require_enabled=False).health())
        except store.SecretStoreError as exc:
            raise HTTPException(400, f"cannot enable this store: {exc}")
    try:
        cfg = await run_in_threadpool(store.write_config, body)
    except OSError as exc:
        raise HTTPException(500, f"could not save the store settings: {exc}")
    return {"config": store.public_config(cfg), "configured": store.is_configured()}


@router.post("/test")
async def test(body: dict = Body(default={}), _: str = Depends(require_auth)):
    """Check a configuration -- including one not saved yet.

    Fields left blank fall back to what is saved, so the connection can be
    tested without re-entering a token the UI never showed back.
    """
    saved = await run_in_threadpool(store.read_config)
    merged = {**saved, **{k: v for k, v in body.items()
                          if k in store.DEFAULT_CONFIG
                          and not (k in store.SECRET_FIELDS and not v)}}
    try:
        info = await run_in_threadpool(lambda: store.get_store(merged, require_enabled=False).health())
    except store.SecretStoreError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "info": info}


@router.get("/entries")
async def entries(_: str = Depends(require_auth)):
    """Image names the store holds a passphrase for."""
    if not store.is_configured():
        return {"entries": [], "configured": False}
    try:
        names = await run_in_threadpool(lambda: store.get_store().list())
    except store.SecretStoreError as exc:
        raise HTTPException(502, str(exc))
    return {"entries": names, "configured": True}


@router.get("/passphrase/{image}")
async def passphrase(image: str, _: str = Depends(require_auth)):
    """Reveal an image's stored LUKS passphrase.

    Deliberately reachable from the UI: the moment it is needed is a machine
    stopped at an initramfs prompt, and a recovery key that requires a working
    second system to retrieve is a recovery key with a bad failure mode. It is
    no wider than the session already is -- this UI drives the Docker socket,
    which is root on the host.
    """
    name = _safe_image(image)
    if not store.is_configured():
        raise HTTPException(400, "no secrets manager is configured")
    try:
        value = await run_in_threadpool(store.fetch_passphrase, name)
    except store.SecretStoreError as exc:
        raise HTTPException(502, str(exc))
    if not value:
        raise HTTPException(404, f"the store has no passphrase for {store.secret_name(name)}")
    return {"image": store.secret_name(name), "passphrase": value}
