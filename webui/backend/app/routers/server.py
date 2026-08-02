from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app import orchestrator as orch
from app.security import require_auth

router = APIRouter(prefix="/server", tags=["server"])


@router.get("/config")
async def get_config(_: str = Depends(require_auth)):
    return orch.read_env()


@router.put("/config")
async def put_config(cfg: dict = Body(...), _: str = Depends(require_auth)):
    orch.write_env(cfg)
    return orch.read_env()


@router.get("/assignments")
async def get_assignments(_: str = Depends(require_auth)):
    """Per-machine image targeting: MAC -> image."""
    return await run_in_threadpool(orch.read_assignments)


@router.put("/assignments")
async def put_assignments(items: list = Body(...), _: str = Depends(require_auth)):
    try:
        return await run_in_threadpool(orch.write_assignments, items)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/interfaces")
async def interfaces(_: str = Depends(require_auth)):
    """Host NICs to choose a provisioning network from."""
    return await run_in_threadpool(orch.list_interfaces)


@router.get("/preflight")
async def preflight(_: str = Depends(require_auth)):
    problems = await run_in_threadpool(orch.provisioning_preflight)
    return {"ready": not problems, "problems": problems}


@router.get("/status")
async def status(_: str = Depends(require_auth)):
    return await run_in_threadpool(orch.server_status)


@router.post("/up")
async def up(_: str = Depends(require_auth)):
    # Refuse rather than start a server that would leave machines PXE-booting
    # into nothing — or, worse, serve DHCP without a confining interface.
    problems = await run_in_threadpool(orch.provisioning_preflight)
    if problems:
        raise HTTPException(503, " ".join(problems))
    return {"message": await run_in_threadpool(orch.server_up)}


@router.post("/down")
async def down(_: str = Depends(require_auth)):
    return {"message": await run_in_threadpool(orch.server_down)}


@router.get("/clients")
async def clients(_: str = Depends(require_auth)):
    return await run_in_threadpool(orch.server_clients)
