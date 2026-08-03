import json
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import orchestrator as orch
from app.jobs import _ProgressEvent as ProgressEvent, jobs
from app.security import create_stream_token, require_auth, verify_stream_token

router = APIRouter(tags=["builds"])

# The 1 MiB BIOS-GRUB partition plus alignment padding, and a floor so the
# overlay partition isn't degenerate.
_BOOT_MIB = 512
_ESP_MIB = 128
_MIN_OVERLAY_MIB = 256
# Mirrors build-image.sh: Ubuntu's linux-image-generic hard-depends on
# linux-firmware and linux-modules-extra, which Debian never installs. The
# builder raises the slot to this floor, so validate against the same number.
_MIN_ROOT_MIB = {"ubuntu": 5120, "debian": 2560}

# Architectures the builder can produce a bootable image for. amd64 keeps the
# hybrid BIOS+UEFI boot; arm64 is UEFI-only. Anything else would build and then
# fail to boot, so it is rejected here rather than discovered on the bench.
_ARCHES = ("amd64", "arm64")


def _require_ready() -> None:
    """Fail a build request up front, with the fix, rather than deep in the log."""
    problems = orch.preflight()
    if problems:
        raise HTTPException(503, " ".join(problems))


def _validate_build(opts: dict) -> None:
    arch = opts.get("arch", "amd64")
    if arch not in _ARCHES:
        raise HTTPException(400, f"arch must be one of {', '.join(_ARCHES)}")
    size = opts.get("image_size", "auto")
    try:
        # 0 / "auto" = smallest possible; the image expands on first boot.
        image_mib = 0 if size in ("auto", 0, "0", "", None) else int(size) * 1024
        root_mib = max(int(opts.get("root_size", 3072)),
                       _MIN_ROOT_MIB.get(opts.get("distro", "debian"), 2560))
    except (TypeError, ValueError):
        raise HTTPException(400, "image_size and root_size must be numbers (or image_size 'auto')")
    if root_mib < 1024:
        raise HTTPException(400, "root_size must be at least 1024 MiB")
    need = 2 * root_mib + _BOOT_MIB + _ESP_MIB + 2 + _MIN_OVERLAY_MIB
    if image_mib and image_mib < need:
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


@router.get("/preflight")
async def preflight(_: str = Depends(require_auth)):
    """Whether the UI can actually drive the builder, and what to fix if not."""
    problems = orch.preflight()
    return {"ready": not problems, "problems": problems,
            "host_project_dir": orch.host_project_dir()}


@router.get("/overlay")
async def overlay(_: str = Depends(require_auth)):
    """Files that will be copied into the next image, and what that means.

    Shown in the UI so the effect of overlay.d is visible before a build rather
    than discovered on a machine afterwards.
    """
    files = orch.overlay_files()
    return {
        "files": files,
        "dir": f"{orch.host_project_dir()}/overlay.d",
        "count": len(files),
    }


@router.post("/builds")
async def start_build(opts: dict = Body(...), _: str = Depends(require_auth)):
    _require_ready()
    _validate_build(opts)
    if jobs.running(type="image"):
        raise HTTPException(409, "An image build is already running")
    # The customization script travels through the output directory, which the
    # builder already mounts. Written before the job starts so a failure to
    # write it is reported here rather than as a confusing build error.
    script = str(opts.get("run_script") or "").strip()
    script_path = os.path.join(orch.settings.output_dir, ".build-script.sh")
    try:
        if script:
            if not script.startswith("#!"):
                script = "#!/bin/bash\nset -euo pipefail\n" + script
            with open(script_path, "w") as f:
                f.write(script if script.endswith("\n") else script + "\n")
            os.chmod(script_path, 0o755)
        elif os.path.exists(script_path):
            os.remove(script_path)
    except OSError as exc:
        raise HTTPException(500, f"could not stage the customization script: {exc}")
    cmd, label, env = orch.build_image_cmd(opts)
    job = await jobs.start(type="image", label=label, cmd=cmd, now=orch.now(), env=env)
    return job.public()


@router.post("/imager/build")
async def start_imager(body: dict = Body(default={}), _: str = Depends(require_auth)):
    _require_ready()
    if jobs.running(type="imager"):
        raise HTTPException(409, "An imager build is already running")
    arch = str(body.get("arch") or "amd64")
    if arch not in _ARCHES:
        raise HTTPException(400, f"arch must be one of {', '.join(_ARCHES)}")
    cmd, label = orch.build_imager_cmd(arch)
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
        async for item in jobs.subscribe(job):
            if isinstance(item, ProgressEvent):
                yield f"event: progress\ndata: {json.dumps(item.data)}\n\n"
            else:
                yield f"data: {item}\n\n"
        yield f"event: end\ndata: {job.status}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
