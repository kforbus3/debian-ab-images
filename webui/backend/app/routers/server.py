from fastapi import APIRouter, Body, Depends
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


@router.get("/status")
async def status(_: str = Depends(require_auth)):
    return await run_in_threadpool(orch.server_status)


@router.post("/up")
async def up(_: str = Depends(require_auth)):
    return {"message": await run_in_threadpool(orch.server_up)}


@router.post("/down")
async def down(_: str = Depends(require_auth)):
    return {"message": await run_in_threadpool(orch.server_down)}


@router.get("/clients")
async def clients(_: str = Depends(require_auth)):
    return await run_in_threadpool(orch.server_clients)
