from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import orchestrator as orch
from app.jobs import jobs
from app.security import create_stream_token, require_auth, verify_stream_token

router = APIRouter(tags=["builds"])

# The 1 MiB BIOS-GRUB partition plus alignment padding, and a floor so the
# overlay partition isn't degenerate.
_BOOT_MIB = 512
_MIN_OVERLAY_MIB = 256


def _validate_build(opts: dict) -> None:
    try:
        image_mib = int(opts.get("image_size", 8)) * 1024
        root_mib = int(opts.get("root_size", 3072))
    except (TypeError, ValueError):
        raise HTTPException(400, "image_size and root_size must be numbers")
    if root_mib < 1024:
        raise HTTPException(400, "root_size must be at least 1024 MiB")
    need = 2 * root_mib + _BOOT_MIB + 2 + _MIN_OVERLAY_MIB
    if image_mib < need:
        raise HTTPException(
            400,
            f"image_size too small: two {root_mib} MiB root slots + boot + overlay "
            f"need at least {need} MiB (≈{-(-need // 1024)} GiB)",
        )
    if opts.get("encrypt"):
        if not opts.get("luks_passphrase"):
            raise HTTPException(400, "encrypt requires a LUKS passphrase")
        if opts.get("unlock") == "tang" and not opts.get("tang_url"):
            raise HTTPException(400, "unlock=tang requires a Tang URL")


@router.post("/builds")
async def start_build(opts: dict = Body(...), _: str = Depends(require_auth)):
    _validate_build(opts)
    if jobs.running(type="image"):
        raise HTTPException(409, "An image build is already running")
    cmd, label, env = orch.build_image_cmd(opts)
    job = await jobs.start(type="image", label=label, cmd=cmd, now=orch.now(), env=env)
    return job.public()


@router.post("/imager/build")
async def start_imager(_: str = Depends(require_auth)):
    if jobs.running(type="imager"):
        raise HTTPException(409, "An imager build is already running")
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
    return {**job.public(), "log": jobs.log_text(job)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, _: str = Depends(require_auth)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    await jobs.cancel(job)
    return job.public()


@router.get("/jobs/{job_id}/stream-token")
async def stream_token(job_id: str, _: str = Depends(require_auth)):
    if not jobs.get(job_id):
        raise HTTPException(404, "Job not found")
    return {"token": create_stream_token(job_id)}


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, token: str = ""):
    # EventSource cannot set Authorization headers; a short-lived scoped token
    # (from /stream-token) authorizes exactly this job's stream.
    verify_stream_token(token, job_id)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    async def gen():
        async for line in jobs.subscribe(job):
            yield f"data: {line}\n\n"
        yield f"event: end\ndata: {job.status}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
