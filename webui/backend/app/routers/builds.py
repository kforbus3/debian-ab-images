from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import orchestrator as orch
from app.jobs import jobs
from app.security import require_auth

router = APIRouter(tags=["builds"])


@router.post("/builds")
async def start_build(opts: dict = Body(...), _: str = Depends(require_auth)):
    cmd, label = orch.build_image_cmd(opts)
    job = await jobs.start(type="image", label=label, cmd=cmd, now=orch.now())
    return job.public()


@router.post("/imager/build")
async def start_imager(_: str = Depends(require_auth)):
    cmd, label = orch.build_imager_cmd()
    job = await jobs.start(type="imager", label=label, cmd=cmd, now=orch.now())
    return job.public()


@router.get("/jobs")
async def list_jobs(_: str = Depends(require_auth)):
    return jobs.list()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, _: str = Depends(require_auth)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {**job.public(), "log": "\n".join(job.lines)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, _: str = Depends(require_auth)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    await jobs.cancel(job)
    return job.public()


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, token: str = ""):
    # EventSource cannot set Authorization headers, so accept a query token.
    from app.security import require_auth as _ra
    try:
        _ra(token)
    except Exception:
        raise HTTPException(401, "Unauthorized")
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    async def gen():
        async for line in jobs.subscribe(job):
            yield f"data: {line}\n\n"
        yield f"event: end\ndata: {job.status}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
