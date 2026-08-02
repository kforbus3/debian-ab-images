"""Endpoints for machines being imaged, and for the page that watches them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..imaging import registry
from ..security import require_auth

router = APIRouter()


@router.post("/imaging/report")
async def report(request: Request):
    """Receive one progress report from an imager.

    Deliberately unauthenticated. The imager runs from a netboot initramfs with
    no credentials and no way to obtain any -- it is on the provisioning network
    precisely because it has not been provisioned yet. The endpoint accepts only
    progress text, stores it in memory, and expires it on its own, so the worst
    a stranger on that network can do is add a row that disappears again. The
    provisioning network is the trust boundary here, as it already is for DHCP
    and TFTP.
    """
    form = dict(await request.form())
    ident = str(form.get("id") or "").strip()
    if not ident:
        return {"ok": False, "error": "id is required"}

    client = request.client.host if request.client else ""
    row = registry.report(
        ident=ident,
        phase=str(form.get("phase") or "booted"),
        percent=form.get("percent"),
        detail=str(form.get("detail") or ""),
        disk=str(form.get("disk") or ""),
        url=str(form.get("url") or ""),
        address=client,
    )
    return {"ok": True, "phase": row["phase"], "percent": row["percent"]}


@router.get("/imaging")
async def list_imaging(_: str = Depends(require_auth)):
    """Machines imaging right now, plus those that just finished."""
    rows = registry.active()
    return {
        "machines": rows,
        "active": sum(1 for r in rows if r["state"] in ("active", "stalled")),
    }


@router.delete("/imaging/{ident}")
async def forget(ident: str, _: str = Depends(require_auth)):
    """Drop a row by hand, for a machine that will never report again."""
    return {"ok": registry.forget(ident)}
