"""Endpoints for machines being imaged, and for the page that watches them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deployments import deployments
from ..imaging import registry
from ..security import Principal, require_operator, require_viewer

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
    phase = str(form.get("phase") or "booted")
    row = registry.report(
        ident=ident,
        phase=phase,
        percent=form.get("percent"),
        detail=str(form.get("detail") or ""),
        disk=str(form.get("disk") or ""),
        url=str(form.get("url") or ""),
        address=client,
    )
    # The live view expires by design; a finished deployment is worth keeping.
    if phase == "done":
        deployments.record("imaged", ident, image=row.get("image", ""),
                           disk=row.get("disk", ""), address=client)
    return {"ok": True, "phase": row["phase"], "percent": row["percent"]}


@router.post("/imaging/checkin")
async def checkin(request: Request):
    """A machine reporting that it booted the image it was given.

    Unauthenticated for the same reason as /imaging/report: this arrives from a
    machine that has just been provisioned and holds no credentials. It records
    an observation, nothing acts on it, and the provisioning network is already
    the trust boundary for DHCP and TFTP.

    Without this, "imaged" is the last thing ever heard from a machine -- and it
    is sent before the reboot, so a machine that images perfectly and then fails
    to boot looks exactly like a success.
    """
    form = dict(await request.form())
    ident = str(form.get("id") or "").strip()
    if not ident:
        return {"ok": False, "error": "id is required"}
    client = request.client.host if request.client else ""
    deployments.record(
        "booted", ident,
        hostname=str(form.get("hostname") or ""),
        slot=str(form.get("slot") or ""),
        version=str(form.get("version") or ""),
        address=client,
    )
    return {"ok": True}


@router.get("/deployments")
async def list_deployments(_: Principal = Depends(require_viewer)):
    """Every machine this server has imaged, and whether it came back."""
    rows = deployments.fleet()
    return {
        "machines": rows,
        "running": sum(1 for r in rows if r["state"] == "running"),
        "never_booted": sum(1 for r in rows if r["state"] == "never-booted"),
    }


@router.get("/imaging")
async def list_imaging(_: Principal = Depends(require_viewer)):
    """Machines imaging right now, plus those that just finished."""
    rows = registry.active()
    return {
        "machines": rows,
        "active": sum(1 for r in rows if r["state"] in ("active", "stalled")),
    }


@router.delete("/imaging/{ident}")
async def forget(ident: str, _: Principal = Depends(require_operator)):
    """Drop a row by hand, for a machine that will never report again."""
    return {"ok": registry.forget(ident)}
