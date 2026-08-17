from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app import orchestrator as orch
from app.security import Principal, require_admin, require_operator, require_viewer

router = APIRouter(prefix="/server", tags=["server"])


@router.get("/config")
async def get_config(_: Principal = Depends(require_viewer)):
    return orch.read_env()


@router.put("/config")
async def put_config(cfg: dict = Body(...), _: Principal = Depends(require_admin)):
    orch.write_env(cfg)
    return orch.read_env()


@router.get("/assignments")
async def get_assignments(_: Principal = Depends(require_viewer)):
    """Per-machine image targeting: MAC -> image."""
    return await run_in_threadpool(orch.read_assignments)


@router.put("/assignments")
async def put_assignments(items: list = Body(...), _: Principal = Depends(require_operator)):
    try:
        return await run_in_threadpool(orch.write_assignments, items)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/interfaces")
async def interfaces(_: Principal = Depends(require_viewer)):
    """Host NICs to choose a provisioning network from, plus a proposed static
    address for one that has none (the usual case for a dedicated NIC)."""
    ifaces = await run_in_threadpool(orch.list_interfaces)
    suggestion = await run_in_threadpool(orch.suggest_provisioning_net, ifaces)
    return {"interfaces": ifaces, "suggestion": suggestion}


@router.get("/preflight")
async def preflight(_: Principal = Depends(require_viewer)):
    problems = await run_in_threadpool(orch.provisioning_preflight)
    return {"ready": not problems, "problems": problems}


@router.get("/status")
async def status(_: Principal = Depends(require_viewer)):
    return await run_in_threadpool(orch.server_status)


@router.post("/up")
async def up(_: Principal = Depends(require_operator)):
    # Refuse rather than start a server that would leave machines PXE-booting
    # into nothing — or, worse, serve DHCP without a confining interface.
    problems = await run_in_threadpool(orch.provisioning_preflight)
    if problems:
        raise HTTPException(503, " ".join(problems))
    return {"message": await run_in_threadpool(orch.server_up)}


@router.post("/down")
async def down(_: Principal = Depends(require_operator)):
    return {"message": await run_in_threadpool(orch.server_down)}


@router.get("/clients")
async def clients(_: Principal = Depends(require_viewer)):
    return await run_in_threadpool(orch.server_clients)
